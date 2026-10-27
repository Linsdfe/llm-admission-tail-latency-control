"""Audit content-selection facts, public alternatives and native measurements."""
import argparse
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import statistics


def read(p): return json.loads(p.read_text(encoding='utf-8'))
def rows(p): return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s]


def audit(run):
    summary = read(run / 'summary.json')
    assert summary['status'] == 'completed' and summary['measured_requests'] == 48 and summary['warmups'] == 2
    checks = 0
    def check(value, label):
        nonlocal checks
        assert value, label
        checks += 1
    for name, digest in read(run / 'source-sha256.json').items():
        check(hashlib.sha256((run / 'source' / name).read_bytes()).hexdigest() == digest, 'execution source')
    refs = rows(run / 'evaluation-plan.jsonl')
    public = {r['req_id']: r for r in rows(run / 'public-plan.jsonl')}
    features = {r['req_id']: r for r in rows(run / 'public-features-before-data.jsonl')}
    tokenized = {}
    for r in rows(run / 'public-output-tokenization.jsonl'):
        check(r['request']['add_special'] is False and r['request']['parse_special'] is False, 'literal output tokenize')
        tokens = json.loads(r['response_body'])['tokens']
        check(r['token_count'] == len(tokens) and all(type(t) is int for t in tokens), 'actual token response')
        tokenized.setdefault(r['req_id'], {})[r['request']['content']] = len(tokens)
    results = []
    for index, ref in enumerate(refs, 1):
        r = rows(run / 'cases' / f'q{index:03d}' / 'replay/client.jsonl')[0]
        req = ref['req_id']
        check(r['req_id'] == req and r['prompt'] == public[req]['prompt'] == ref['prompt'], 'exact source request')
        check(not {'expected_ids','expected_answer','facts_evaluation_only','target_count_evaluation_only'} & public[req].keys(), 'no oracle payload')
        check(hashlib.sha256(ref['prompt'].encode('utf-8')).hexdigest() == ref['content_sha256'], 'prompt hash')
        check(r['terminal'] == 'completed' and r['output_tokens_known'] and r['finish_reason'] == 'stop', 'natural complete')
        check(r['cached_prompt_tokens'] == 0 and r['output_tokens'] <= r['max_tokens'], 'no cache or limit')
        check(r['input_tokens'] + r['max_tokens'] + 1 <= 2048 and r['send_lag_ms'] <= 50, 'context and lag')
        expected = sorted(f['id'] for f in ref['facts_evaluation_only'] if f['current_down'])
        check(expected == ref['expected_ids'], 'independent latent truth')
        text = '\n'.join(expected) if expected else '无'
        check(text == ref['expected_answer'], 'oracle rendering')
        ids = sorted(f['id'] for f in ref['facts_evaluation_only'])
        choices = {'无'} | {'\n'.join(group) for n in range(1, len(ids)+1) for group in itertools.combinations(ids, n)}
        check(set(tokenized[req]) == choices and r['content'] in choices, 'all public subsets, not true subset only')
        f = features[req]
        check(f['rule_ids'] == expected and f['rule_output_tokens'] == tokenized[req][text]+1, 'public rule independent truth agreement')
        check(f['grammar_max_output_tokens'] == max(tokenized[req].values())+1, 'public maximum')
        correct = r['content'] == text
        observed = [] if r['content'] == '无' else r['content'].splitlines()
        results.append({'req_id': req, 'category': ref['category'], 'seed': ref['seed'], 'prompt': ref['prompt'],
                        'content': r['content'], 'expected_answer': text, 'whole_correct': correct,
                        'missing': sorted(set(expected)-set(observed)), 'extra': sorted(set(observed)-set(expected)),
                        'expected_count': len(expected), 'observed_count': len(observed),
                        'input_tokens': r['input_tokens'], 'output_tokens': r['output_tokens'],
                        'rule_output_tokens': f['rule_output_tokens'], 'rule_error': r['output_tokens']-f['rule_output_tokens'],
                        'grammar_max_output_tokens': f['grammar_max_output_tokens'],
                        'ttft_ms': r['ttft_ms'], 'native_eos_offset': r['output_tokens']-tokenized[req][r['content']]})
    check(len(results) == len({r['req_id'] for r in results}) == 48, 'all48 unique')
    config = read(run / 'source/configs/p1-content-selection-profile-v14.json')
    classes = {}
    for category in ('interactive','background'):
        group = [r for r in results if r['category'] == category]
        good = [r for r in group if r['whole_correct']]
        lengths = sorted({r['output_tokens'] for r in good if r['expected_count'] > 0})
        classes[category] = {'total': len(group), 'correct': len(good),
            'input_tokens_range': [min(r['input_tokens'] for r in group),max(r['input_tokens'] for r in group)],
            'output_tokens_range': [min(r['output_tokens'] for r in group),max(r['output_tokens'] for r in group)],
            'correct_nonempty_output_token_values': lengths,
            'correct_expected_count_histogram': dict(Counter(r['expected_count'] for r in good)),
            'rule_MAE_all': statistics.mean(abs(r['rule_error']) for r in group),
            'rule_MAE_correct_only': statistics.mean(abs(r['rule_error']) for r in good) if good else None}
    quality = sum(r['whole_correct'] for r in results) >= config['minimum_correct_total'] and all(c['correct'] >= config['minimum_correct_per_class'] for c in classes.values())
    variation = all(len(c['correct_nonempty_output_token_values']) >= config['minimum_correct_nonempty_output_lengths_per_class'] for c in classes.values())
    return {'status':'passed_technical_and_evidence_audit','checks':checks,'measured_requests':48,'warmups':2,
            'correct':sum(r['whole_correct'] for r in results),'classes':classes,
            'task_quality_gate_passed':quality,'natural_length_gate_passed':variation,
            'task_entry_passed':quality and variation,'public_rule_exact_length_all':sum(r['rule_error']==0 for r in results),
            'public_rule_exact_length_correct_only':sum(r['rule_error']==0 and r['whole_correct'] for r in results),
            'public_rule_solves_reference_task':True,'scheduler_comparison_started':False,
            'new_independent_system_repetitions':0,'G1_passed':False,'final_test_accessed':False,'request_results':results}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a = p.parse_args()
    result = audit(a.run_dir)
    with a.out.open('x',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k != 'request_results'},ensure_ascii=False))
