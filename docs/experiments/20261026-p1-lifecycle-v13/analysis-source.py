"""Independent native lifecycle evidence audit, without model performance claims."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def read(p): return json.loads(p.read_text(encoding='utf-8'))
def rows(p): return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s]


def audit(run):
    summary = read(run / 'summary.json')
    assert summary['status'] == 'completed' and summary['correctness_requests'] == 18
    assert summary['warmups'] == 2 and summary['service_exit_zero']
    checks = 0
    def check(value, label):
        nonlocal checks
        assert value, label
        checks += 1
    for name, digest in read(run / 'source-sha256.json').items():
        check(hashlib.sha256((run / 'source' / name).read_bytes()).hexdigest() == digest, 'source snapshot ' + name)
    counts, output = Counter(), []
    all_sent = set()
    http_ids = set()
    for item in summary['scenarios']:
        folder = run / 'cases' / item['name']
        for phase in ('primary', 'recovery'):
            path = folder / phase
            actual = rows(path / 'client.jsonl')
            by_id = {r['req_id']: r for r in actual}
            check(len(by_id) == len(actual), 'unique records')
            events = rows(path / 'control/events.jsonl')
            active, released, returned, feedback = {}, set(), set(), set()
            for event in events:
                req = event.get('req_id')
                if event['event'] == 'native_release_seen':
                    released.add(req)
                elif event['event'] == 'permit':
                    check(req not in active and event['reservation'] <= 2048, 'single permit and slot limit')
                    active[req] = event['reservation']
                    check(len(active) <= 2 and sum(active.values()) <= 4096, 'hard bound')
                elif event['event'] == 'return_reservation':
                    check(req in active and req not in returned and active[req] == event['reservation'], 'once return')
                    if event['proof'] == 'native_release':
                        check(req in released, 'release before return')
                    else:
                        r = by_id[req]
                        check(event['proof'] == 'http_rejection_and_exclusive_idle' and r['http_status'] == 400, 'explicit HTTP rejection proof')
                        check(req not in released, 'no invented CAP release')
                    active.pop(req)
                    returned.add(req)
                elif event['event'] == 'feedback':
                    r = by_id[req]
                    check(req in returned and req in released and req not in feedback, 'once release then feedback')
                    check(r['terminal'] == 'completed' and r['output_tokens_known'], 'only known valid completion')
                    check(event['actual'] == r['output_tokens'] and event['error'] == max(0, r['output_tokens'] - r['prediction']), 'actual error')
                    check(event['http_end_ms'] <= event['observed_ms'], 'no future feedback')
                    feedback.add(req)
                elif event['event'] == 'final':
                    check(not active and not event['active'] and not event['pending'] and not event['fatal'], 'clean final state')
            for r in actual:
                counts[r['terminal']] += 1
                check(r['control_ok'] and not r.get('control_error'), 'no control error')
                check((r['terminal'] == 'completed') == (r['req_id'] in feedback), 'non-success never feedback')
                if r['actual_send_offset_ms'] is not None:
                    all_sent.add(r['req_id'])
                    check(r['send_lag_ms'] <= 50, 'release to send lag')
                if r['reclaim_proof'] == 'native_release':
                    check(r['native_release_confirm_offset_ms'] >= r['end_offset_ms'], 'HTTP ended before reclamation')
                if r['http_status'] == 400:
                    http_ids.add(r['req_id'])
                output.append({'scenario': item['name'], 'phase': phase, **r})
            if phase == 'recovery':
                check(len(actual) == 1 and actual[0]['terminal'] == 'completed', 'same service recovery')
        primary = rows(folder / 'primary/client.jsonl')
        focus = next(r for r in primary if r['req_id'].endswith('-B' if item['name'].startswith('queue_') else '-A'))
        check(focus['terminal'] == item['expected_terminal'], 'registered terminal')
        if item['name'] in ('queue_cancel', 'queue_timeout', 'local_capacity'):
            check(focus['actual_send_offset_ms'] is None and focus['reclaim_proof'] == 'no_reservation', 'must not send')
        if item['name'] in ('active_cancel', 'active_timeout', 'global_cutoff'):
            check(focus['actual_send_offset_ms'] is not None and focus['reclaim_proof'] == 'native_release', 'active abort reclaimed')
            check(not focus['output_tokens_known'], 'unknown aborted tokens not zero filled')
    check(counts == Counter(completed=11, rejected=1, timeout=2, cancelled=2, failed=1, unfinished=1), 'all six states exact')
    native = []
    for line in (run / 'server.log').read_text(encoding='utf-8', errors='replace').splitlines():
        if 'SCHED_CAP_LAUNCH' in line or 'SCHED_CAP_RELEASE' in line:
            fields = dict(x.split('=', 1) for x in line.split() if '=' in x)
            native.append(('release' if 'SCHED_CAP_RELEASE' in line else 'launch', fields['clientid']))
    launches = Counter(req for event, req in native if event == 'launch')
    releases = Counter(req for event, req in native if event == 'release')
    check(launches == releases and all(n == 1 for n in launches.values()), 'native one launch one release')
    check(all_sent - http_ids <= set(launches), 'every active abort really launched')
    check(len(all_sent) == 15 and len(output) == 18, 'actual sends and records')
    check(not summary['final_test_accessed'] and not summary['G1_passed'], 'scope')
    return {'status': 'passed', 'checks': checks, 'correctness_requests': 18, 'warmups': 2,
            'actual_sent': len(all_sent), 'terminal_counts': dict(counts), 'same_server_recoveries': 8,
            'native_launches': len(launches), 'native_releases': len(releases), 'request_results': output,
            'scope': 'native GPU lifecycle correctness; no performance or final-test claim', 'G1_passed': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    result = audit(a.run_dir)
    with a.out.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != 'request_results'}, ensure_ascii=False))
