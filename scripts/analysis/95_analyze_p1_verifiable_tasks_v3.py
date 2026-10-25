#!/usr/bin/env python3
"""Independent source-derived answer checks for the registered v3 pilot."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import statistics

KINDS = ('extract', 'check', 'organize')
FORBIDDEN_IN_REPLAY = {'expected_lines', 'expected_answer', 'source_records', 'left_records',
    'right_records', 'evaluation_only', 'oracle_evaluation_only'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def dump(path, value):
    with path.open('x', encoding='utf-8') as output:
        json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write('\n')


def source_map(records, label):
    if not isinstance(records, list) or not records:
        raise ValueError(label + ': source must be a nonempty list')
    result = {}
    for item in records:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not re.fullmatch(r'R\d+', item['id']):
            raise ValueError(label + ': invalid source ID')
        value = item.get('value')
        if not isinstance(value, str) or not value or value != value.strip() or any(c in value for c in '|\r\n'):
            raise ValueError(label + ': value must be an unambiguous source string')
        if item['id'] in result:
            raise ValueError(label + ': duplicate source ID ' + item['id'])
        result[item['id']] = value
    return result


def derive_expected(row):
    """Do not read the generator's expected fields when deriving the oracle."""
    kind, targets = row.get('task_kind'), row.get('target_ids')
    if kind not in KINDS or not isinstance(targets, list) or not targets or len(set(targets)) != len(targets):
        raise ValueError('Invalid task kind or ordered targets')
    if any(not isinstance(target, str) or not re.fullmatch(r'R\d+', target) for target in targets):
        raise ValueError('Invalid target ID')
    if kind == 'check':
        left = source_map(row.get('left_records'), 'A')
        right = source_map(row.get('right_records'), 'B')
        if not set(targets) <= left.keys() or not set(targets) <= right.keys():
            raise ValueError('Target not provided in both sources')
        return [[target, '一致' if left[target] == right[target] else '不一致'] for target in targets]
    source = source_map(row.get('source_records'), '资料')
    if not set(targets) <= source.keys():
        raise ValueError('Target not provided in source')
    return [[target, source[target]] for target in targets]


def prompt_facts_match(row):
    """Independently rebuild the registered prompt from source facts and targets.

    Exact matching also rejects additional instructions, repeated user turns or
    changed comparison semantics that a source-row-only parser could overlook.
    No generator or generator-supplied expected answer is consulted.
    """
    derive_expected(row)  # Reject ambiguous or incomplete evaluation facts first.
    target_text = ','.join(row['target_ids'])
    def source_text(records):
        return '\n'.join(item['id'] + '|' + item['value'] for item in records)
    if row['task_kind'] == 'check':
        body = ('A\n' + source_text(row['left_records']) + '\nB\n'
            + source_text(row['right_records'])
            + '\n核对' + target_text + '：同写一致，异写不一致。每行ID|结论。')
    else:
        instruction = ('提取' + target_text + '。') if row['task_kind'] == 'extract' else ('按' + target_text + '顺序整理。')
        body = '资料\n' + source_text(row['source_records']) + '\n' + instruction + '每行ID|值。'
    expected_prompt = ('<|im_start|>system\n只输出结果，无表头。<|im_end|>\n'
        + '<|im_start|>user\n' + body + '<|im_end|>\n<|im_start|>assistant\n')
    if row.get('prompt') != expected_prompt:
        raise ValueError('Prompt facts, task or requested order differ from registered evaluation facts')
    return True


def normalize_fields(content):
    if not isinstance(content, str):
        raise TypeError('Response must be text')
    parsed, invalid, ignored = [], [], 0
    for number, raw in enumerate(content.replace('\r\n', '\n').replace('\r', '\n').split('\n'), 1):
        if not raw.strip():
            ignored += 1
            continue
        fields = raw.split('|')
        if len(fields) != 2 or any(not value.strip() for value in fields):
            invalid.append({'line_number':number, 'text':raw, 'reason':'Expected exactly two nonempty ASCII-pipe fields'})
            continue
        parsed.append([field.strip() for field in fields])
    return parsed, invalid, ignored


