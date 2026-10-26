"""Recompute interval geometry directly from preserved decision records."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[2]


def read(p): return json.loads(p.read_text(encoding='utf-8'))


def audit(folder):
    summary = read(folder / 'summary.json')
    intervals = read(folder / 'intervals.json')
    checks = 0
    def check(value, label):
        nonlocal checks
        assert value, label
        checks += 1
    events, count = {}, Counter()
    for name, digest in read(folder / 'evidence-sha256.json').items():
        p = ROOT / name
        check(hashlib.sha256(p.read_bytes()).hexdigest() == digest, 'preserved input')
        if not name.endswith('/control/events.jsonl'):
            continue
        for line in p.read_text(encoding='utf-8').splitlines():
            e = json.loads(line)
            if e['event'] != 'decision':
                continue
            m, b = e['shadow']['M'], e['shadow']['B3_0']
            count['all'] += 1
            check(m['admit'] == b['admit'], 'observed same-state decisions equal')
            if m['cost'] != b['cost']:
                count['different_cost'] += 1
                if not e['active']:
                    count['empty_masked'] += 1
                else:
                    key = (p.parents[2].name, e['req_id'], e['decision_clock_ms'])
                    check(key not in events, 'unique interval state')
                    events[key] = e
    check(count == Counter(all=4064, different_cost=164, empty_masked=30), 'full factual counts')
    check(len(intervals) == len(events) == 134, 'all eligible states retained')
    seen = set()
    for r in intervals:
        key = (r['round'], r['req_id'], r['decision_clock_ms'])
        check(key not in seen, 'no repeated interval')
        seen.add(key)
        e = events[key]
        used = sum(v['cost'] for v in e['active'].values())
        low = used + min(e['shadow']['M']['cost'], e['shadow']['B3_0']['cost'])
        high = used + max(e['shadow']['M']['cost'], e['shadow']['B3_0']['cost'])
        check(low == r['low_inclusive'] and high == r['high_exclusive'], 'exact boundaries')
        check(math.isclose(high-low, r['width_proxy_ms'], rel_tol=1e-12), 'width')
        check(not (low <= r['locked_budget'] < high) and not r['contains_locked_budget'], 'locked budget outside interval')
        check(min(abs(r['locked_budget']-low), abs(r['locked_budget']-high)) == r['distance_from_locked_budget'], 'distance')
    bench = read(folder / 'component-benchmark.json')
    check(len(bench['blocks']) == 27 and bench['recorded_states'] == 1871, 'finite benchmark blocks')
    timing = {}
    for policy in ('B3_0', 'B3_1', 'M'):
        values = [b for b in bench['blocks'] if b['policy'] == policy]
        check(len(values) == 9 and {v['calls'] for v in values} == {59872}, 'equal component calls')
        check(len({v['checksum'] for v in values}) == 1, 'same calculation result across blocks')
        data = [b['wall_ns_per_call'] / 1000 for b in values]
        timing[policy] = {'median_us': statistics.median(data), 'min_us': min(data), 'max_us': max(data)}
    return {'status': 'passed', 'checks': checks, 'observed_states': 4064, 'different_cost': 164,
            'masked_empty': 30, 'complete_intervals': 134, 'all_intervals_miss_locked_budget': True,
            'component_wall_clock_us': timing,
            'limits': ['Intervals hold the recorded state fixed; no changed-budget queue performance claim.',
                       'Microbenchmark times only Python length/margin calculations; no C++ or end-to-end cost claim.',
                       'Nine benchmark blocks are not nine GPU performance repetitions.'],
            'new_GPU_measurements': 0, 'G1_passed': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--folder', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    result = audit(a.folder)
    with a.out.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))
