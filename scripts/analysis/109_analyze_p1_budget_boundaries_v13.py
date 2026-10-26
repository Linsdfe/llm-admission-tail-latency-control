"""Exhaust all budget intervals on recorded states, without counterfactual runs."""
import argparse
from collections import Counter
import gc
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/replay'))
from p1_live_control_v12 import estimate as clip_estimate, quantile, reserve
from p1_live_control_v11 import estimate as floor_estimate


def read(p): return json.loads(p.read_text(encoding='utf-8'))
def rows(p): return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def component(row, history, policy):
    margin = max(0, math.ceil(quantile(history))) if policy == 'M' else 1 if policy == 'B3_1' else 0
    return clip_estimate(row, margin)


def analyze(out):
    out.mkdir(parents=True, exist_ok=False)
    evidence, aggregate, intervals, points = {}, {}, [], []
    checks = 0
    def check(value, label):
        nonlocal checks
        assert value, label
        checks += 1
    for role, run_id, report_id, estimate in [
        ('floor', '20261026-1525-p1-live-mechanism-v11-r3', '20261026-p1-live-mechanism-v11-r3', floor_estimate),
        ('clip', '20261026-1545-p1-soft-role-v12', '20261026-p1-soft-role-v12', clip_estimate),
    ]:
        run = ROOT / 'results/raw/development' / run_id
        report = ROOT / 'docs/experiments' / report_id / 'summary.json'
        evidence[report.relative_to(ROOT).as_posix()] = sha(report)
        count = Counter()
        for item in read(report)['rounds']:
            folder = run / 'rounds' / item['name'] / 'replay'
            event_file, plan_file = folder / 'control/events.jsonl', folder / 'trace.jsonl'
            evidence[event_file.relative_to(ROOT).as_posix()] = sha(event_file)
            evidence[plan_file.relative_to(ROOT).as_posix()] = sha(plan_file)
            plan = {r['req_id']: r for r in rows(plan_file)}
            history = None
            for event in rows(event_file):
                if event['event'] == 'initial':
                    history = {k: list(v) for k, v in event['history'].items()}
                    budget = event['budget']
                elif event['event'] == 'feedback':
                    history[event['category'] + ':' + event['task_kind']].append(event['error'])
                elif event['event'] == 'decision':
                    row = plan[event['req_id']]
                    h = history[row['category'] + ':' + row['task_kind']][-32:]
                    margin = max(0, math.ceil(quantile(h)))
                    m, b = estimate(row, margin), estimate(row, 0)
                    check(m[1] == event['shadow']['M']['cost'] and b[1] == event['shadow']['B3_0']['cost'], 'independent real-history costs')
                    count['observed_decisions'] += 1
                    count['locked_budget_admit_differences'] += event['shadow']['M']['admit'] != event['shadow']['B3_0']['admit']
                    if role == 'clip':
                        points.append((row, tuple(h)))
                    if m[1] == b[1]:
                        count['equal_cost'] += 1
                        continue
                    count['different_cost'] += 1
                    active = event['active']
                    hard = len(active) < 2 and reserve(row) <= 2048 and sum(v['reservation'] for v in active.values()) + reserve(row) <= 4096
                    aged = row['category'] == 'background' and event['decision_clock_ms'] - row['arrival_offset_ms'] >= 5000
                    if not hard:
                        count['masked_hard'] += 1
                        continue
                    if not active:
                        count['masked_empty'] += 1
                        continue
                    if aged:
                        count['masked_aging'] += 1
                        continue
                    used = sum(v['cost'] for v in active.values())
                    check(used == event['used_cost'], 'recorded outstanding cost')
                    low, high = used + min(m[1], b[1]), used + max(m[1], b[1])
                    check(high > low, 'nonempty exact interval')
                    def differs(q): return (used + m[1] <= q) != (used + b[1] <= q)
                    check(not differs(math.nextafter(low, -math.inf)), 'below interval')
                    check(differs(low), 'inclusive lower boundary')
                    check(differs((low + high) / 2), 'interior witness')
                    check(not differs(high), 'exclusive upper boundary')
                    check(differs(budget) == (event['shadow']['M']['admit'] != event['shadow']['B3_0']['admit']), 'actual budget Boolean')
                    count['eligible_intervals'] += 1
                    intervals.append({'role': role, 'round': item['name'], 'req_id': row['req_id'],
                         'decision_clock_ms': event['decision_clock_ms'], 'margin': margin,
                         'low_inclusive': low, 'high_exclusive': high, 'width_proxy_ms': high - low,
                         'width_over_locked_budget': (high - low) / budget, 'locked_budget': budget,
                         'distance_from_locked_budget': 0 if low <= budget < high else min(abs(budget-low), abs(budget-high)),
                         'contains_locked_budget': low <= budget < high})
        aggregate[role] = dict(count)
    # Separate component measurements: nine blocks, cyclic balanced order, each
    # recorded state repeated32 times. No HTTP/model/shadow/logging is measured.
    timing = []
    for block in range(9):
        policies = ['B3_0', 'B3_1', 'M']
        policies = policies[block % 3:] + policies[:block % 3]
        for policy in policies:
            enabled = gc.isenabled()
            gc.disable()
            checksum = 0.0
            try:
                cpu, wall = time.process_time_ns(), time.perf_counter_ns()
                for _ in range(32):
                    for row, h in points:
                        _, cost = component(row, h, policy)
                        checksum += cost
                cpu, wall = time.process_time_ns() - cpu, time.perf_counter_ns() - wall
            finally:
                if enabled:
                    gc.enable()
            timing.append({'block': block, 'policy': policy, 'calls': len(points) * 32,
                           'cpu_ns_per_call': cpu / (len(points) * 32), 'wall_ns_per_call': wall / (len(points) * 32), 'checksum': checksum})
    benchmark = {'scope': 'Python isolated length/margin component; excludes history update, queue selection, I/O, shadows and inference',
                 'python': sys.version, 'platform': platform.platform(), 'recorded_states': len(points), 'blocks': timing,
                 'median_cpu_ns_per_call': {p: statistics.median(t['cpu_ns_per_call'] for t in timing if t['policy'] == p) for p in ('B3_0', 'B3_1', 'M')}}
    result = {'status': 'passed', 'checks': checks, 'roles': aggregate, 'new_GPU_measurements': 0,
              'scope': 'retrospective complete conditional budget intervals, not a budget search or counterfactual queue performance',
              'eligible_intervals': len(intervals),
              'width_proxy_ms_range': [min(x['width_proxy_ms'] for x in intervals), max(x['width_proxy_ms'] for x in intervals)] if intervals else None,
              'width_over_budget_range': [min(x['width_over_locked_budget'] for x in intervals), max(x['width_over_locked_budget'] for x in intervals)] if intervals else None,
              'closest_distance_from_locked_budget': min((x['distance_from_locked_budget'] for x in intervals), default=None),
              'benchmark_median_cpu_ns_per_call': benchmark['median_cpu_ns_per_call'],
              'independent_new_samples': 0, 'final_test_accessed': False, 'G1_passed': False}
    for name, value in [('summary.json', result), ('intervals.json', intervals), ('component-benchmark.json', benchmark), ('evidence-sha256.json', evidence)]:
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / 'analysis-source.py').write_bytes(Path(__file__).read_bytes())
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--out-dir', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(analyze(a.out_dir), ensure_ascii=False))