def evaluate_answer(row, content):
    expected = derive_expected(row)
    parsed, invalid, ignored = normalize_fields(content)
    expected_ids = [item[0] for item in expected]
    observed_ids = [item[0] for item in parsed]
    wanted, seen = Counter(expected_ids), Counter(observed_ids)
    wanted_values = dict(expected)
    mismatches = [{'position':number, 'id':identity, 'expected':wanted_values[identity], 'observed':value}
        for number, (identity, value) in enumerate(parsed, 1)
        if identity in wanted_values and value != wanted_values[identity]]
    extra_text = [item for item in invalid]
    extra_text += [{'id':identity, 'value':value, 'reason':'Non-record header or explanation'}
        for identity, value in parsed if not re.fullmatch(r'R\d+', identity)]
    return {'business_correct': not invalid and parsed == expected,
        'raw_text_exact': content == '\n'.join('|'.join(item) for item in expected),
        'expected_fields':expected, 'expected_lines':['|'.join(item) for item in expected],
        'observed_fields':parsed, 'structure_valid':bool(parsed) and not invalid,
        'field_values_correct':bool(parsed) and not invalid and not mismatches and all(identity in wanted_values for identity in observed_ids),
        'order_correct':not invalid and observed_ids == expected_ids,
        'missing_ids':list((wanted-seen).elements()), 'extra_ids':list((seen-wanted).elements()),
        'duplicate_ids':[identity for identity,count in seen.items() if count > 1],
        'value_mismatches':mismatches, 'invalid_lines':invalid, 'nonempty_extra_text':extra_text,
        'missing_line_count':max(0,len(expected)-len(parsed)),
        'extra_line_count':max(0,len(parsed)-len(expected)), 'blank_lines_ignored':ignored,
        'normalization':'CRLF/CR to LF, ignore whitespace-only lines, trim two ASCII-pipe fields only'}


def distribution(values):
    return {'samples':len(values), 'min':min(values), 'median':statistics.median(values),
        'max':max(values), 'mean':statistics.mean(values)}


