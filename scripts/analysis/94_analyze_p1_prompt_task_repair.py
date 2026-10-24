#!/usr/bin/env python3
"""File-only P1 prompt/task repair metrics; semantic review remains explicit."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import statistics


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


def repetition(text):
    compact = ''.join(text.split())
    result = {}
    for n in (4, 8, 16):
        grams = Counter(compact[start:start+n] for start in range(max(0, len(compact)-n+1)))
        count = sum(grams.values())
        result[str(n)] = {'windows': count, 'unique': len(grams),
            'duplicate_excess_ratio': (count-len(grams))/count if count else None,
            'maximum_occurrences': max(grams.values(), default=0)}
    return {'character_count_without_whitespace': len(compact), 'ngrams': result,
        'scope': 'Descriptive Unicode character windows; no semantic-quality threshold'}


def distribution(values):
    return {'n': len(values), 'min': min(values), 'median': statistics.median(values),
            'max': max(values), 'mean': statistics.mean(values)}


def analyze(run, out):
    run, out = run.resolve(strict=True), out.resolve()
    if out.exists():
        raise FileExistsError('Retain previous analyses; use a new output directory')
    status = read(run / 'summary.json')
    assert status == read(run / 'batch-status.json') and status['status'] == 'completed'
    assert status['server_exit_code'] == 0 and status['accounting']['events'] == 84
    assert status['terminal_counts']['completed'] == 40
    assert sum(status['terminal_counts'].values()) == 40
    assert status['final_test_generated_or_read'] is False
    plan = rows(run / 'plan.jsonl')
    assert len(plan) == len({r['req_id'] for r in plan}) == 40
    by_id = {r['req_id']: r for r in plan}
    evidence = {name: sha(run / name) for name in ('summary.json', 'batch-status.json', 'plan.jsonl')}
    observed = []
    fulltexts = []
    for client in sorted((run / 'cases').glob('q*/replay/client.jsonl')):
        records = rows(client)
        assert len(records) == 1
        row = records[0]
        intended = by_id[row['req_id']]
        assert all(row[key] == value for key, value in intended.items())
        assert row['terminal'] == 'completed' and row['http_status'] == 200
        assert row['cache_prompt'] is False and row['cached_prompt_tokens'] == 0
        assert row['input_tokens'] == row['new_prompt_tokens'] == row['native_final']['tokens_evaluated']
        assert row['output_tokens'] == row['timings']['predicted_n'] == row['native_final']['tokens_predicted']
        assert row['output_tokens_known'] is True and row['native_final']['truncated'] is False
        assert 0 < row['output_tokens'] <= row['max_tokens']
        assert row['max_tokens'] == {'interactive': 64, 'background': 384}[row['category']]
        directory = client.parent.parent
        assert (directory / 'output.txt').read_text(encoding='utf-8') == row['content']
        assert read(client.parent / 'requests' / (row['req_id'] + '.json')) == row
        if row['repair_role'] == 'fresh_development_pilot':
            assert row['split'] == 'development' and row['seed'] == 22001 and row['prompt_version'] == 'v2'
            assert row['input_token_range'][0] <= row['input_tokens'] <= row['input_token_range'][1]
        record = {key: row[key] for key in ('req_id', 'repair_role', 'prompt_version', 'pair_index',
            'source_case', 'source_req_id', 'category', 'template_family', 'input_token_bucket',
            'input_tokens', 'max_tokens', 'output_tokens', 'new_prompt_tokens', 'cached_prompt_tokens')}
        record.update(stop_type=row['native_final']['stop_type'],
            input_plus_max_output_plus_safety=row['input_tokens']+row['max_tokens']+1,
            output_at_cap=row['output_tokens'] == row['max_tokens'],
            content_sha256=text_sha(row['content']), prompt_sha256=text_sha(row['prompt']),
            repetition=repetition(row['content']), source_directory=directory.relative_to(run).as_posix(),
            semantic_review='pending_assistant_exploratory_reading_no_automatic_pass')
        assert record['input_plus_max_output_plus_safety'] <= 2048
        observed.append(record)
        fulltexts.append({**record, 'prompt': row['prompt'], 'content': row['content']})
        for path in (client, directory / 'output.txt', client.parent / 'requests' / (row['req_id'] + '.json')):
            evidence[path.relative_to(run).as_posix()] = sha(path)
    assert len(observed) == 40 and {r['req_id'] for r in observed} == set(by_id)
    paired = [r for r in observed if r['repair_role'] == 'paired_sentinel']
    fresh = [r for r in observed if r['repair_role'] == 'fresh_development_pilot']
    assert len(paired) == 16 and len(fresh) == 24
    counts = Counter((r['category'], r['input_token_bucket']) for r in fresh)
    assert len(counts) == 6 and set(counts.values()) == {4}
    pairs = defaultdict(dict)
    for record in paired:
        assert record['prompt_version'] not in pairs[record['pair_index']]
        pairs[record['pair_index']][record['prompt_version']] = record
    assert set(pairs) == set(range(1, 9)) and all(set(pair) == {'v1', 'v2'} for pair in pairs.values())
    groups = {f'paired_{version}': [r for r in paired if r['prompt_version'] == version] for version in ('v1', 'v2')}
    groups['fresh_v2'] = fresh
    result = {'status': 'passed_file_only_objective_recomputation', 'run_id': run.name,
        'analyzed_at': datetime.now(timezone(timedelta(hours=8))).isoformat(), 'measured_completions': 40,
        'warmups_separate': 2, 'paired_prompts': 8, 'fresh_development_prompts': 24,
        'analysis_script_sha256': sha(Path(__file__)), 'groups': {}, 'pairs': dict(pairs),
        'fresh_development': fresh, 'semantic_quality_gate': None, 'G1_passed': False,
        'scope': 'Serial development diagnostic; no estimator fit, final test, population quality, scheduling or performance effect'}
    for name, group in groups.items():
        result['groups'][name] = {'requests': len(group),
            'stop_types': dict(Counter(r['stop_type'] for r in group)),
            'input_tokens': distribution([r['input_tokens'] for r in group]),
            'output_tokens': distribution([r['output_tokens'] for r in group]),
            'total_output_tokens': sum(r['output_tokens'] for r in group),
            'output_at_cap': sum(r['output_at_cap'] for r in group)}
    out.mkdir(parents=True, exist_ok=False)
    dump(out / 'summary.json', result)
    dump(out / 'fulltexts.json', fulltexts)
    dump(out / 'evidence-sha256.json', evidence)
    (out / 'analysis-source.py').write_bytes(Path(__file__).read_bytes())
    lines = ['# 提示任务修订v2：客观记录', '', f'批次：`{run.name}`。40次测量、2预热另计。语义质量需要另读全文，本表不自动判定任务完成。', '',
        '|请求|用途/版本|类别|输入/实际输出token|停止原因|8字符重复超额比率|', '|---|---|---|---|---|---:|']
    for row in observed:
        ratio = row['repetition']['ngrams']['8']['duplicate_excess_ratio']
        lines.append(f"|{row['req_id']}|{row['repair_role']} / {row['prompt_version']}|{row['category']}|{row['input_tokens']} / {row['output_tokens']}|{row['stop_type']}|{ratio if ratio is not None else 'null'}|")
    lines += ['', '重复指标为去空白后Unicode字符滑窗，不等同token长度或语义质量；没有自动合格阈值。EOS和达到输出上限分别记录，即使实际token恰等上限也按原生stop_type解释。', '',
        '本批全部cache_prompt=false，不与旧cache开启的性能记录直接比较；提示改写也改变输入长度。8条配对是已知问题选例，24条来自固定开发家族，不作总体合格率或显著性判断。语义核读及采前32份v2进入条件另列。', '']
    (out / 'metrics.md').write_text('\n'.join(lines), encoding='utf-8')
    dump(out / 'report-sha256.json', {p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file()})
    return {key: result[key] for key in ('status', 'run_id', 'measured_completions', 'groups')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--out-dir', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.run_dir, args.out_dir), ensure_ascii=False))
