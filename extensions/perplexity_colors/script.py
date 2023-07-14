import gradio
import torch
from transformers import LogitsProcessor
import numpy as np

from modules import shared

params = {
    'active': True,
    'color_by_perplexity': False,
    'color_by_probability': False,
    'ppl_scale': 15.0, # No slider for this right now, because I don't think it really needs to be changed. Very large perplexity scores don't show up often.
    'probability_dropdown': False
}

class PerplexityLogits(LogitsProcessor):
    def __init__(self, verbose=False):
        self.generated_token_ids = []
        self.selected_probs = []
        self.top_token_ids_list = []
        self.top_probs_list = []
        self.perplexities_list = []
        self.last_probs = None
        self.verbose = verbose

    def __call__(self, input_ids, scores):
        probs = torch.softmax(scores, dim=-1, dtype=torch.float)
        log_probs = torch.nan_to_num(torch.log(probs))
        entropy = -torch.sum(probs*log_probs)
        entropy = entropy.cpu().numpy()
        perplexity = round(float(np.exp(entropy)), 4)
        self.perplexities_list.append(perplexity)
        last_token_id = int(input_ids[0][-1].cpu().numpy().item())
        # Store the generated tokens (not sure why this isn't accessible in the output endpoint!)
        self.generated_token_ids.append(last_token_id)
        # Get last probability, and add to the list if it wasn't there
        if len(self.selected_probs) > 0:
            # Is the selected token in the top tokens?
            if self.verbose:
                print('Probs: Token after', shared.tokenizer.decode(last_token_id))
                print('Probs:', [shared.tokenizer.decode(token_id) for token_id in self.top_token_ids_list[-1][0]])
                print('Probs:', [round(float(prob), 4) for prob in self.top_probs_list[-1][0]])
            if last_token_id in self.top_token_ids_list[-1]:
                idx = self.top_token_ids_list[-1].index(last_token_id)
                self.selected_probs.append(self.top_probs_list[-1][idx])
            else:
                self.top_token_ids_list[-1].append(last_token_id)
                last_prob = round(float(self.last_probs[last_token_id]), 4)
                self.top_probs_list[-1].append(last_prob)
                self.selected_probs.append(last_prob)
        else:
            self.selected_probs.append(1.0) # Placeholder for the last token of the prompt

        if self.verbose:
            pplbar = "-"
            if not np.isnan(perplexity):
                pplbar = "*"*round(perplexity)
            print(f"PPL: Token after {shared.tokenizer.decode(last_token_id)}\t{perplexity:.2f}\t{pplbar}")

        # Get top 5 probabilities
        top_tokens_and_probs = torch.topk(probs, 5)
        top_probs = top_tokens_and_probs.values.cpu().numpy().astype(float).tolist()
        top_token_ids = top_tokens_and_probs.indices.cpu().numpy().astype(int).tolist()

        self.top_token_ids_list.append(top_token_ids)
        self.top_probs_list.append(top_probs)
        
        probs = probs.cpu().numpy().flatten()
        self.last_probs = probs # Need to keep this as a reference for top probs

        # Doesn't actually modify the logits!
        return scores

# Stores the perplexity and top probabilities
ppl_logits_processor = None

def logits_processor_modifier(logits_processor_list, input_ids):
    global ppl_logits_processor
    if params['active']:
        ppl_logits_processor = PerplexityLogits(verbose=False)
        logits_processor_list.append(ppl_logits_processor)

