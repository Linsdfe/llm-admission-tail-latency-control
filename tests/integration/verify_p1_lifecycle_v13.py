"""Adversarial TCP/controller tests. These are not GPU measurements."""
import argparse
import asyncio
import errno
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/replay'))
import p1_controlled_replay_v13 as client
from p1_live_control_v13 import Controller, ControlIntegrityError


def history():
    return {(c, k): [0] * 32 for c in ('interactive', 'background') for k in ('extract', 'check', 'organize')}


def row(req, **extra):
    return dict(req_id=req, category='interactive', task_kind='extract', arrival_offset_ms=0,
                prompt='test', input_tokens=5, max_tokens=64, temperature=0, ignore_eos=False,
                cache_prompt=False, prediction=7, grammar_ceiling=None,
                input_cost_coefficient=1, output_cost_coefficient=10, **extra)


def rows(path):
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s]


async def scenario(name, directory, legacy=False):
    directory.mkdir()
    log = directory / 'synthetic-server.log'
    log.write_text('', encoding='utf-8')
    active = set()
    handlers = set()
    def event(kind, req):
        with log.open('a', encoding='utf-8') as f:
            f.write(f'SCHED_CAP_{kind} clientid={req} event_ms=0\n')
    async def handle(reader, writer):
        handlers.add(asyncio.current_task())
        req = None
        try:
            await reader.readline()
            headers = {}
            while True:
                line = await reader.readline()
                if line in (b'\r\n', b''):
                    break
                k, v = line.decode().split(':', 1)
                headers[k.lower()] = v.strip()
            body = json.loads(await reader.readexactly(int(headers['content-length'])))
            req = body['client_req_id']
            if req.endswith('-A') and name in ('http_503', 'http_400'):
                status = 503 if name == 'http_503' else 400
                payload = json.dumps({'error': {'type': 'exceed_context_size_error'}}).encode()
                writer.write(f'HTTP/1.1 {status} Error\r\nContent-Length: {len(payload)}\r\n\r\n'.encode() + payload)
                await writer.drain()
                return
            event('LAUNCH', req)
            active.add(req)
            await asyncio.sleep(.28 if req.endswith('-A') else .02)
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n')
            if name == 'truncated_sse' and req.endswith('-A'):
                value = {'content': 'partial'}
            else:
                value = {'stop': True, 'content': 'ok', 'stop_type': 'eos',
                         'tokens_evaluated': 5, 'timings': {'prompt_n': 5, 'cache_n': 0, 'prompt_ms': 1, 'predicted_ms': 2}}
                if name != 'unknown_length' or not req.endswith('-A'):
                    value['timings']['predicted_n'] = 8
            writer.write(('data: ' + json.dumps(value) + '\n\n').encode())
            try:
                await writer.drain()
            except (ConnectionError, BrokenPipeError):
                pass
            await asyncio.sleep(.02)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if req in active:
                active.remove(req)
                if name != 'missing_release':
                    event('RELEASE', req)
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            handlers.discard(asyncio.current_task())
    server = await asyncio.start_server(handle, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    async def idle():
        return not active
    controller_type = Controller
    replay = client
    if legacy:
        import p1_controlled_replay_v11 as replay
        from p1_live_control_v12 import Controller as controller_type
    ctl = controller_type('M', 100, history(), log, **({} if legacy else {'idle_probe': idle, 'reclamation_timeout': .65}))
    plan = [row(name + '-A')]
    expected = 'completed'
    duration, drain = .4, 5 if legacy else 2
    if name in ('queue_cancel', 'queue_timeout'):
        plan.append(row(name + '-B'))
        plan[1]['arrival_offset_ms'] = 10
        if name == 'queue_cancel':
            plan[1]['cancel_after_ms'] = 60
            expected = 'cancelled'
        else:
            plan[1]['timeout_seconds'] = .06
            expected = 'timeout'
    elif name in ('active_cancel', 'active_timeout'):
        plan[0]['cancel_after_ms' if name == 'active_cancel' else 'timeout_seconds'] = 80 if name == 'active_cancel' else .08
        expected = 'cancelled' if name == 'active_cancel' else 'timeout'
    elif name in ('http_503', 'http_400', 'local_capacity'):
        expected = 'rejected'
        if name == 'local_capacity':
            plan[0]['input_tokens'] = 2040
            plan[0]['timeout_seconds'] = .1
    elif name == 'connect_failure':
        expected = 'failed'
    elif name == 'truncated_sse':
        expected = 'failed'
    elif name == 'global_cutoff':
        duration, drain, expected = .03, .03, 'unfinished'
    if name == 'double_callback':
        complete = ctl.complete
        async def double_complete(r, record):
            await complete(r, record)
            await complete(r, record)
        ctl.complete = double_complete
    if name == 'transient_read':
        start = ctl.start
        async def start_with_fault(origin, out):
            await start(origin, out)
            original = ctl.capfile
            class FaultFile:
                first = True
                def seek(self, offset): return original.seek(offset)
                def read(self, size):
                    if self.first:
                        self.first = False
                        raise OSError(errno.ENODATA, 'No data available')
                    return original.read(size)
                def close(self): return original.close()
            ctl.capfile = FaultFile()
        ctl.start = start_with_fault
    async def refused(*args, **kwargs):
        raise ConnectionRefusedError('injected connection refusal before write')
    error = None
    try:
        try:
            if name == 'connect_failure':
                with patch.object(asyncio, 'open_connection', refused):
                    await replay.replay(plan, directory / 'primary', f'http://127.0.0.1:{port}', duration, drain, 1, 50, 2, controller=ctl)
            else:
                await replay.replay(plan, directory / 'primary', f'http://127.0.0.1:{port}', duration, drain, 1, 50, 2, controller=ctl)
        except ControlIntegrityError as exc:
            error = repr(exc)
        records = rows(directory / 'primary/client.jsonl')
        events = rows(directory / 'primary/control/events.jsonl')
        focus = next(r for r in records if r['req_id'].endswith('-B' if name.startswith('queue_') else '-A'))
        if legacy:
            assert focus['terminal'] != expected, 'legacy bug was not reproduced'
            return {'scenario': name, 'status': 'known_problem_reproduced', 'expected': expected,
                    'observed': focus['terminal'], 'fatal': ctl.fatal, 'recorded': len(records)}
        assert focus['terminal'] == expected, (name, focus)
        returns = [e for e in events if e['event'] == 'return_reservation']
        feedback = [e for e in events if e['event'] == 'feedback']
        assert len({e['req_id'] for e in returns}) == len(returns)
        assert len({e['req_id'] for e in feedback}) == len(feedback)
        for e in feedback:
            record = next(r for r in records if r['req_id'] == e['req_id'])
            assert record['terminal'] == 'completed' and record['output_tokens_known']
            release = next(x for x in events if x['event'] == 'native_release_seen' and x['req_id'] == e['req_id'])
            assert release['observed_ms'] <= e['observed_ms']
        if name == 'missing_release':
            assert error and ctl.fatal and len(ctl.active) == 1 and not returns and not feedback
            assert focus['terminal'] == 'completed' and focus['control_ok'] is False
            try:
                await ctl.acquire(row('must-not-be-admitted'))
            except RuntimeError:
                pass
            else:
                raise AssertionError('quarantine failed to block a new request')
            recovered = False
        else:
            assert error is None and not ctl.fatal and not ctl.pending and not ctl.active
            if name.startswith('queue_') or name == 'local_capacity':
                assert focus['actual_send_offset_ms'] is None
            if name in ('active_cancel', 'active_timeout', 'global_cutoff', 'truncated_sse'):
                assert focus['reclaim_proof'] == 'native_release' and not focus['output_tokens_known']
            if name in ('http_503', 'http_400'):
                assert focus['reclaim_proof'] == 'http_rejection_and_exclusive_idle'
            if name == 'connect_failure':
                assert focus['reclaim_proof'] == 'not_sent'
            if name == 'unknown_length':
                assert not feedback and not focus['output_tokens_known']
            if name == 'transient_read':
                assert ctl.empty_read_retries == 1
            # Fresh controller, same synthetic server; primary ledger is already clear.
            recover = controller_type('M', 100, history(), log, idle_probe=idle)
            recovery = await replay.replay([row(name + '-recovery')], directory / 'recovery',
                         f'http://127.0.0.1:{port}', .05, 2, 1, 50, 2, controller=recover)
            assert recovery['terminal_counts']['completed'] == 1
            recovered = True
        return {'scenario': name, 'status': 'passed', 'transport_terminal': focus['terminal'],
                'recorded': len(records), 'reclaim_proof': focus.get('reclaim_proof'),
                'returns': len(returns), 'feedback': len(feedback), 'same_server_recovery': recovered,
                'expected_quarantine': name == 'missing_release'}
    finally:
        server.close()
        await server.wait_closed()
        if handlers:
            await asyncio.gather(*handlers, return_exceptions=True)


async def run(out, legacy=False):
    out.mkdir(parents=True, exist_ok=False)
    files = ['tests/integration/verify_p1_lifecycle_v13.py', 'scripts/replay/p1_live_control_v13.py',
             'scripts/replay/p1_controlled_replay_v13.py', 'scripts/replay/p1_live_control_v12.py',
             'scripts/replay/p1_controlled_replay_v11.py']
    hashes = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in files}
    for p in files:
        target = out / 'source' / p
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / p).read_bytes())
    (out / 'source-sha256.json').write_text(json.dumps(hashes, indent=2), encoding='utf-8')
    names = ['http_503', 'local_capacity'] if legacy else [
        'normal', 'queue_cancel', 'queue_timeout', 'active_cancel', 'active_timeout',
        'http_503', 'http_400', 'connect_failure', 'truncated_sse', 'global_cutoff',
        'local_capacity', 'double_callback', 'unknown_length', 'transient_read', 'missing_release']
    try:
        results = [await scenario(name, out / name, legacy) for name in names]
    except BaseException:
        (out / 'error-traceback.txt').write_text(traceback.format_exc(), encoding='utf-8')
        raise
    result = {'status': 'known_problems_reproduced' if legacy else 'passed',
              'scope': 'synthetic engineering tests, not GPU measurements', 'scenarios': results,
              'source_sha256': hashes}
    (out / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--out-dir', type=Path, required=True)
    p.add_argument('--legacy', action='store_true')
    a = p.parse_args()
    asyncio.run(run(a.out_dir, a.legacy))
