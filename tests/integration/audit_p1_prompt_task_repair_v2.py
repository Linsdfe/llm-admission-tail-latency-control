#!/usr/bin/env python3
"""Independent P1 repair raw-data audit: standard library, no collector imports."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(root, run):
    checks = 0
    evidence = {}

    def check(value, label):
        nonlocal checks
        checks += 1
        if not value:
            raise AssertionError(label)

    def tracked(path):
        evidence[path.relative_to(root).as_posix()] = sha(path)

    summary = read(run / 'summary.json')
    check(summary == read(run / 'batch-status.json') and summary['status'] == 'completed', 'complete consistent status')
    check(summary['completion_requests'] == 42 and summary['measured_completions'] == 40, '42/40 counts')
    check(summary['terminal_counts']['completed'] == sum(summary['terminal_counts'].values()) == 40, 'all terminals')
    check(summary['cache_prompt'] is False and summary['all_cached_prompt_tokens_zero'] is True, 'cache mode')
    check(summary['final_test_generated_or_read'] is False, 'no final')
    plan = rows(run / 'plan.jsonl')
    check(len(plan) == len({r['req_id'] for r in plan}) == 40, 'plan IDs')
    check(len(rows(run / 'paired-sentinel-plan.jsonl')) == 16, '16 paired plan')
    check(len(rows(run / 'fresh-development-plan.jsonl')) == 24, '24 fresh plan')
    tokenized = {}
    for row in rows(run / 'tokenization.jsonl'):
        check(row['http_status'] == 200, 'tokenizer HTTP')
        check(row['request']['add_special'] is True and row['request']['parse_special'] is True, 'tokenizer flags')
        ids = json.loads(row['response_body'])['tokens']
        prompt = row['request']['content']
        check(all(type(v) is int for v in ids), 'token IDs integer')
        tokenized[hashlib.sha256(prompt.encode()).hexdigest()] = ids
    diagnostics = read(run / 'diagnostics.json')
    check(len(diagnostics) == 40, '40 diagnostics')
    observed = {}
    for diagnostic in diagnostics:
        directory = run / diagnostic['directory']
        data = rows(directory / 'replay/client.jsonl')
        check(len(data) == 1, 'one recorded request')
        row = data[0]
        check(row['req_id'] not in observed, 'unique record ID')
        observed[row['req_id']] = row
        check(read(directory / 'replay/requests' / (row['req_id']+'.json')) == row, 'JSON=JSONL')
        check(row['terminal'] == 'completed' and row['http_status'] == 200, 'completed HTTP')
        check(row['cache_prompt'] is False and row['cached_prompt_tokens'] == 0, 'uncached request')
        check(row['new_prompt_tokens'] == row['input_tokens'] == row['native_final']['tokens_evaluated'], 'native input equality')
        prompt_sha = hashlib.sha256(row['prompt'].encode()).hexdigest()
        check(prompt_sha == row['content_sha256'], 'input content hash')
        ids = tokenized[prompt_sha]
        check(len(ids) == row['input_tokens'], 'actual tokenize length')
        check(hashlib.sha256(json.dumps(ids, separators=(',', ':')).encode()).hexdigest() == row['token_ids_sha256'], 'actual token ID hash')
        check(row['output_tokens_known'] is True, 'output known')
        check(row['output_tokens'] == row['native_final']['tokens_predicted'] == row['timings']['predicted_n'], 'native output equality')
        check(row['native_final']['truncated'] is False, 'no context truncation')
        check(row['max_tokens'] == {'interactive': 64, 'background': 384}[row['category']], 'same fixed caps')
        check(0 < row['output_tokens'] <= row['max_tokens'] and row['input_tokens']+row['max_tokens']+1 <= 2048, 'output and capacity')
        check(row['system_deadline_offset_ms'] == 45000, '45s planned deadline')
        check(0 <= row['actual_send_offset_ms'] <= row['end_offset_ms'] < 45000 and 0 <= row['send_lag_ms'] <= 50, 'time and send lag')
        check((directory / 'output.txt').read_text(encoding='utf-8') == row['content'] == diagnostic['content'], 'full output retained')
        check(diagnostic['content_sha256'] == hashlib.sha256(row['content'].encode()).hexdigest(), 'output text hash')
        slots = read(directory / 'after-request-slots.json')
        check(len(slots) == 2 and all(s['is_processing'] is False for s in slots), 'per-request idle')
        for path in (directory/'replay/client.jsonl', directory/'output.txt'):
            tracked(path)
    check(set(observed) == {r['req_id'] for r in plan}, 'observed IDs equal plan')
    for intended in plan:
        check(all(observed[intended['req_id']][key] == value for key, value in intended.items()), 'all planned values preserved')
    fresh = [r for r in plan if r['repair_role'] == 'fresh_development_pilot']
    paired = [r for r in plan if r['repair_role'] == 'paired_sentinel']
    check(len(fresh) == 24 and len(paired) == 16, 'fresh/paired counts')
    check(all(r['prompt_version'] == 'v2' and r['split'] == 'development' and r['seed'] == 22001 for r in fresh), 'fresh identities')
    cells = Counter((r['category'], r['input_token_bucket']) for r in fresh)
    check(len(cells) == 6 and set(cells.values()) == {4}, 'six fresh cells')
    for row in fresh:
        check(row['input_token_range'][0] <= row['input_tokens'] <= row['input_token_range'][1], 'fresh actual bucket')
    for index in range(1, 9):
        pair = [r for r in paired if r['pair_index'] == index]
        check(len(pair) == 2 and {r['prompt_version'] for r in pair} == {'v1', 'v2'}, 'paired versions')
        check(len({r['source_case'] for r in pair}) == len({r['source_req_id'] for r in pair}) == 1, 'paired source')
        check([r['prompt_version'] for r in pair] == (['v1', 'v2'] if index % 2 else ['v2', 'v1']), 'AB/BA order')
        by_version = {r['prompt_version']: r for r in pair}
        old, revised = by_version['v1'], by_version['v2']
        old_file = root / 'results/raw/development/20261024-025138-p1-development-validation-v1/prep/calibration' / old['source_case'] / 'replay/client.jsonl'
        original = rows(old_file)[0]
        check(old['prompt'] == original['prompt'] and old['max_tokens'] == original['max_tokens'], 'paired v1 original task and cap')
        check(revised['repair_source_prompt_sha256'] == hashlib.sha256(old['prompt'].encode()).hexdigest(), 'repair source hash')
        for field in ('task', 'records'):
            text = revised['repair_original_' + field]
            check(text in revised['prompt'], 'retained original ' + field)
            check(hashlib.sha256(text.encode()).hexdigest() == revised['repair_' + field + '_sha256'], 'repair metadata ' + field)
        check(revised['repair_uses_completion_or_answer'] is False, 'no answer injection declaration')
    check({r['template_family'] for r in paired}.isdisjoint(r['template_family'] for r in fresh), 'family separation')
    warm = rows(run / 'warmup/client.jsonl')
    check(len(warm) == 2 and all(r['terminal'] == 'completed' and r['cache_prompt'] is False and r['cached_prompt_tokens'] == 0 for r in warm), 'two uncached warmups')
    events = []
    for line in (run / 'service/server.log').read_text(encoding='utf-8', errors='replace').splitlines():
        for event in ('LAUNCH', 'RELEASE'):
            if 'SCHED_CAP_' + event in line:
                events.append(dict(event=event, **dict(v.split('=', 1) for v in line.split() if '=' in v)))
    check(len(events) == 84, '84 CAP events')
    ids = set(observed) | {'warm0', 'warm1'}
    check({e['clientid'] for e in events} == ids, 'CAP identities')
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
    check(not active and count == 0, 'CAP final zero')
    for req_id in ids:
        check([e['event'] for e in events if e['clientid'] == req_id] == ['LAUNCH', 'RELEASE'], 'one exact pair')
    slots = read(run/'service/after-all-slots.json')
    check(len(slots) == 2 and not any(s['is_processing'] for s in slots), 'final idle slots')
    metrics = (run/'service/after-all-metrics.txt').read_text()
    for metric in ('llamacpp:requests_processing', 'llamacpp:requests_deferred'):
        found = [float(line.split()[1]) for line in metrics.splitlines() if line.startswith(metric+' ')]
        check(bool(found) and all(value == 0 for value in found), 'final zero '+metric)
    check(read(run/'service-stop.json')['server_exit_code'] == 0, 'owned stop success')
    for manifest, directory in (('source-sha256.json', 'source'), ('input-source-sha256.json', 'input-source')):
        for relative, digest in read(run/manifest).items():
            check(sha(root/relative) == sha(run/directory/relative) == digest, 'current and frozen source '+relative)
    env = read(run/'environment.json')
    engine_root = Path(env['engine_binary']).parents[2]
    for relative, digest in read(run/'engine-source-sha256.json').items():
        check(sha(run/'engine-source'/relative) == sha(engine_root/relative) == digest, 'actual engine source')
    runtime = read(run/'engine-runtime-sha256.json')
    check(runtime == read(run/'engine-runtime-sha256-after.json'), 'runtime before after')
    checked_runtime = set()
    for entry in runtime.values():
        path = Path(entry['path'])
        check(str(path.resolve()) == entry['realpath'], 'runtime target')
        if entry['realpath'] not in checked_runtime:
            check(sha(path) == entry['sha256'], 'actual runtime SHA')
            checked_runtime.add(entry['realpath'])
    after = read(run/'model-sha256-after.json')
    check(env['model_sha256'] == after['sha256'] == sha(Path(env['model'])), 'actual model SHA')
    for path in (run/'summary.json', run/'plan.jsonl', run/'source-sha256.json', run/'service/server.log'):
        tracked(path)
    return {'status': 'passed', 'checks': checks, 'measured_completions': 40, 'warmups': 2, 'cap_events': 84,
        'frozen_project_sources': len(read(run/'source-sha256.json')), 'engine_sources': len(read(run/'engine-source-sha256.json')),
        'runtime_entries': len(runtime), 'runtime_actual_files': len(checked_runtime),
        'max_send_lag_ms': max(r['send_lag_ms'] for r in observed.values()),
        'evidence_sha256': evidence, 'semantic_quality_accepted': None,
        'scope': 'Independent raw data integrity and actual frozen asset audit; no collector/analysis imports, no GPU or semantic verdict'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.root.resolve(), args.run_dir.resolve())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write('\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'evidence_sha256'}, ensure_ascii=False))