def output_modifier(text):
    global ppl_logits_processor

    if not params['active']:
        return text

    # TODO: It's probably more efficient to do this above rather than modifying all these lists
    # Remove last element of perplexities_list, top_token_ids_list, top_tokens_list, top_probs_list since everything is off by one because this extension runs before generation
    perplexities = ppl_logits_processor.perplexities_list[:-1]
    top_token_ids_list = ppl_logits_processor.top_token_ids_list[:-1]
    top_tokens_list = [[shared.tokenizer.decode(token_id) for token_id in top_token_ids[0]] for top_token_ids in top_token_ids_list]
    top_probs_list = ppl_logits_processor.top_probs_list[:-1]
    # Remove first element of generated_token_ids, generated_tokens, selected_probs because they are for the last token of the prompt
    gen_token_ids = ppl_logits_processor.generated_token_ids[1:]
    gen_tokens = [shared.tokenizer.decode(token_id) for token_id in gen_token_ids]
    sel_probs = ppl_logits_processor.selected_probs[1:]

    end_part = '</div></div>' if params['probability_dropdown'] else '</span>' # Helps with finding the index after replacing part of the text.
    
    in_code = False # Since the <span> tags mess up code blocks, avoid coloring while inside a code block, based on finding tokens with '`' in them

    if params['color_by_probability'] and params['color_by_perplexity']:
        i = 0
        for token, prob, ppl, top_tokens, top_probs in zip(gen_tokens, sel_probs, perplexities, top_tokens_list, top_probs_list):
            if '`' in token:
                in_code = not in_code
                continue
            if in_code:
                continue
            color = probability_perplexity_color_scale(prob, ppl)
            if token in text[i:]:
                if params['probability_dropdown']:
                    text = text[:i] + text[i:].replace(token, add_dropdown_html(token, color, top_tokens, top_probs[0]), 1)
                else:
                    text = text[:i] + text[i:].replace(token, add_color_html(token, color), 1)
                i += text[i:].find(end_part) + len(end_part)
    elif params['color_by_perplexity']:
        i = 0
        for token, ppl, top_tokens, top_probs in zip(gen_tokens, perplexities, top_tokens_list, top_probs_list):
            if '`' in token:
                in_code = not in_code
                continue
            if in_code:
                continue
            color = perplexity_color_scale(ppl)
            if token in text[i:]:
                if params['probability_dropdown']:
                    text = text[:i] + text[i:].replace(token, add_dropdown_html(token, color, top_tokens, top_probs[0]), 1)
                else:
                    text = text[:i] + text[i:].replace(token, add_color_html(token, color), 1)
                i += text[i:].find(end_part) + len(end_part)
    elif params['color_by_probability']:
        i = 0
        for token, prob, top_tokens, top_probs in zip(gen_tokens, sel_probs, top_tokens_list, top_probs_list):
            if '`' in token:
                in_code = not in_code
                continue
            if in_code:
                continue
            color = probability_color_scale(prob)
            if token in text[i:]:
                if params['probability_dropdown']:
                    text = text[:i] + text[i:].replace(token, add_dropdown_html(token, color, top_tokens, top_probs[0]), 1)
                else:
                    text = text[:i] + text[i:].replace(token, add_color_html(token, color), 1)
                i += text[i:].find(end_part) + len(end_part)
    elif params['probability_dropdown']:
        i = 0
        for token, prob, top_tokens, top_probs in zip(gen_tokens, sel_probs, top_tokens_list, top_probs_list):
            if '`' in token:
                in_code = not in_code
                continue
            if in_code:
                continue
            color = 'ffffff'
            if token in text[i:]:
                text = text[:i] + text[i:].replace(token, add_dropdown_html(token, color, top_tokens, top_probs[0]), 1)
                i += text[i:].find(end_part) + len(end_part)

    print('Average perplexity:', round(np.mean(perplexities), 4))
    # Optional hacky workaround: Without this, spaces get added between every token. With this, there is a little extra whitespace at the top.
    # This fixes the tokenization spaces, somehow. However, this also removes any paragraph breaks in the message.
    # return '<p>' + text + '</p>'
    return text

# Green-yellow-red color scale
def probability_color_scale(prob):
    rv = 0
    gv = 0
    if prob <= 0.5:
        rv = 'ff'
        gv = hex(int(255*prob*2))[2:]
        if len(gv) < 2:
            gv = '0'*(2 - len(gv)) + gv
    else:
        rv = hex(int(255 - 255*(prob - 0.5)*2))[2:]
        gv = 'ff'
        if len(rv) < 2:
            rv = '0'*(2 - len(rv)) + rv
    return rv + gv + '00'

