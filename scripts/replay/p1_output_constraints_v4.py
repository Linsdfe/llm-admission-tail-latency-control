#!/usr/bin/env python3
"""Output syntax derived only from the public v3 task instruction.

No source-value or expected-answer argument is accepted. Numeric values retain
the entire registered 10..99 domain; checking retains both legal labels.
"""
import hashlib
import json
import re

KINDS = ('extract', 'check', 'organize')
CATEGORIES = ('interactive', 'background')
_IDS = r'(R[1-9]\d*(?:,R[1-9]\d*)*)'
_COMMANDS = {
    'extract': re.compile(r'提取' + _IDS + r'。每行ID\|值。'),
    'check': re.compile(r'核对' + _IDS + r'：同写一致，异写不一致。每行ID\|结论。'),
    'organize': re.compile(r'按' + _IDS + r'顺序整理。每行ID\|值。'),
}
_USER_START = '<|im_start|>user\n'
_END = '<|im_end|>\n<|im_start|>assistant\n'


def parse_public_task(prompt, category):
    """Read only the final instruction and its ordered public target IDs."""
    if category not in CATEGORIES or not isinstance(prompt, str):
        raise ValueError('Registered category and text prompt required')
    if prompt.count(_USER_START) != 1 or not prompt.endswith(_END):
        raise ValueError('Expected one complete explicit ChatML user task')
    user = prompt.split(_USER_START, 1)[1][:-len(_END)]
    command = user.rsplit('\n', 1)[-1]
    matches = [(kind, pattern.fullmatch(command)) for kind, pattern in _COMMANDS.items()]
    matches = [(kind, match) for kind, match in matches if match is not None]
    if len(matches) != 1:
        raise ValueError('Unregistered final task instruction')
    kind, match = matches[0]
    targets = match.group(1).split(',')
    expected_count = 6 if category == 'background' else (2 if kind == 'organize' else 1)
    if len(targets) != expected_count or len(set(targets)) != len(targets):
        raise ValueError('Target count or uniqueness differs from registered task')
    return {'task_kind': kind, 'target_ids': targets}


def grammar_from_prompt(prompt, category):
    """Constrain ID/order/count only; never constrain a value to its answer."""
    public = parse_public_task(prompt, category)
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    sequence = []
    for index, identity in enumerate(public['target_ids']):
        if index:
            sequence.append(quote('\n'))
        sequence.extend((quote(identity + '|'), 'value'))
    value_rule = ('"一致" | "不一致"' if public['task_kind'] == 'check'
                  else '[1-9] [0-9]')
    return 'root ::= ' + ' '.join(sequence) + '\nvalue ::= ' + value_rule + '\n'


def constraint_from_prompt(prompt, category):
    public = parse_public_task(prompt, category)
    grammar = grammar_from_prompt(prompt, category)
    return {**public, 'grammar': grammar,
        'grammar_sha256': hashlib.sha256(grammar.encode('utf-8')).hexdigest(),
        'constraint_source': 'public_final_task_instruction_only',
        'value_domain': ['一致', '不一致'] if public['task_kind'] == 'check' else 'all integers 10..99'}