def analyze(run, out):
    run, out = run.resolve(strict=True), out.resolve()
    if out.exists():
        raise FileExistsError('Use a new output directory; never overwrite earlier analyses')
    status = read(run/'summary.json')
    assert status == read(run/'batch-status.json') and status['status'] == 'completed'
    assert status['measured_completions'] == status['terminal_counts']['completed'] == sum(status['terminal_counts'].values()) == 24
    assert status['completion_requests'] == 26 and status['accounting']['events'] == 52 and status['server_exit_code'] == 0
    assert status['final_test_generated_or_read'] is False
    plan, evaluation = rows(run/'plan.jsonl'), rows(run/'evaluation-plan.jsonl')
    assert len(plan) == len(evaluation) == 24
    by_id = {item['req_id']:item for item in evaluation}
    assert len(by_id) == 24 and {item['req_id'] for item in plan} == set(by_id)
    assert not any(FORBIDDEN_IN_REPLAY & row.keys() for row in plan)
    evidence = {name:sha(run/name) for name in ('summary.json','batch-status.json','plan.jsonl','evaluation-plan.jsonl')}
    assessed, fulltexts = [], []
    for path in sorted((run/'cases').glob('q*/replay/client.jsonl')):
        one = rows(path)
        assert len(one) == 1
        row = one[0]
        reference = by_id[row['req_id']]
        assert row['terminal'] == 'completed' and row['http_status'] == 200
        assert row['cached_prompt_tokens'] == 0 and row['cache_prompt'] is False
        assert row['input_tokens'] == row['new_prompt_tokens'] == row['native_final']['tokens_evaluated']
        assert row['output_tokens'] == row['timings']['predicted_n'] == row['native_final']['tokens_predicted']
        assert row['output_tokens_known'] is True and row['native_final']['truncated'] is False
        assert 0 < row['output_tokens'] <= row['max_tokens']
        assert not (FORBIDDEN_IN_REPLAY & row.keys())
        assert row['prompt'] == reference['prompt'] and row['input_tokens'] == reference['input_tokens']
        prompt_facts_match(reference)
        expected = derive_expected(reference)
        expected_lines = ['|'.join(item) for item in expected]
        assert reference['expected_lines'] == expected_lines and reference['expected_answer'] == '\n'.join(expected_lines)
        directory = path.parent.parent
        assert (directory/'output.txt').read_text(encoding='utf-8') == row['content']
        assert read(path.parent/'requests'/(row['req_id']+'.json')) == row
        result = evaluate_answer(reference,row['content'])
        record = {key:reference[key] for key in ('req_id','task_kind','category','input_token_bucket','input_tokens','max_tokens')}
        record.update(output_tokens=row['output_tokens'],stop_type=row['native_final']['stop_type'],
            prompt_sha256=text_sha(row['prompt']),content_sha256=text_sha(row['content']),
            directory=directory.relative_to(run).as_posix(),**result)
        assessed.append(record)
        fulltexts.append({**record,'prompt':row['prompt'],'content':row['content'],
            'evaluation_facts':{key:reference[key] for key in ('target_ids','source_records','left_records','right_records') if key in reference}})
        for item in (path,directory/'output.txt',path.parent/'requests'/(row['req_id']+'.json')):
            evidence[item.relative_to(run).as_posix()] = sha(item)
    assert len(assessed) == len({row['req_id'] for row in assessed}) == 24
    assert Counter(row['task_kind'] for row in assessed) == Counter({kind:8 for kind in KINDS})
    cells = Counter((row['category'],row['input_token_bucket']) for row in assessed)
    assert len(cells) == 6 and set(cells.values()) == {4}
    def aggregate(group):
        return {'requests':len(group),'business_correct':sum(row['business_correct'] for row in group),
            'raw_text_exact':sum(row['raw_text_exact'] for row in group),
            'structure_invalid':sum(not row['structure_valid'] for row in group),
            'field_value_errors':sum(not row['field_values_correct'] for row in group),
            'order_errors':sum(not row['order_correct'] for row in group),
            'missing_ids_cases':sum(bool(row['missing_ids']) for row in group),
            'extra_ids_cases':sum(bool(row['extra_ids']) for row in group),
            'duplicate_ids_cases':sum(bool(row['duplicate_ids']) for row in group),
            'nonempty_extra_text_cases':sum(bool(row['nonempty_extra_text']) for row in group),
            'stop_types':dict(Counter(row['stop_type'] for row in group)),
            'input_tokens':distribution([row['input_tokens'] for row in group]),
            'output_tokens':distribution([row['output_tokens'] for row in group]),
            'total_output_tokens':sum(row['output_tokens'] for row in group)}
    counts = aggregate(assessed)
    result = {'schema_version':1,'status':'passed_independent_source_derived_answer_recomputation',
        'run_id':run.name,'analyzed_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'analysis_script_sha256':sha(Path(__file__)),'counts':counts,
        'by_task_kind':{kind:aggregate([r for r in assessed if r['task_kind']==kind]) for kind in KINDS},
        'by_category':{kind:aggregate([r for r in assessed if r['category']==kind]) for kind in ('interactive','background')},
        'by_cell':{f'{category}:{bucket}':aggregate([r for r in assessed if (r['category'],r['input_token_bucket'])==(category,bucket)]) for category,bucket in sorted(cells)},
        'entry_condition':{'required_correct':24,'observed_correct':counts['business_correct'],
            'satisfied':counts['business_correct']==24,'scope':'May separately register new length and small development load checks only; not G1 or overall business quality'},
        'G1_passed':False,'final_test_accessed':False,'request_results':assessed,
        'limits':['One serial synthetic development pilot; not a population accuracy estimate or scheduler comparison.',
            'Oracle evaluation metadata is kept offline; prompt facts and requested order are independently rechecked.',
            'No output-based task filtering, estimator fitting, threshold selection, or M/B3-value conclusion.']}
    out.mkdir(parents=True,exist_ok=False)
    dump(out/'summary.json',result)
    dump(out/'fulltexts.json',fulltexts)
    dump(out/'evidence-sha256.json',evidence)
    (out/'analysis-source.py').write_bytes(Path(__file__).read_bytes())
    lines = ['# 可核对资料任务v3：逐条确定性核对','',f'批次：`{run.name}`。24测量、2预热另计。主规则只标准化良性空白，不改ID、值、标签或顺序。',
        '', '|请求|任务/类别|输入/输出token|停因|事实与顺序完整正确|原文完全相同|主要差异|','|---|---|---|---|---|---|---|']
    for row in assessed:
        issues = [label for key,label in (('invalid_lines','结构/说明'),('value_mismatches','值'),('missing_ids','漏ID'),('extra_ids','多ID'),('duplicate_ids','重复ID')) if row[key]]
        if not row['order_correct']: issues.append('顺序/覆盖')
        lines.append(f"|{row['req_id']}|{row['task_kind']} / {row['category']}|{row['input_tokens']} / {row['output_tokens']}|{row['stop_type']}|{'是' if row['business_correct'] else '否'}|{'是' if row['raw_text_exact'] else '否'}|{'、'.join(issues) or '—'}|")
    lines += ['',f"主核对正确{counts['business_correct']}/24；原始字节完全相同{counts['raw_text_exact']}/24。各分项会重叠，不相加成单一分数。",'',
        '24/24主核对正确才达到另登记新长度及小开发负载的进入条件；这不是G1通过或总体业务质量保证。EOS、未触顶和技术计数通过不能代替答案核对。完整原文与逐字段差异见summary/fulltexts，助手仍需对全部24原题和输出补核实际任务一致性。','']
    (out/'metrics.md').write_text('\n'.join(lines),encoding='utf-8')
    dump(out/'report-sha256.json',{path.name:sha(path) for path in sorted(out.iterdir()) if path.is_file()})
    return {'status':result['status'],'run_id':run.name,'counts':counts,'entry_condition':result['entry_condition']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--out-dir',type=Path,required=True)
    args = parser.parse_args()
    print(json.dumps(analyze(args.run_dir,args.out_dir),ensure_ascii=False))
