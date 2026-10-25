#!/usr/bin/env python3
"""Paired original/clear instructions over one new set of source facts.

Both arms share the source rows, target order and answer. Only the final public
instruction changes; tokenization is separately measured for the two prompts.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / 'configs/p1-explicit-instructions-v5.json'
KINDS = ('extract', 'check', 'organize')
CATEGORIES = ('interactive', 'background')
ARMS = ('O', 'C')


def load_config(path=DEFAULT_CONFIG):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha_text(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def build_descriptors(config=None, split='development', seed=25001):
    cfg = config or load_config()
    if split != 'development' or cfg['allowed_splits'] != ['development']:
        raise ValueError('Only the registered development pilot is allowed; final and calibration are disabled')
    if type(seed) is not int or seed not in cfg['development']['seeds']:
        raise ValueError('Unregistered development seed')
    if seed != 25001 or cfg['task_kinds'] != list(KINDS) or cfg['per_category_per_kind'] != 4:
        raise ValueError('This version has one fixed 24-task paired pilot')
    result = []
    for category in CATEGORIES:
        for kind_index, kind in enumerate(KINDS):
            for instance in range(4):
                index = kind_index * 4 + instance
                bucket = cfg['bucket_assignment_by_kind'][kind][instance]
                identity = f"v5-{seed}-{'i' if category == 'interactive' else 'b'}{index:03d}"
                result.append({'req_id':identity, 'content_id':identity, 'split':split, 'seed':seed,
                    'category':category, 'category_index':index, 'task_kind':kind,
                    'kind_instance':instance, 'template_family':f'v5_development_{kind}',
                    'template_version':cfg['template_version'], 'source_id':cfg['source_id'],
                    'input_token_bucket':bucket,
                    'input_token_range':deepcopy(cfg['input_token_buckets'][category][bucket]),
                    'target_count':cfg['target_counts'][category][kind],
                    'max_tokens':cfg['max_output_tokens'][category],
                    'temperature':cfg['sampling']['temperature'], 'ignore_eos':False, 'cache_prompt':False})
    cells = Counter((row['category'],row['input_token_bucket']) for row in result)
    assert len(result) == 24 and len(cells) == 6 and set(cells.values()) == {4}
    assert set(Counter((row['category'],row['task_kind']) for row in result).values()) == {4}
    return result


def _source_identity(descriptor):
    """Arm-suffixed transport IDs never change the source truth."""
    identity = descriptor['content_id']
    if not isinstance(identity,str) or not identity.startswith('v5-25001-'):
        raise ValueError('Registered base content identity required')
    return identity


def _priority(descriptor, record_id):
    return sha_text(f"{_source_identity(descriptor)}:source-order:{record_id}")


def task_material(descriptor, extra_records=0):
    if descriptor['split'] != 'development' or descriptor['seed'] != 25001:
        raise ValueError('Only registered development descriptors are allowed')
    if type(extra_records) is not int or not 0 <= extra_records <= 128:
        raise ValueError('Extra record count must be an integer from 0 to 128')
    target_count = descriptor['target_count']
    if type(target_count) is not int or target_count < 1:
        raise ValueError('Invalid target count')
    identity = _source_identity(descriptor)
    records = []
    for ordinal in range(1,target_count+extra_records+1):
        record_id = f'R{ordinal}'
        value = random.Random(f'{identity}:value:{record_id}').randint(10,99)
        if ordinal == 1:
            value = 10 + (descriptor['seed'] % 41 + descriptor['category_index'] * 7) % 90
        records.append({'id':record_id,'value':str(value)})
    records.sort(key=lambda record:_priority(descriptor,record['id']))
    targets = [f'R{ordinal}' for ordinal in range(1,target_count+1)]
    if descriptor['task_kind'] == 'organize':
        targets = list(reversed([record['id'] for record in records if record['id'] in set(targets)]))
    source = {record['id']:record['value'] for record in records}
    data = {'target_ids':targets,'extra_records':extra_records,
        'oracle_evaluation_only':True,'evaluation_only':True}
    if descriptor['task_kind'] == 'check':
        right = []
        for record in reversed(records):
            mismatch = (int(record['id'][1:])+descriptor['kind_instance']) % 2 == 1
            value = str(10+(int(record['value'])-10+1)%90) if mismatch else record['value']
            right.append({'id':record['id'],'value':value})
        right_values = {record['id']:record['value'] for record in right}
        data.update(left_records=records,right_records=right)
        lines = [f"{target}|{'一致' if source[target] == right_values[target] else '不一致'}" for target in targets]
    else:
        data['source_records'] = records
        lines = [target+'|'+source[target] for target in targets]
    data.update(expected_lines=lines,expected_answer='\n'.join(lines))
    return data


def task_instruction(kind, target_ids, arm='O'):
    if arm not in ARMS or kind not in KINDS:
        raise ValueError('Registered task and O/C instruction arm required')
    targets = ','.join(target_ids)
    if arm == 'O':
        if kind == 'check':
            return f'核对{targets}：同写一致，异写不一致。每行ID|结论。'
        return (f'提取{targets}。' if kind == 'extract' else f'按{targets}顺序整理。')+'每行ID|值。'
    if kind == 'extract':
        return f'提取{targets}对应的数值，不要复制编号作值。每行ID|值。'
    if kind == 'organize':
        return f'只按{targets}顺序抄对应数值，不要抄其他行。每行ID|值。'
    return f'逐项比较A和B中{targets}的数值：相等写一致，否则写不一致。每行ID|结论。'


def render_candidate(descriptor, extra_records=0, arm='O'):
    data = task_material(descriptor,extra_records)
    table = lambda records:'\n'.join(record['id']+'|'+record['value'] for record in records)
    if descriptor['task_kind'] == 'check':
        user = 'A\n'+table(data['left_records'])+'\nB\n'+table(data['right_records'])
    else:
        user = '资料\n'+table(data['source_records'])
    user += '\n'+task_instruction(descriptor['task_kind'],data['target_ids'],arm)
    return ('<|im_start|>system\n只输出结果，无表头。<|im_end|>\n'
        '<|im_start|>user\n'+user+'<|im_end|>\n<|im_start|>assistant\n')


def materialize_trace(descriptors, tokenize, config=None):
    """Return already paired O/C evaluation rows, using shared source counts."""
    cfg = config or load_config()
    if cfg['max_extra_records'] != 128:
        raise ValueError('Registered extra-record ceiling must remain 128')
    rows = []
    for pair_index,descriptor in enumerate(descriptors,1):
        low,high = descriptor['input_token_range']
        cache = {}
        def measure(units):
            if units not in cache:
                variants = {}
                for arm in ARMS:
                    prompt = render_candidate(descriptor,units,arm)
                    tokens = tokenize(prompt)
                    if not isinstance(tokens,list) or not tokens or any(type(token) is not int or token < 0 for token in tokens):
                        raise ValueError('Tokenizer must return actual nonnegative integer token IDs')
                    variants[arm] = (prompt,tokens)
                cache[units] = variants
            return [len(cache[units][arm][1]) for arm in ARMS]
        if min(measure(0)) >= low:
            units = 0
        else:
            left,right = 1,1
            while min(measure(right)) < low and right < cfg['max_extra_records']:
                left,right = right+1,min(cfg['max_extra_records'],right*2)
            if min(measure(right)) < low:
                units = None
            else:
                while left < right:
                    middle = (left+right)//2
                    if min(measure(middle)) < low:
                        left = middle+1
                    else:
                        right = middle
                units = left
        # Token counts need not be perfectly monotone when inserted rows alter
        # boundaries. If the fast candidate fails, inspect the finite registered
        # domain rather than incorrectly relaxing a bucket or declaring failure.
        if units is None or not all(low <= count <= high for count in measure(units)):
            feasible = next((candidate for candidate in range(cfg['max_extra_records']+1)
                if all(low <= count <= high for count in measure(candidate))),None)
            if feasible is None:
                raise ValueError(f"Shared input bucket infeasible for {descriptor['req_id']}: O/C must both fit {low}..{high}")
            units = feasible
        material = task_material(descriptor,units)
        order = 'OC' if descriptor['kind_instance'] % 2 == 0 else 'CO'
        pair_id = descriptor['content_id']
        for position,arm in enumerate(order,1):
            prompt,tokens = cache[units][arm]
            if len(tokens)+descriptor['max_tokens']+cfg['input_plus_output_safety_tokens'] > cfg['slot_context_tokens']:
                raise ValueError('Input plus output budget exceeds registered context')
            row = deepcopy(descriptor)
            row.update(deepcopy(material))
            row.update(req_id=pair_id+'-'+arm.lower(),pair_id=pair_id,pair_index=pair_index,
                arm=arm,condition_order=order,condition_position=position,
                prompt=prompt,input_tokens=len(tokens),
                token_ids_sha256=sha_text(json.dumps(tokens,separators=(',',':'))),
                content_sha256=sha_text(prompt),prompt_format=cfg['prompt_format'],
                tokenization=deepcopy(cfg['tokenizer_request']),arrival_offset_ms=0)
            rows.append(row)
    if len({row['req_id'] for row in rows}) != len(rows) or len({row['content_sha256'] for row in rows}) != len(rows):
        raise ValueError('Duplicate request or prompt content')
    return rows
