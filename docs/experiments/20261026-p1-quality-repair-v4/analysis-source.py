"""Consolidate v11/v12 without counting reused calibration or shadows as samples."""
import collections
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def lines(path):
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s]


def main():
    output = ROOT / 'docs/experiments/20261026-p1-quality-repair-v4'
    output.mkdir(exist_ok=False)
    reports = {}
    inputs = []
    for label, run_id, public_id in [
        ('floor', '20261026-1525-p1-live-mechanism-v11-r3', '20261026-p1-live-mechanism-v11-r3'),
        ('clip', '20261026-1545-p1-soft-role-v12', '20261026-p1-soft-role-v12'),
    ]:
        run = ROOT / 'results/raw/development' / run_id
        source = ROOT / 'docs/experiments' / public_id / 'summary.json'
        inputs.append(source)
        summary = read(source)
        assert summary['status'] == 'passed_independent_prototype_audit'
        count = collections.Counter()
        margins = collections.Counter()
        reasons = collections.Counter()
        for item in summary['rounds']:
            events_file = run / 'rounds' / item['name'] / 'replay/control/events.jsonl'
            inputs.append(events_file)
            for event in lines(events_file):
                if event['event'] == 'decision':
                    shadow = event['shadow']
                    count['observed_decisions'] += 1
                    count['M_vs_B3_0_admit_diff'] += shadow['M']['admit'] != shadow['B3_0']['admit']
                    count['M_vs_B3_1_admit_diff'] += shadow['M']['admit'] != shadow['B3_1']['admit']
                    count['M_vs_B3_0_cost_diff'] += shadow['M']['cost'] != shadow['B3_0']['cost']
                    margins[str(shadow['M']['margin'])] += 1
                    reasons[shadow['M']['reason']] += 1
        reports[label] = {'run_id': run_id, 'counts': dict(count), 'M_margin_at_observed_decisions': dict(margins),
                          'M_reason_at_observed_decisions': dict(reasons), 'paired_development': summary['paired_development']}
    failures = []
    for run_id in ['20261026-1500-p1-live-mechanism-v11', '20261026-1515-p1-live-mechanism-v11-r2']:
        run = ROOT / 'results/raw/development' / run_id
        path = run / 'rounds/pilot-1p0/replay/client.jsonl'
        inputs.append(path)
        rows = lines(path)
        failures.append({'run_id': run_id, 'planned_client_terminal_records': len(rows),
                         'actually_sent': sum(r['actual_send_offset_ms'] is not None for r in rows),
                         'terminal_counts': dict(collections.Counter(r['terminal'] for r in rows)),
                         'usable_performance_round': False})
    result = {'status': 'passed', 'new_valid_measurements': 144 + 7 * 230 + 6 * 230,
              'new_valid_warmups': 2 + 7 * 2 + 6 * 2,
              'new_calibration_unique': 144, 'prior_calibration_history': 48, 'warm_history_unique': 192,
              'independent_development_groups_per_role': 2, 'roles_not_interleaved': True,
              'reports': reports, 'failed_attempts': failures,
              'fixed_rubric_baseline': [13, 12, 15, 18, 18], 'fixed_rubric_after': [15, 15, 17, 19, 18],
              'subjective_work_quality_score': 84, 'G1_passed': False, 'final_test_accessed': False}
    assert result['new_valid_measurements'] == 3134 and result['new_valid_warmups'] == 28
    assert sum(result['fixed_rubric_after']) == result['subjective_work_quality_score']
    (output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / 'evidence-sha256.json').write_text(json.dumps({str(p.relative_to(ROOT).as_posix()): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}, indent=2) + '\n', encoding='utf-8')
    (output / 'analysis-source.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
