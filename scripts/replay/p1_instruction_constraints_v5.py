#!/usr/bin/env python3
"""One answer-independent grammar for original and explicit instructions."""
import hashlib
import json
import re

KINDS = ('extract','check','organize')
CATEGORIES = ('interactive','background')
_IDS = r'(R[1-9]\d*(?:,R[1-9]\d*)*)'
_COMMANDS = {
    'extract':(r'提取'+_IDS+r'。每行ID\|值。',
        r'提取'+_IDS+r'对应的数值，不要复制编号作值。每行ID\|值。'),
    'check':(r'核对'+_IDS+r'：同写一致，异写不一致。每行ID\|结论。',
        r'逐项比较A和B中'+_IDS+r'的数值：相等写一致，否则写不一致。每行ID\|结论。'),
    'organize':(r'按'+_IDS+r'顺序整理。每行ID\|值。',
        r'只按'+_IDS+r'顺序抄对应数值，不要抄其他行。每行ID\|值。')}
_USER_START = '<|im_start|>user\n'
_END = '<|im_end|>\n<|im_start|>assistant\n'


def parse_public_task(prompt, category):
    if category not in CATEGORIES or not isinstance(prompt,str):
        raise ValueError('Registered category and text prompt required')
    if prompt.count(_USER_START) != 1 or not prompt.endswith(_END):
        raise ValueError('Expected one complete explicit ChatML user task')
    user = prompt.split(_USER_START,1)[1][:-len(_END)]
    command = user.rsplit('\n',1)[-1]
    matches = [(kind,re.fullmatch(pattern,command)) for kind,patterns in _COMMANDS.items() for pattern in patterns]
    matches = [(kind,match) for kind,match in matches if match is not None]
    if len(matches) != 1:
        raise ValueError('Unregistered final task instruction')
    kind,match = matches[0]
    targets = match.group(1).split(',')
    count = 6 if category == 'background' else 2 if kind == 'organize' else 1
    if len(targets) != count or len(set(targets)) != len(targets):
        raise ValueError('Target count or uniqueness differs from registered task')
    return {'task_kind':kind,'target_ids':targets}


def grammar_from_prompt(prompt, category):
    public = parse_public_task(prompt,category)
    quote = lambda value:json.dumps(value,ensure_ascii=False)
    sequence = []
    for index,identity in enumerate(public['target_ids']):
        if index: sequence.append(quote('\n'))
        sequence.extend((quote(identity+'|'),'value'))
    rule = '"一致" | "不一致"' if public['task_kind'] == 'check' else '[1-9] [0-9]'
    return 'root ::= '+' '.join(sequence)+'\nvalue ::= '+rule+'\n'


def constraint_from_prompt(prompt, category):
    public = parse_public_task(prompt,category)
    grammar = grammar_from_prompt(prompt,category)
    return {**public,'grammar':grammar,'grammar_sha256':hashlib.sha256(grammar.encode('utf-8')).hexdigest(),
        'constraint_source':'public_final_task_instruction_only',
        'value_domain':['一致','不一致'] if public['task_kind'] == 'check' else 'all integers 10..99'}
