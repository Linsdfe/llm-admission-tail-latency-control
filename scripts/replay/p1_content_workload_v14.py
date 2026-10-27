"""Content-dependent selection with an independent fact oracle and public parser."""
import hashlib
import itertools
import json
from pathlib import Path
import random
import re

ROOT = Path(__file__).resolve().parents[2]
UP = ('服务可用', '已恢复服务', '能够正常响应')
DOWN = ('服务不可用', '尚未恢复服务', '无法正常响应')


def config():
    return json.loads((ROOT / 'configs/p1-content-selection-profile-v14.json').read_text(encoding='utf-8'))


def digest(text): return hashlib.sha256(text.encode('utf-8')).hexdigest()


def descriptors(cfg=None):
    cfg = cfg or config()
    result = []
    for seed_index, seed in enumerate(cfg['allowed_seeds']):
        for category_index, category in enumerate(('interactive', 'background')):
            for index, count in enumerate(cfg['answer_counts'][category]):
                global_index = seed_index * 24 + category_index * 12 + index
                result.append({'req_id': f'v14-dev-{seed}-{category[0]}{index:02}', 'split': 'development',
                               'seed': seed, 'category': category, 'task_kind': 'select_current',
                               'source_id': 'project_original_content_selection_v14',
                               'source_index': global_index, 'record_count': cfg['records'][category],
                               'target_count_evaluation_only': count, 'nonce': 0})
    return result


def materialize(descriptor):
    """The oracle uses independent latent state, never the public parser."""
    cfg = config()
    if descriptor['split'] != 'development' or descriptor['seed'] not in cfg['allowed_seeds']:
        raise ValueError('only registered development seeds')
    rng = random.Random(f"{descriptor['req_id']}:nonce{descriptor['nonce']}")
    n = descriptor['record_count']
    ids = [f"R{4100 + descriptor['source_index'] * 10 + i}" for i in range(n)]
    selected = set(rng.sample(ids, descriptor['target_count_evaluation_only']))
    facts, lines = [], []
    for req in ids:
        previous_down = rng.choice((False, True))
        current_down = req in selected
        early = rng.choice(DOWN if previous_down else UP)
        late = rng.choice(DOWN if current_down else UP)
        events = [('09:10', early), ('09:40', late)]
        rng.shuffle(events)
        lines.append(req + '：' + '；'.join(t + ' ' + s for t, s in events))
        facts.append({'id': req, 'previous_down': previous_down, 'current_down': current_down,
                      'events_in_written_order': events})
    rng.shuffle(lines)
    instruction = ('每条工单有两条状态记录，只按时间较晚的记录判断，不按书写顺序判断。'
                   '找出仍不可用的工单，编号升序，每行只写一个编号；没有就写无。不要解释。')
    prompt = ('<|im_start|>system\n只按资料筛选，不补信息。<|im_end|>\n'
              '<|im_start|>user\n' + '\n'.join(lines) + '\n' + instruction +
              '<|im_end|>\n<|im_start|>assistant\n')
    expected = sorted(selected)
    return {**descriptor, 'prompt': prompt, 'content_sha256': digest(prompt),
            'input_token_range': cfg['input_ranges'][descriptor['category']],
            'max_tokens': cfg['max_tokens'][descriptor['category']], 'arrival_offset_ms': 0,
            'temperature': 0, 'ignore_eos': False, 'cache_prompt': False,
            'facts_evaluation_only': facts, 'expected_ids': expected,
            'expected_answer': '\n'.join(expected) if expected else '无'}


def public_ids(prompt):
    ids = re.findall(r'(?m)^(R[0-9]{4})：', prompt)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('unique public record IDs required')
    return sorted(ids)


def parse_public(prompt):
    """A strong rule reading only the actual visible document."""
    result = []
    records = re.findall(r'(?m)^(R[0-9]{4})：([^\n]+)$', prompt)
    if len(records) != len(public_ids(prompt)):
        raise ValueError('unparsed public record')
    for req, body in records:
        events = re.findall(r'(\d{2}):(\d{2}) ([^；\n]+)', body)
        if len(events) != 2:
            raise ValueError('two timestamped records required')
        ranked = [(int(h) * 60 + int(m), text) for h, m, text in events]
        if ranked[0][0] == ranked[1][0]:
            raise ValueError('ambiguous equal timestamps')
        last = max(ranked)[1]
        if last in DOWN:
            result.append(req)
        elif last not in UP:
            raise ValueError('unknown public phrase')
    return sorted(result)


def alternatives(prompt):
    ids = public_ids(prompt)
    return ['无'] + ['\n'.join(group) for n in range(1, len(ids) + 1) for group in itertools.combinations(ids, n)]


def grammar(prompt):
    return 'root ::= ' + ' | '.join(json.dumps(text, ensure_ascii=False) for text in alternatives(prompt)) + '\n'


def public_row(full):
    keys = ['req_id', 'split', 'seed', 'category', 'task_kind', 'source_id', 'prompt', 'input_tokens',
            'input_token_range', 'max_tokens', 'arrival_offset_ms', 'temperature', 'ignore_eos',
            'cache_prompt', 'content_sha256', 'token_ids_sha256', 'grammar']
    return {key: full[key] for key in keys}


def quality(full, actual):
    answer = actual.get('content', '')
    lines = answer.splitlines()
    observed = [] if answer == '无' else lines
    expected = full['expected_ids']
    return {'expected_ids': expected, 'observed_ids': observed,
            'whole_correct': actual['terminal'] == 'completed' and answer == full['expected_answer'],
            'missing': sorted(set(expected) - set(observed)),
            'extra': sorted(set(observed) - set(expected)),
            'order_or_format_wrong': set(expected) == set(observed) and answer != full['expected_answer']}
