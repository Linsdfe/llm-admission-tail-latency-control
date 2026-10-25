#!/usr/bin/env python3
"""Deterministic extraction/checking/ordering pilot with evaluation-only answers.

The renderer never reads a completion. Input lengths use actual tokenizer IDs;
only extra records of the same schema may be added to reach a registered bucket.
Expected lines and source structures are evaluation metadata, not HTTP fields.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / 'configs/p1-output-constraints-v4.json'
KINDS = ('extract', 'check', 'organize')
CATEGORIES = ('interactive', 'background')


def load_config(path=DEFAULT_CONFIG):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def build_descriptors(config=None, split='development', seed=24001):
    cfg = config or load_config()
    if split != 'development' or cfg['allowed_splits'] != ['development']:
        raise ValueError('Only the registered development pilot is allowed; final and calibration are disabled')
    if type(seed) is not int or seed not in cfg['development']['seeds']:
        raise ValueError('Unregistered development seed')
    if seed != 24001 or cfg['task_kinds'] != list(KINDS) or cfg['per_category_per_kind'] != 4:
        raise ValueError('This version has one fixed 24-request pilot')
    result = []
    for category in CATEGORIES:
        for kind_index, kind in enumerate(KINDS):
            for instance in range(4):
                index = kind_index * 4 + instance
                bucket = cfg['bucket_assignment_by_kind'][kind][instance]
                req_id = f"v4-{seed}-{'i' if category == 'interactive' else 'b'}{index:03d}"
                result.append({'req_id': req_id, 'content_id': req_id, 'split': split, 'seed': seed,
                    'category': category, 'category_index': index, 'task_kind': kind,
                    'kind_instance': instance, 'template_family': f'v4_development_{kind}',
                    'template_version': cfg['template_version'], 'source_id': cfg['source_id'],
                    'input_token_bucket': bucket,
                    'input_token_range': deepcopy(cfg['input_token_buckets'][category][bucket]),
                    'target_count': cfg['target_counts'][category][kind],
                    'max_tokens': cfg['max_output_tokens'][category],
                    'temperature': cfg['sampling']['temperature'], 'ignore_eos': False,
                    'cache_prompt': False})
    counts = Counter((r['category'], r['input_token_bucket']) for r in result)
    assert len(result) == 24 and len(counts) == 6 and set(counts.values()) == {4}
    assert set(Counter((r['category'], r['task_kind']) for r in result).values()) == {4}
    return result


def _priority(descriptor, record_id):
    return sha_text(f"{descriptor['req_id']}:source-order:{record_id}")


def task_material(descriptor, extra_records=0):
    """Return only deterministic source facts and their evaluation-only target.

    Target record values are stable when extra records are added. A fixed hash
    order scatters rows without assigning temporal meaning; organize requests the
    reverse of the target rows' source order, so it actually requires reordering.
    """
    if descriptor['split'] != 'development' or descriptor['seed'] != 24001:
        raise ValueError('Only registered development descriptors are allowed')
    if type(extra_records) is not int or not 0 <= extra_records <= 128:
        raise ValueError('Extra record count must be an integer from 0 to 128')
    target_count = descriptor['target_count']
    if type(target_count) is not int or target_count < 1:
        raise ValueError('Invalid target count')
    ids = [f'R{j}' for j in range(1, target_count + extra_records + 1)]
    records = []
    for j, record_id in enumerate(ids, 1):
        rng = random.Random(f"{descriptor['req_id']}:value:{record_id}")
        value = rng.randint(10, 99)
        if j == 1:
            value = 10 + (descriptor['seed'] % 41 + descriptor['category_index'] * 7) % 90
        records.append({'id': record_id, 'value': str(value)})
    records.sort(key=lambda r: _priority(descriptor, r['id']))
    targets = [f'R{j}' for j in range(1, target_count + 1)]
    if descriptor['task_kind'] == 'organize':
        targets = list(reversed([r['id'] for r in records if r['id'] in set(targets)]))
    by_id = {r['id']: r['value'] for r in records}
    data = {'target_ids': targets, 'extra_records': extra_records,
            'oracle_evaluation_only': True, 'evaluation_only': True}
    if descriptor['task_kind'] == 'check':
        right = []
        for record in reversed(records):
            ordinal = int(record['id'][1:])
            mismatch = (ordinal + descriptor['kind_instance']) % 2 == 1
            value = str(10 + (int(record['value']) - 10 + 1) % 90) if mismatch else record['value']
            right.append({'id': record['id'], 'value': value})
        right_map = {r['id']: r['value'] for r in right}
        data.update(left_records=records, right_records=right)
        lines = [f"{key}|{'一致' if by_id[key] == right_map[key] else '不一致'}" for key in targets]
    else:
        data['source_records'] = records
        lines = [f'{key}|{by_id[key]}' for key in targets]
    data.update(expected_lines=lines, expected_answer='\n'.join(lines))
    return data


def render_candidate(descriptor, extra_records=0):
    data = task_material(descriptor, extra_records)
    def table(records):
        return '\n'.join(r['id'] + '|' + r['value'] for r in records)
    targets = ','.join(data['target_ids'])
    if descriptor['task_kind'] == 'check':
        user = ('A\n' + table(data['left_records']) + '\nB\n' + table(data['right_records'])
            + f'\n核对{targets}：同写一致，异写不一致。每行ID|结论。')
    else:
        task = f'提取{targets}。' if descriptor['task_kind'] == 'extract' else f'按{targets}顺序整理。'
        user = '资料\n' + table(data['source_records']) + '\n' + task + '每行ID|值。'
    return ('<|im_start|>system\n只输出结果，无表头。<|im_end|>\n'
        '<|im_start|>user\n' + user + '<|im_end|>\n<|im_start|>assistant\n')


def materialize_trace(descriptors, tokenize, config=None):
    cfg = config or load_config()
    rows = []
    for descriptor in descriptors:
        low, high = descriptor['input_token_range']
        cache = {}
        def measure(units):
            if units not in cache:
                prompt = render_candidate(descriptor, units)
                tokens = tokenize(prompt)
                if not isinstance(tokens, list) or not tokens or any(type(t) is not int or t < 0 for t in tokens):
                    raise ValueError('Tokenizer must return nonempty actual nonnegative integer token IDs')
                cache[units] = (prompt, tokens)
            return len(cache[units][1])
        if measure(0) >= low:
            units = 0
        else:
            left, right = 1, 1
            while measure(right) < low and right < cfg['max_extra_records']:
                left, right = right + 1, min(cfg['max_extra_records'], right * 2)
            if measure(right) < low:
                raise ValueError('Input bucket unreachable: ' + descriptor['req_id'])
            while left < right:
                middle = (left + right) // 2
                if measure(middle) < low:
                    left = middle + 1
                else:
                    right = middle
            units = left
        prompt, tokens = cache[units]
        if not low <= len(tokens) <= high:
            raise ValueError(f"Input bucket infeasible for {descriptor['req_id']}: {len(tokens)} not in {low}..{high}")
        if len(tokens) + descriptor['max_tokens'] + cfg['input_plus_output_safety_tokens'] > cfg['slot_context_tokens']:
            raise ValueError('Input plus output budget exceeds registered context')
        row = deepcopy(descriptor)
        row.update(task_material(descriptor, units))
        row.update(prompt=prompt, input_tokens=len(tokens),
            token_ids_sha256=sha_text(json.dumps(tokens, separators=(',', ':'))),
            content_sha256=sha_text(prompt), prompt_format=cfg['prompt_format'],
            tokenization=deepcopy(cfg['tokenizer_request']), arrival_offset_ms=0)
        rows.append(row)
    if len({r['req_id'] for r in rows}) != len(rows) or len({r['content_sha256'] for r in rows}) != len(rows):
        raise ValueError('Duplicate request or prompt content')
    return rows