# Red component only, white for 0 perplexity (sorry if you're not in dark mode)
def perplexity_color_scale(ppl):
    value = hex(max(int(255.0 - params['ppl_scale']*(float(ppl)-1.0)), 0))[2:]
    if len(value) < 2:
        value = '0'*(2 - len(value)) + value
    return 'ff' + value + value

# Green-yellow-red for probability and blue component for perplexity
def probability_perplexity_color_scale(prob, ppl):
    rv = 0
    gv = 0
    bv = hex(min(max(int(params['ppl_scale']*(float(ppl)-1.0)), 0), 255))[2:]
    if len(bv) < 2:
            bv = '0'*(2 - len(bv)) + bv
    if prob <= 0.5:
        rv = 'ff'
        gv = hex(int(255*prob*2))[2:]
        if len(gv) < 2:
            gv = '0'*(2 - len(gv)) + gv
    else:
        rv = hex(int(255 - 255*(prob - 0.5)*2))[2:]
        gv = 'ff'
        if len(rv) < 2:
            rv = '0'*(2 - len(rv)) + rv
    return rv + gv + bv

def add_color_html(token, color):
    return f'<span style="color: #{color}">{token}</span>'

def add_dropdown_html(token, color, top_tokens, top_probs):
    html = f'<div class="hoverable" style="color: #{color}">{token}<div class="dropdown"><table class="dropdown-content"><tbody>'
    for token_option, prob in zip(top_tokens, top_probs):
        # TODO: Bold for selected token?
        # Using divs prevented the problem of divs inside spans causing issues.
        # Now the problem is that divs show the same whitespace of one space between every token.
        # There is probably some way to fix this in CSS that I don't know about.
        row_color = probability_color_scale(prob)
        html += f'<tr><td style="color: #{row_color}">{token_option}</td><td style="color: #{row_color}">{prob:.4f}</td></tr>'
    html += '</tbody></table></div></div>'
    return html

def custom_css():
    return """
        .dropdown {
            display: none;
            position: absolute;
            z-index: 50; 
            background-color: #374151;
            box-shadow: 0px 8px 16px 0px rgba(0,0,0,0.2);
            width: max-content;
            overflow: visible;
            padding: 5px;
            border-radius: 10px;
            border: 1px solid #4b5563;
        }
        
        .dropdown-content {
            border: none;
            z-index: 50;
        }
        
        .hoverable {
            position: relative;
            display: inline-block;
            overflow: visible;
            font-size: 15px;
            margin: 0;
            padding: 0;
        }
        
        .hoverable:hover .dropdown {
            display: block;
        }
        
        .chat {
            overflow-y: visible;
        }
    """

def ui():
    active_check = gradio.Checkbox(value=True, label="Compute probabilities and perplexity scores", info="Activate this extension. Note that this extension currently does not work with exllama or llama.cpp.")
    def update_active_check(x):
        params.update({'active': x})
    active_check.change(update_active_check, active_check, None)

    color_by_ppl_check = gradio.Checkbox(value=False, label="Color by perplexity", info="Higher perplexity is more red. If also showing probability, higher perplexity has more blue component.")
    def update_color_by_ppl_check(x):
        params.update({'color_by_perplexity': x})
    color_by_ppl_check.change(update_color_by_ppl_check, color_by_ppl_check, None)

    color_by_prob_check = gradio.Checkbox(value=False, label="Color by probability", info="Green-yellow-red linear scale, with 100% green, 50% yellow, 0% red.")
    def update_color_by_prob_check(x):
        params.update({'color_by_probability': x})
    color_by_prob_check.change(update_color_by_prob_check, color_by_prob_check, None)

    prob_dropdown_check = gradio.Checkbox(value=False, label="Probability dropdown", info="Hover over a token to show a dropdown of top token probabilities.")
    def update_prob_dropdown_check(x):
        params.update({'probability_dropdown': x})
    prob_dropdown_check.change(update_prob_dropdown_check, prob_dropdown_check, None)
