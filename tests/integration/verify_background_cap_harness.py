#!/usr/bin/env python3
"""No-GPU cap harness checks; synthetic cap logs never count as engine evidence."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/replay'))

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

runner = load('cap_harness_runner', ROOT / 'scripts/replay/78_run_background_cap.py')
gate = load('cap_harness_gate', ROOT / 'tests/integration/verify_background_cap_engine.py')


def cap_line(event, req_id, before, after, event_ms, cap=1, slot=0, category='background'):
    return (f'SCHED_CAP_{event} id={req_id} clientid={req_id} class={category} slot={slot} '
            f'running_background_before={before} running_background_after={after} '
            f'cap={cap} event_ms={event_ms}\n')


def verify(out, source_run):
    out.mkdir(parents=True, exist_ok=False)
    cfg = runner.read(ROOT / 'configs/prestudy-background-cap-v1.json')
    traces, indices = runner.make_traces(cfg)
    assert len(cfg['execution_order']) == cfg['max_measurement_runs'] == 18
    assert {(x['repeat'], x['scenario'], x['strategy']) for x in cfg['execution_order']} == {
        (r, s, p) for r in (1, 2, 3) for s in ('steady', 'burst') for p in ('original', 'simple', 'capped')}
    assert all(len(t) == 230 and sum(x['category'] == 'interactive' for x in t) == 200 for t in traces.values())
    assert indices == cfg['selected_background_indices']
    for strategy in ('original', 'simple', 'capped'):
        assert cfg['strategies'][strategy]['environment'] == runner.policy_environment(strategy, 5000)
    for scenario in ('steady', 'burst'):
        position = {p: [] for p in ('original', 'simple', 'capped')}
        for repeat in (1, 2, 3):
            group = [x for x in cfg['execution_order'] if x['scenario'] == scenario and x['repeat'] == repeat]
            for i, row in enumerate(group):
                position[row['strategy']].append(i)
        assert all(sorted(v) == [0, 1, 2] for v in position.values())
    good = [cap_line('LAUNCH', 'B', 0, 1, 10), cap_line('LAUNCH', 'I', 1, 1, 11, slot=1, category='interactive'),
            cap_line('RELEASE', 'I', 1, 1, 12, slot=1, category='interactive'), cap_line('RELEASE', 'B', 1, 0, 13)]
    path = out / 'synthetic-cap-events.log'
    path.write_text(''.join(good), encoding='utf-8')
    accounting = gate.verify_cap_events(path, 1)
    bad_cases = {
        'cap_overflow': [cap_line('LAUNCH', 'B0', 0, 1, 1), cap_line('LAUNCH', 'B1', 1, 2, 2, slot=1)],
        'orphan_release': [cap_line('RELEASE', 'B', 1, 0, 1)],
        'same_slot_overlap': [cap_line('LAUNCH', 'B0', 0, 1, 1), cap_line('LAUNCH', 'I', 1, 1, 2, category='interactive')],
        'clock_reversal': [cap_line('LAUNCH', 'B', 0, 1, 5), cap_line('RELEASE', 'B', 1, 0, 4)],
        'missing_release': [cap_line('LAUNCH', 'B', 0, 1, 1)],
        'wrong_delta': [cap_line('LAUNCH', 'B', 0, 0, 1)],
    }
    rejected = []
    for name, lines in bad_cases.items():
        path = out / (name + '.log')
        path.write_text(''.join(lines), encoding='utf-8')
        try:
            gate.verify_cap_events(path, 1)
        except (AssertionError, KeyError):
            rejected.append(name)
        else:
            raise AssertionError('Accepted invalid synthetic event stream: ' + name)
    fixture = out / 'synthetic-run-audit'
    shutil.copytree(source_run, fixture)
    # Add invented cap accounting to an old completed request cohort solely to
    # exercise integrity rejection. This is explicitly not real engine output.
    env = runner.policy_environment('capped', 5000)
    runner.dump(fixture / 'server-policy-environment.json', env)
    old_log = (fixture / 'server.log').read_text(encoding='utf-8')
    synthetic = ['\nSCHED_CONFIG mode=1 background_aging_ms=5000 max_running_background=1 preemptive=0\n']
    rows = runner.jsonlines(fixture / 'replay/client.jsonl')
    for i, row in enumerate(rows):
        after = int(row['category'] == 'background')
        synthetic += [cap_line('LAUNCH', row['req_id'], 0, after, 2*i, category=row['category']),
                      cap_line('RELEASE', row['req_id'], after, 0, 2*i+1, category=row['category'])]
    (fixture / 'server.log').write_text(old_log + ''.join(synthetic), encoding='utf-8')
    trace = runner.jsonlines(fixture / 'replay/trace.jsonl')
    audit = runner.audit_run(fixture, trace, cfg)
    assert audit['flags'] == [], audit
    (fixture / 'server.log').write_text(old_log + ''.join(synthetic) + cap_line('LAUNCH', 'extra0', 0, 1, 1000) +
                                      cap_line('LAUNCH', 'extra1', 1, 2, 1001, slot=1), encoding='utf-8')
    bad_audit = runner.audit_run(fixture, trace, cfg)
    assert 'background running cap violated' in bad_audit['flags'], bad_audit
    summary = {'status': 'passed', 'scope': 'Synthetic harness only, no GPU engine or measured policy performance',
               'matrix': 18, 'measurement_requests': 4140, 'warmups': 36,
               'synthetic_good_accounting': accounting, 'rejected_bad_streams': rejected,
               'synthetic_run_audit': audit, 'injected_overflow_flags': bad_audit['flags'],
               'historical_source_run': str(source_run.resolve()),
               'source_sha256': {p: runner.sha(ROOT / p) for p in
                                 ('scripts/replay/78_run_background_cap.py', 'tests/integration/verify_background_cap_engine.py',
                                  'tests/integration/verify_background_cap_harness.py', 'configs/prestudy-background-cap-v1.json')}}
    runner.dump(out / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--source-run', type=Path, default=ROOT / 'results/raw/prestudy/20261023-100000-p0-simple-policy-v2/r1-steady-original')
    args = parser.parse_args()
    verify(args.out_dir.resolve(), args.source_run.resolve())
