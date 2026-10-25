#!/usr/bin/env python3
"""Independent O/C v5 audit; no new generator, collection, grammar or 97 imports."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

ARMS = ('O', 'C')
KINDS = ('extract', 'check', 'organize')
FORBIDDEN = {'expected_answer', 'expected_lines', 'evaluation_only', 'oracle_evaluation_only',
    'source_records', 'left_records', 'right_records', 'target_ids', 'oracle', 'evaluation'}
MODEL_SHA = '6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e'
ENGINE_COMMIT = '620d2225cc436ab0b001e3200e590e3b286ef9ab'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def independent_answer(reference):
    """Use raw source facts, not expected text or the workload generator."""
    assert reference['task_kind'] in KINDS
    targets = reference['target_ids']
    assert targets and len(set(targets)) == len(targets)
    assert all(isinstance(identity, str) and re.fullmatch(r'R[1-9]\d*', identity) for identity in targets)
    count = 6 if reference['category'] == 'background' else (2 if reference['task_kind'] == 'organize' else 1)
    assert len(targets) == count
    fields = ('left_records', 'right_records') if reference['task_kind'] == 'check' else ('source_records',)
    tables = []
    for field in fields:
        data = reference[field]
        assert isinstance(data, list) and data
        table = {record['id']: record['value'] for record in data}
        assert len(table) == len(data) and set(targets) <= table.keys()
        assert all(isinstance(identity, str) and re.fullmatch(r'R[1-9]\d*', identity) for identity in table)
        assert all(isinstance(value, str) and re.fullmatch(r'[1-9][0-9]', value) for value in table.values())
        tables.append(table)
    if len(tables) == 2:
        return [[identity, '一致' if tables[0][identity] == tables[1][identity] else '不一致'] for identity in targets]
    return [[identity, tables[0][identity]] for identity in targets]


def independent_command(kind, targets, arm):
    """Only the two predeclared instruction candidates, never source values."""
    assert kind in KINDS and arm in ARMS
    identities = ','.join(targets)
    if arm == 'O':
        if kind == 'check':
            return '核对' + identities + '：同写一致，异写不一致。每行ID|结论。'
        return ('提取' + identities + '。' if kind == 'extract'
            else '按' + identities + '顺序整理。') + '每行ID|值。'
    if kind == 'check':
        return '逐项比较A和B中' + identities + '的数值：相等写一致，否则写不一致。每行ID|结论。'
    if kind == 'extract':
        return '提取' + identities + '对应的数值，不要复制编号作值。每行ID|值。'
    return '只按' + identities + '顺序抄对应数值，不要抄其他行。每行ID|值。'


def independent_prompt_matches(reference):
    """Independently rebuild full O/C prompt and reject metadata/prompt mismatch."""
    independent_answer(reference)
    def table(field):
        return '\n'.join(record['id'] + '|' + record['value'] for record in reference[field])
    if reference['task_kind'] == 'check':
        body = 'A\n' + table('left_records') + '\nB\n' + table('right_records')
    else:
        body = '资料\n' + table('source_records')
    body += '\n' + independent_command(reference['task_kind'], reference['target_ids'], reference['arm'])
    expected = ('<|im_start|>system\n只输出结果，无表头。<|im_end|>\n'
        '<|im_start|>user\n' + body + '<|im_end|>\n<|im_start|>assistant\n')
    return reference.get('prompt') == expected


def independent_grammar(prompt, category):
    """Read only the public final task line. There is no answer/facts input."""
    assert category in ('interactive', 'background') and isinstance(prompt, str)
    start, ending = '<|im_start|>user\n', '<|im_end|>\n<|im_start|>assistant\n'
    assert prompt.count(start) == 1 and prompt.endswith(ending)
    command = prompt.split(start, 1)[1][:-len(ending)].rsplit('\n', 1)[-1]
    identity_pattern = r'(R[1-9]\d*(?:,R[1-9]\d*)*)'
    patterns = [
        ('extract', r'提取' + identity_pattern + r'。每行ID\|值。'),
        ('check', r'核对' + identity_pattern + r'：同写一致，异写不一致。每行ID\|结论。'),
        ('organize', r'按' + identity_pattern + r'顺序整理。每行ID\|值。'),
        ('extract', r'提取' + identity_pattern + r'对应的数值，不要复制编号作值。每行ID\|值。'),
        ('check', r'逐项比较A和B中' + identity_pattern + r'的数值：相等写一致，否则写不一致。每行ID\|结论。'),
        ('organize', r'只按' + identity_pattern + r'顺序抄对应数值，不要抄其他行。每行ID\|值。')]
    matched = [(kind, re.fullmatch(pattern, command)) for kind, pattern in patterns]
    matched = [(kind, match) for kind, match in matched if match]
    assert len(matched) == 1
    kind, match = matched[0]
    targets = match.group(1).split(',')
    count = 6 if category == 'background' else (2 if kind == 'organize' else 1)
    assert len(targets) == count and len(set(targets)) == count
    terms = []
    for index, identity in enumerate(targets):
        if index:
            terms.append(json.dumps('\n', ensure_ascii=False))
        terms += [json.dumps(identity + '|', ensure_ascii=False), 'value']
    value = '"一致" | "不一致"' if kind == 'check' else '[1-9] [0-9]'
    return 'root ::= ' + ' '.join(terms) + '\nvalue ::= ' + value + '\n'


def independent_fields(content, expected, kind):
    """Keep all planned fields in the denominator; duplicate IDs are ambiguous."""
    parsed, malformed = [], []
    for line in content.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if not line.strip():
            continue
        fields = [part.strip() for part in line.split('|')]
        if len(fields) != 2 or not all(fields):
            malformed.append(line)
        else:
            parsed.append(fields)
    ids = [identity for identity, _ in parsed]
    targets = [identity for identity, _ in expected]
    correct_fields = sum([identity, value] in parsed and ids.count(identity) == 1 for identity, value in expected)
    allowed = lambda value: value in ('一致', '不一致') if kind == 'check' else bool(re.fullmatch(r'[1-9][0-9]', value))
    syntax = not malformed and ids == targets and all(allowed(value) for _, value in parsed)
    return {'business_correct': not malformed and parsed == expected,
        'syntax_valid': syntax, 'planned_fields': len(expected), 'correct_fields': correct_fields,
        'observed_fields': parsed, 'malformed_lines': malformed}


def audit(root, run, analysis=None):
    checks, evidence = 0, {}
    def check(condition, label):
        nonlocal checks
        checks += 1
        if not condition:
            raise AssertionError(label)
    def tracked(path):
        evidence[path.relative_to(root).as_posix()] = sha(path)
    summary = read(run / 'summary.json')
    check(summary == read(run / 'batch-status.json') and summary['status'] == 'completed', 'complete summary')
    check(summary['measured_completions'] == summary['terminal_counts']['completed'] == sum(summary['terminal_counts'].values()) == 48, '48 complete')
    check(summary['unique_tasks'] == 24 and summary['warmup_completions'] == 2 and summary['completion_requests'] == 50, '24 pairs and 50 requests')
    check(summary['accounting']['events'] == 100 and summary['server_exit_code'] == 0, '100 CAP and exit zero')
    check(summary['final_test_generated_or_read'] is False, 'final data isolation')
    for gate_name in ('client-gate', 'structured-client-gate'):
        gate = read(run / gate_name / 'summary.json')
        check(gate['status'] == 'passed' and gate['source_sha256'], 'frozen client gate ' + gate_name)
        for relative, digest in gate['source_sha256'].items():
            check(sha(root / relative) == digest, 'client gate source ' + relative)
    check('scripts/replay/p1_structured_replay_v4.py' in read(run / 'structured-client-gate/summary.json')['source_sha256'], 'new actual HTTP client gate')
    plan, refs = rows(run / 'plan.jsonl'), rows(run / 'evaluation-plan.jsonl')
    planned, reference = {row['req_id']: row for row in plan}, {row['req_id']: row for row in refs}
    check(len(plan) == len(planned) == len(refs) == len(reference) == 48 and set(planned) == set(reference), '48 unique IDs')
    check([row['req_id'] for row in plan] == [row['req_id'] for row in refs], 'evaluation and inference sequence')
    check(Counter(row['arm'] for row in refs) == {'O': 24, 'C': 24}, '24 per arm')
    check(all(not FORBIDDEN & row.keys() for row in plan), 'inference plan excludes offline oracle fields')
    check(all(row['split'] == 'development' and row['seed'] == 25001 for row in refs), 'registered new seed only')
    firsts = refs[::2]
    check(len({row['pair_id'] for row in firsts}) == 24, '24 unique adjacent pairs')
    orders = Counter(row['condition_order'] for row in firsts)
    check(orders == {'OC': 12, 'CO': 12}, '12/12 order balance')
    for index in range(0, 48, 2):
        left, right = refs[index:index + 2]
        check(left['pair_id'] == right['pair_id'], 'adjacent pair IDs')
        check(left['arm'] + right['arm'] == left['condition_order'] == right['condition_order'], 'actual pair order')
        check(left['condition_order'] == ('OC' if left['kind_instance'] % 2 == 0 else 'CO'), 'predeclared order parity')
        check((left['condition_position'], right['condition_position']) == (1, 2), 'within-pair position')
        check(left['pair_index'] == right['pair_index'] == index // 2 + 1, 'pair index')
        for member in (left, right):
            check(member['req_id'] == member['pair_id'] + '-' + member['arm'].lower(), 'arm ID suffix')
        shared = ('content_id', 'input_token_bucket', 'input_token_range', 'max_tokens',
            'task_kind', 'category', 'target_ids', 'expected_lines', 'expected_answer', 'extra_records')
        check(all(left[key] == right[key] for key in shared), 'same paired source design/budget/reference')
        check(left['prompt'] != right['prompt'] and left['content_sha256'] != right['content_sha256']
            and left['token_ids_sha256'] != right['token_ids_sha256'], 'two distinct instruction inputs tokenized separately')
        check(independent_grammar(left['prompt'], left['category']) ==
            independent_grammar(right['prompt'], right['category']), 'same public-only output grammar across O/C')
        facts = ('left_records', 'right_records') if left['task_kind'] == 'check' else ('source_records',)
        check(all(left[key] == right[key] for key in facts), 'same paired source facts')
        check(planned[left['req_id']]['grammar'] == planned[right['req_id']]['grammar'] != '', 'same nonempty planned grammar for both instructions')
    cells = Counter((row['category'], row['input_token_bucket']) for row in firsts)
    check(len(cells) == 6 and set(cells.values()) == {4}, 'six cells four unique tasks each')
    balanced = Counter((row['category'], row['input_token_bucket'], row['condition_order']) for row in firsts)
    check(len(balanced) == 12 and set(balanced.values()) == {2}, 'two each order per cell')
    kinds = Counter((row['category'], row['task_kind'], row['condition_order']) for row in firsts)
    check(len(kinds) == 12 and set(kinds.values()) == {2}, 'two each order per category/task')
    check(Counter(row['task_kind'] for row in firsts) == {kind: 8 for kind in KINDS}, 'three kinds eight pairs each')
    tokenized = {}
    for record in rows(run / 'tokenization.jsonl'):
        check(record['http_status'] == 200, 'tokenizer HTTP')
        check(record['request']['add_special'] is True and record['request']['parse_special'] is True, 'tokenizer flags')
        ids = json.loads(record['response_body'])['tokens']
        check(ids and all(type(value) is int for value in ids), 'actual token IDs')
        tokenized[text_sha(record['request']['content'])] = ids
    observed, assessed = {}, {}
    case_paths = sorted((run / 'cases').glob('q*/replay/client.jsonl'))
    check(len(case_paths) == 48, '48 case files')
    for index, client in enumerate(case_paths):
        recorded = rows(client)
        check(len(recorded) == 1, 'one client record')
        row = recorded[0]
        req_id, directory = row['req_id'], client.parent.parent
        check(req_id == plan[index]['req_id'] and req_id not in observed, 'actual case sequence and unique ID')
        observed[req_id] = row
        check(all(row[key] == value for key, value in planned[req_id].items()), 'inference plan preserved')
        check(not FORBIDDEN & row.keys(), 'client excludes offline oracle fields')
        check(row['terminal'] == 'completed' and row['http_status'] == 200, 'completed HTTP')
        check(read(client.parent / 'requests' / (req_id + '.json')) == row, 'JSON=JSONL')
        check(row['cache_prompt'] is False and row['cached_prompt_tokens'] == 0, 'actual cache disabled')
        check(row['input_tokens'] == row['new_prompt_tokens'] == row['native_final']['tokens_evaluated'], 'native input')
        prompt_sha = text_sha(row['prompt'])
        ids = tokenized[prompt_sha]
        check(prompt_sha == row['content_sha256'] and len(ids) == row['input_tokens'], 'prompt and actual tokenizer length')
        check(text_sha(json.dumps(ids, separators=(',', ':'))) == row['token_ids_sha256'], 'token ID SHA')
        check(row['output_tokens_known'] is True and row['output_tokens'] == row['native_final']['tokens_predicted'] == row['timings']['predicted_n'], 'native output count')
        check(row['native_final']['truncated'] is False and 0 < row['output_tokens'] <= row['max_tokens'], 'output/context bounds')
        check(row['max_tokens'] == {'interactive': 64, 'background': 384}[row['category']], 'fixed output budgets')
        check(row['input_tokens'] + row['max_tokens'] + 1 <= 2048, 'hard capacity')
        registered_ranges = {'interactive': [[32, 79], [80, 143], [144, 256]],
            'background': [[128, 319], [320, 639], [640, 1024]]}
        check(row['input_token_range'] == registered_ranges[row['category']][row['input_token_bucket']], 'registered fixed input bucket range')
        check(row['input_token_range'][0] <= row['input_tokens'] <= row['input_token_range'][1], 'actual input bucket')
        check(row['system_deadline_offset_ms'] == 45000 and 0 <= row['actual_send_offset_ms'] <= row['end_offset_ms'] < 45000, 'planned deadline')
        check(0 <= row['send_lag_ms'] <= 50, 'send-lag gate')
        check((directory / 'output.txt').read_text(encoding='utf-8') == row['content'], 'full output preserved')
        slots = read(directory / 'after-request-slots.json')
        check(len(slots) == 2 and not any(slot['is_processing'] for slot in slots), 'per-request slots idle')
        ref = reference[req_id]
        check(ref['prompt'] == row['prompt'] and ref['input_tokens'] == row['input_tokens'], 'offline/inference input equal')
        check(independent_prompt_matches(ref), 'actual complete prompt matches registered facts/task')
        expected = independent_answer(ref)
        check(ref['expected_lines'] == ['|'.join(item) for item in expected] and ref['expected_answer'] == '\n'.join('|'.join(item) for item in expected), 'independent source-derived answer')
        check(ref.get('oracle_evaluation_only') is True or ref.get('evaluation_only') is True, 'offline marker')
        assessed[req_id] = independent_fields(row['content'], expected, ref['task_kind'])
        grammar = independent_grammar(row['prompt'], row['category'])
        check(bool(grammar) and row.get('grammar') == grammar, 'both arms have public-only grammar in inference row')
        check(row['grammar_sha256'] == text_sha(grammar), 'grammar SHA')
        settings = row['native_final'].get('generation_settings')
        check(isinstance(settings, dict) and 'grammar' in settings and settings['grammar'] == grammar, 'actual engine grammar echo')
        check(settings.get('grammar_lazy') is False, 'grammar active from beginning')
        check(settings.get('temperature') == 0 and settings.get('ignore_eos') is False and settings.get('n_predict') == row['max_tokens'], 'actual generation settings')
        payload_record = read(directory / 'completion-request-payload.json')
        check(payload_record['evidence_kind'] == 'reconstructed_from_frozen_client_not_actual_wire_capture', 'payload provenance label')
        expected_payload = {'prompt': row['prompt'], 'n_predict': row['max_tokens'], 'temperature': 0.0,
            'stream': True, 'ignore_eos': False, 'cache_prompt': False, 'request_class': row['category'], 'client_req_id': req_id}
        expected_payload['grammar'] = grammar
        check(payload_record['payload'] == expected_payload and len(expected_payload) == 9, 'both arms use nine-field HTTP payload and grammar')
        check(not FORBIDDEN & payload_record['payload'].keys(), 'offline answer fields not in payload')
        check(payload_record['client_source_sha256'] == sha(root / 'scripts/replay/p1_structured_replay_v4.py'), 'new native client SHA')
        for item in (client, directory / 'output.txt', directory / 'completion-request-payload.json'):
            tracked(item)
    check(set(observed) == set(planned), 'all planned requests observed')
    by_arm = {}
    for arm in ARMS:
        subset = [assessed[row['req_id']] for row in refs if row['arm'] == arm]
        by_arm[arm] = {'requests': len(subset), 'business_correct': sum(item['business_correct'] for item in subset),
            'syntax_valid': sum(item['syntax_valid'] for item in subset), 'planned_fields': sum(item['planned_fields'] for item in subset),
            'correct_fields': sum(item['correct_fields'] for item in subset)}
        check(by_arm[arm]['requests'] == 24 and by_arm[arm]['planned_fields'] == 88, 'fixed 24/88 denominator per arm')
    pair_counts = {'both_correct': 0, 'both_wrong': 0, 'O_wrong_C_correct': 0, 'O_correct_C_wrong': 0}
    for first in firsts:
        pair_id = first['pair_id']
        original, clear = (assessed[pair_id + '-' + arm]['business_correct'] for arm in ('o', 'c'))
        key = ('both_correct' if original and clear else 'both_wrong' if not original and not clear
            else 'O_wrong_C_correct' if clear else 'O_correct_C_wrong')
        pair_counts[key] += 1
    check(sum(pair_counts.values()) == 24, 'four paired outcome cells complete')
    warm = rows(run / 'warmup/client.jsonl')
    check(len(warm) == 2 and {row['req_id'] for row in warm} == {'warm0', 'warm1'}, 'two registered warmups')
    check(all(row['terminal'] == 'completed' and row['cache_prompt'] is False and row['cached_prompt_tokens'] == 0 for row in warm), 'uncached warmups complete')
    events = []
    for line in (run / 'service/server.log').read_text(encoding='utf-8', errors='replace').splitlines():
        for kind in ('LAUNCH', 'RELEASE'):
            if 'SCHED_CAP_' + kind in line:
                events.append(dict(event=kind, **dict(item.split('=', 1) for item in line.split() if '=' in item)))
    check(len(events) == 100, '100 actual CAP events')
    expected_ids = set(observed) | {'warm0', 'warm1'}
    check({event['clientid'] for event in events} == expected_ids, 'CAP ID coverage')
    active, count = {}, 0
    for event in events:
        check(int(event['cap']) == 0 and int(event['running_background_before']) == count, 'CAP before')
        if event['event'] == 'LAUNCH':
            check(event['slot'] not in active, 'launch free slot')
            active[event['slot']] = event
            count += event['class'] == 'background'
        else:
            prior = active.pop(event['slot'])
            check(prior['clientid'] == event['clientid'] and prior['id'] == event['id'], 'matching release')
            count -= event['class'] == 'background'
        check(count >= 0 and int(event['running_background_after']) == count, 'CAP after')
    check(not active and count == 0, 'final CAP zero')
    for req_id in expected_ids:
        check([event['event'] for event in events if event['clientid'] == req_id] == ['LAUNCH', 'RELEASE'], 'one CAP pair per ID')
    slots = read(run / 'service/after-all-slots.json')
    check(len(slots) == 2 and not any(slot['is_processing'] for slot in slots), 'final slots idle')
    props = read(run / 'service/props.json')
    check(props['total_slots'] == 2 and props['default_generation_settings']['n_ctx'] == 2048, 'two slots each 2048')
    for metric in ('llamacpp:requests_processing', 'llamacpp:requests_deferred'):
        found = [float(line.split()[1]) for line in (run / 'service/after-all-metrics.txt').read_text().splitlines() if line.startswith(metric + ' ')]
        check(found and all(value == 0 for value in found), 'final metric zero')
    check(read(run / 'service-stop.json')['server_exit_code'] == 0, 'owned server exit')
    sources = read(run / 'source-sha256.json')
    check(len(sources) == 69, '69 registered project sources')
    old = read(root / 'results/raw/development/20261025-1045-p1-output-constraints-v4/source-sha256.json')
    check(len(old) == 58 and all(sources.get(relative) == digest for relative, digest in old.items()), 'old 58 collection sources frozen')
    old_evaluation = read(root / 'results/raw/maintenance/20261025-p1-output-constraints-evaluation-freeze-v4/summary.json')['evaluation_source_sha256']
    check(len(old_evaluation) == 3 and all(sources.get(relative) == digest for relative, digest in old_evaluation.items()), 'old evaluation three sources frozen')
    for relative, digest in sources.items():
        check(sha(root / relative) == sha(run / 'source' / relative) == digest, 'project source ' + relative)
    env = read(run / 'environment.json')
    check(env['engine_commit'] == ENGINE_COMMIT and env['model_sha256'] == MODEL_SHA, 'fixed engine/model identities')
    engine_root = Path(env['engine_binary']).parents[2]
    engine_sources = read(run / 'engine-source-sha256.json')
    check(len(engine_sources) == 8, 'eight engine sources')
    for relative, digest in engine_sources.items():
        check(sha(run / 'engine-source' / relative) == sha(engine_root / relative) == digest, 'actual engine source')
    before = read(run / 'engine-runtime-sha256.json')
    check(len(before) == 23 and before == read(run / 'engine-runtime-sha256-after.json'), '23 runtime entries before/after')
    actual = set()
    for entry in before.values():
        path = Path(entry['path'])
        check(str(path.resolve()) == entry['realpath'], 'runtime resolved target')
        if entry['realpath'] not in actual:
            check(sha(path) == entry['sha256'], 'actual runtime bytes')
            actual.add(entry['realpath'])
    check(len(actual) == 9, 'nine actual runtime files')
    after = read(run / 'model-sha256-after.json')
    check(env['model_sha256'] == after['sha256'] == sha(Path(env['model'])) == MODEL_SHA, 'actual model bytes before/after')
    if analysis is not None:
        reported = read(analysis / 'summary.json')
        check({item['req_id']: item['business_correct'] for item in reported['request_results']} ==
            {identity: item['business_correct'] for identity, item in assessed.items()}, '97 all individual business results')
        for item in reported['request_results']:
            independent = assessed[item['req_id']]
            check(item['schema_valid'] == independent['syntax_valid'] and
                item['target_fields_correct'] == independent['correct_fields'] and
                item['target_fields_expected'] == independent['planned_fields'], '97 individual syntax and fixed-denominator fields')
        for arm in ARMS:
            check(reported['by_arm'][arm]['requests'] == by_arm[arm]['requests'] and
                reported['by_arm'][arm]['business_correct'] == by_arm[arm]['business_correct'], '97 arm counts')
            check(reported['by_arm'][arm]['schema_valid'] == by_arm[arm]['syntax_valid'] and
                reported['by_arm'][arm]['target_fields_correct'] == by_arm[arm]['correct_fields'] and
                reported['by_arm'][arm]['target_fields_expected'] == by_arm[arm]['planned_fields'], '97 arm syntax and 88-field denominator')
        check(reported['pair_counts'] == pair_counts, '97 paired outcome table')
    for path in (run / 'summary.json', run / 'plan.jsonl', run / 'evaluation-plan.jsonl', run / 'source-sha256.json', run / 'service/server.log'):
        tracked(path)
    return {'status': 'passed', 'checked_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'audit_script_sha256': sha(Path(__file__)), 'checks': checks, 'unique_tasks': 24,
        'measured_completions': 48, 'warmups': 2, 'cap_events': 100, 'frozen_project_sources': len(sources),
        'engine_sources': len(engine_sources), 'runtime_entries': len(before), 'runtime_actual_files': len(actual),
        'max_send_lag_ms': max(row['send_lag_ms'] for row in observed.values()), 'by_arm': by_arm,
        'pair_counts': pair_counts, 'actual_engine_grammar_echo_checked': 48,
        'individual_business_correct': {identity: item['business_correct'] for identity, item in assessed.items()},
        'individual_fields': assessed, 'analysis_compared': analysis is not None,
        'G1_passed': False, 'final_test_accessed': False, 'evidence_sha256': evidence,
        'scope': 'Independent original/clear-instruction paired raw/native/public-only grammar and source-derived answer checks; no collector/generator/97 imports, no GPU execution or general quality guarantee'}


def self_test():
    """Model-free O/C prompt, oracle, grammar and fixed-denominator counterexamples."""
    checked = 0
    def must_fail(call):
        try:
            call()
        except (AssertionError, KeyError, ValueError):
            return
        raise AssertionError('Invalid fixture unexpectedly accepted')
    from copy import deepcopy
    def checked_assert(condition):
        nonlocal checked
        assert condition
        checked += 1
    def checked_fail(call):
        nonlocal checked
        must_fail(call)
        checked += 1
    def make_prompt(ref):
        fields = ('left_records', 'right_records') if ref['task_kind'] == 'check' else ('source_records',)
        table = lambda field: '\n'.join(item['id'] + '|' + item['value'] for item in ref[field])
        body = ('A\n' + table(fields[0]) + '\nB\n' + table(fields[1])
            if ref['task_kind'] == 'check' else '资料\n' + table(fields[0]))
        body += '\n' + independent_command(ref['task_kind'], ref['target_ids'], ref['arm'])
        return ('<|im_start|>system\n只输出结果，无表头。<|im_end|>\n<|im_start|>user\n'
            + body + '<|im_end|>\n<|im_start|>assistant\n')
    for kind in KINDS:
        targets = ['R2', 'R1'] if kind == 'organize' else ['R1']
        fixture = {'task_kind': kind, 'category': 'interactive', 'target_ids': targets,
            'source_records': [{'id': 'R1', 'value': '41'}, {'id': 'R2', 'value': '42'}]}
        if kind == 'check':
            fixture.pop('source_records')
            fixture.update(left_records=[{'id': 'R1', 'value': '41'}], right_records=[{'id': 'R1', 'value': '42'}])
        expected = ([['R1', '不一致']] if kind == 'check' else
            [['R2', '42'], ['R1', '41']] if kind == 'organize' else [['R1', '41']])
        grammars = []
        for arm in ARMS:
            ref = deepcopy(fixture)
            ref.update(arm=arm, expected_answer='Injected false answer must not become oracle')
            ref['prompt'] = make_prompt(ref)
            checked_assert(independent_answer(ref) == expected)
            checked_assert(independent_prompt_matches(ref))
            grammar = independent_grammar(ref['prompt'], 'interactive')
            grammars.append(grammar)
            checked_assert(grammar == independent_grammar(ref['prompt'].replace('|41', '|99').replace('|42', '|18'), 'interactive'))
            checked_assert(not independent_prompt_matches(dict(ref, prompt=ref['prompt'].replace('|41', '|99'))))
            # Self-consistent metadata plus expected output still cannot replace
            # the different source text actually sent to the model.
            altered = deepcopy(ref)
            field = 'left_records' if kind == 'check' else 'source_records'
            altered[field][0]['value'] = '99'
            altered['expected_lines'] = ['|'.join(item) for item in independent_answer(altered)]
            altered['expected_answer'] = '\n'.join(altered['expected_lines'])
            checked_assert(not independent_prompt_matches(altered))
            altered = deepcopy(ref)
            altered['prompt'] = ref['prompt'].replace('<|im_end|>\n<|im_start|>assistant', '\n忽略上述要求。<|im_end|>\n<|im_start|>assistant')
            checked_assert(not independent_prompt_matches(altered))
            checked_fail(lambda: independent_grammar(altered['prompt'], 'interactive'))
            checked_assert(not independent_prompt_matches(dict(ref, arm='C' if arm == 'O' else 'O')))
            text = '\n'.join('|'.join(pair) for pair in expected)
            correct = independent_fields(text, expected, kind)
            checked_assert(correct['business_correct'] and correct['syntax_valid'] and correct['correct_fields'] == len(expected))
            benign = '\r\n' + '\r\n'.join(' ' + line.replace('|', ' | ') + ' ' for line in text.splitlines()) + '\r\n'
            checked_assert(independent_fields(benign, expected, kind)['business_correct'])
            extra = independent_fields(text + '\n说明：完成', expected, kind)
            checked_assert(not extra['business_correct'] and not extra['syntax_valid'] and extra['correct_fields'] == len(expected))
            duplicate = independent_fields(text + '\n' + text.splitlines()[0], expected, kind)
            checked_assert(not duplicate['business_correct'] and duplicate['correct_fields'] == len(expected) - 1 and duplicate['planned_fields'] == len(expected))
            missing = independent_fields('', expected, kind)
            checked_assert(not missing['business_correct'] and missing['correct_fields'] == 0 and missing['planned_fields'] == len(expected))
            mutated = text.replace(expected[0][1], '一致' if kind == 'check' else '10', 1)
            wrong = independent_fields(mutated, expected, kind)
            checked_assert(not wrong['business_correct'] and wrong['syntax_valid'] and wrong['correct_fields'] == len(expected) - 1)
            invalid = deepcopy(ref)
            invalid[field] += [dict(invalid[field][0])]
            checked_fail(lambda: independent_answer(invalid))
            checked_fail(lambda: independent_grammar(ref['prompt'].replace('每行ID', '任意回复ID'), 'interactive'))
        checked_assert(grammars[0] == grammars[1])
    return {'status': 'passed', 'model_started': False, 'independent_helper_checks': checked,
        'audit_source_sha256': sha(Path(__file__))}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--analysis-dir', type=Path)
    args = parser.parse_args()
    result = audit(args.root.resolve(), args.run_dir.resolve(), args.analysis_dir.resolve() if args.analysis_dir else None)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write('\n')
    print(json.dumps({key: value for key, value in result.items()
        if key not in ('evidence_sha256', 'individual_business_correct', 'individual_fields')}, ensure_ascii=False))
