"""Bounded native-model lifecycle correctness run; no scheduling benefit test."""
import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time
import traceback
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/replay'))
spec = importlib.util.spec_from_file_location('lifecycle_v8_helpers', ROOT / 'scripts/replay/79j_run_p1_target_mix_v8.py')
v8 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v8)
base = v8.base
import p1_controlled_replay_v13 as client
from p1_live_control_v13 import Controller

PARENT = ROOT / 'results/raw/development/20261026-1500-p1-live-mechanism-v11'
NEW = ['scripts/replay/79q_run_p1_lifecycle_v13.py', 'scripts/replay/p1_live_control_v13.py',
       'scripts/replay/p1_controlled_replay_v13.py', 'scripts/replay/p1_live_control_v12.py',
       'scripts/replay/p1_live_control_v11.py', 'scripts/replay/p1_controlled_replay_v11.py',
       'tests/integration/verify_p1_lifecycle_v13.py', 'tests/integration/audit_p1_lifecycle_v13.py',
       'docs/p1/lifecycle-and-boundary-protocol-v13.md', 'configs/p1-live-mechanism-v11.json']


async def collect(out, engine):
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    server = log = tokenizer = None
    try:
        gate = base.read(ROOT / 'results/raw/maintenance/20261026-p1-lifecycle-preparation-v13/synthetic-wsl-v2/summary.json')
        assert gate['status'] == 'passed' and len(gate['scenarios']) == 15
        assert all(base.prior.sha(ROOT / p) == h for p, h in gate['source_sha256'].items())
        hashes = v8.frozen_sources()
        hashes.update({p: base.prior.sha(ROOT / p) for p in v8.NEW_SOURCES + NEW})
        for name, digest in hashes.items():
            assert base.prior.sha(ROOT / name) == digest
            target = out / 'source' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / name).read_bytes())
        base.dump(out / 'source-sha256.json', hashes)
        cfg = base.read(ROOT / 'configs/p1-live-mechanism-v11.json')
        model = Path(cfg['model_path'])
        assert await asyncio.to_thread(base.prior.sha, model) == cfg['model_sha256']
        runtime = base.prior.binary_inventory(engine)
        assert runtime == base.read(v8.RUN7 / 'engine-runtime-sha256.json')
        engine_sources = base.read(v8.RUN7 / 'engine-source-sha256.json')
        assert all(base.prior.sha(engine.parents[2] / p) == h for p, h in engine_sources.items())
        assert shutil.disk_usage(out).free >= 10 * 1024**3
        base.dump(out / 'environment.json', {'started_at': datetime.now().astimezone().isoformat(),
                  'model_sha256': cfg['model_sha256'], 'engine_runtime_sha256': runtime,
                  'engine_source_sha256': engine_sources, 'scope': 'exclusive native lifecycle correctness',
                  'G1_passed': False, 'final_test_accessed': False})
        server, log = await v8.start(out, engine, model, 'original')
        tokenizer = base.Tokenizer(out)
        old = base.rows(PARENT / 'public-plan.jsonl')
        prototypes = {}
        for category in ('interactive', 'background'):
            proto = next(r for r in old if r['seed'] == 32101 and r['task_kind'] == 'extract' and r['category'] == category)
            tokens = await asyncio.to_thread(tokenizer, proto['prompt'])
            assert len(tokens) == proto['input_tokens'] and len(tokens) + proto['max_tokens'] + 1 <= 2048
            prototypes[category] = dict(proto)
        tokenizer.close()
        tokenizer = None
        warm = base.rows(ROOT / 'results/raw/development/20261026-0845-p1-target-mix-v8-r2/warmup-plan.jsonl')
        await v8.warm(out, warm)
        sources = base.rows(PARENT / 'warm-history-sources.jsonl')
        history = defaultdict(list)
        for r in sources:
            history[(r['category'], r['task_kind'])].append(r['error'])
        assert len(history) == 6 and all(len(h) == 32 for h in history.values())
        base.write_rows(out / 'reused-warm-history.jsonl', sources)
        artifact = base.read(PARENT / 'calibration-artifact-before-development.json')
        base.dump(out / 'reused-inputs.json', {str(p.relative_to(ROOT)): base.prior.sha(p) for p in [PARENT / 'public-plan.jsonl', PARENT / 'warm-history-sources.jsonl', PARENT / 'calibration-artifact-before-development.json']})
        async def idle_probe():
            def fetch():
                with urllib.request.urlopen(base.prior.URL + '/slots', timeout=1) as response:
                    slots = json.load(response)
                with (out / 'exclusive-idle-probes.jsonl').open('a', encoding='utf-8') as f:
                    f.write(json.dumps({'observed_wall': datetime.now().astimezone().isoformat(), 'owned_pid': server.pid, 'slots': slots}) + '\n')
                return len(slots) == 2 and all(s['is_processing'] is False for s in slots)
            return await asyncio.to_thread(fetch)
        def make(name, category='interactive'):
            row = dict(prototypes[category], req_id=name, arrival_offset_ms=0, cache_prompt=False,
                       input_cost_coefficient=artifact['input_cost_coefficient'],
                       output_cost_coefficient=artifact['output_cost_coefficient'])
            return row
        definitions = [('normal', 'completed'), ('queue_cancel', 'cancelled'), ('queue_timeout', 'timeout'),
                       ('active_cancel', 'cancelled'), ('active_timeout', 'timeout'),
                       ('local_capacity', 'rejected'), ('http_400', 'failed'), ('global_cutoff', 'unfinished')]
        scenarios = []
        all_records = []
        required_ids = {'warm0', 'warm1'}
        all_ids = set(required_ids)
        for name, expected in definitions:
            assert time.monotonic() - started < 600
            assert all(base.prior.sha(ROOT / p) == h for p, h in hashes.items())
            folder = out / 'cases' / name
            folder.mkdir(parents=True)
            background = name in ('queue_cancel', 'queue_timeout', 'active_cancel', 'active_timeout', 'global_cutoff')
            plan = [make(name + '-A', 'background' if background else 'interactive')]
            measurement, drain = .5, 10
            if name.startswith('queue_'):
                other = make(name + '-B')
                other['arrival_offset_ms'] = 10
                other['cancel_after_ms' if name == 'queue_cancel' else 'timeout_seconds'] = 80 if name == 'queue_cancel' else .08
                plan.append(other)
            elif name.startswith('active_'):
                plan[0]['cancel_after_ms' if name == 'active_cancel' else 'timeout_seconds'] = 150 if name == 'active_cancel' else .15
            elif name == 'local_capacity':
                plan[0]['input_tokens'] = 2040  # Deliberate pre-send capacity negative fixture.
            elif name == 'http_400':
                plan[0]['grammar'] = 'root ::= ['
            elif name == 'global_cutoff':
                measurement, drain = .05, .05
            base.write_rows(folder / 'plan.jsonl', plan)
            ctl = Controller('M', 100, history, out / 'server.log', idle_probe=idle_probe)
            await client.replay(plan, folder / 'primary', base.prior.URL, measurement, drain, 10, 50, 20, controller=ctl)
            actual = base.rows(folder / 'primary/client.jsonl')
            focus = next(r for r in actual if r['req_id'].endswith('-B' if name.startswith('queue_') else '-A'))
            assert focus['terminal'] == expected and all(r['control_ok'] for r in actual), (name, focus)
            if name.startswith('queue_') or name == 'local_capacity':
                assert focus['actual_send_offset_ms'] is None
            if name in ('active_cancel', 'active_timeout', 'global_cutoff'):
                assert focus['actual_send_offset_ms'] is not None and focus['reclaim_proof'] == 'native_release'
            await base.await_idle(folder, 'after-primary')
            recovery_plan = [make(name + '-recovery')]
            recover = Controller('M', 100, history, out / 'server.log', idle_probe=idle_probe)
            result = await client.replay(recovery_plan, folder / 'recovery', base.prior.URL, .5, 10, 10, 50, 20, controller=recover)
            assert result['terminal_counts']['completed'] == 1 and not recover.fatal
            await base.await_idle(folder, 'after-recovery')
            records = actual + base.rows(folder / 'recovery/client.jsonl')
            all_records += records
            all_ids.update(r['req_id'] for r in records)
            required_ids.update(r['req_id'] for r in records if r['actual_send_offset_ms'] is not None and r['http_status'] != 400)
            item = {'name': name, 'expected_terminal': expected, 'observed_terminal': focus['terminal'],
                    'recorded': len(records), 'same_server_recovery': True, 'owned_pid': server.pid}
            scenarios.append(item)
            base.dump(folder / 'manifest.json', item)
            base.dump(out / 'batch-status.json', {'status': 'running', 'completed_scenarios': len(scenarios), 'last': item})
            print('SCENARIO ' + json.dumps(item), flush=True)
        accounting = await v8.finish(out, server, log, sorted(all_ids), 'original', sorted(required_ids))
        log = None
        assert all(base.prior.sha(ROOT / p) == h for p, h in hashes.items())
        assert base.prior.binary_inventory(engine) == runtime and await asyncio.to_thread(base.prior.sha, model) == cfg['model_sha256']
        assert all(base.prior.sha(engine.parents[2] / p) == h for p, h in engine_sources.items())
        base.dump(out / 'summary.json', {'status': 'completed', 'correctness_requests': len(all_records), 'warmups': 2,
                  'terminal_counts': dict(Counter(r['terminal'] for r in all_records)), 'scenarios': scenarios,
                  'accounting': accounting, 'elapsed_seconds': time.monotonic() - started,
                  'service_exit_zero': True, 'source_model_runtime_unchanged': True,
                  'G1_passed': False, 'final_test_accessed': False, 'finished_at': datetime.now().astimezone().isoformat()})
    except BaseException:
        (out / 'error-traceback.txt').write_text(traceback.format_exc(), encoding='utf-8')
        raise
    finally:
        if tokenizer is not None:
            tokenizer.close()
        if server is not None and log is not None:
            await base.prior.prior.engine.stop_server(server, log)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--out-dir', type=Path, required=True)
    p.add_argument('--engine-bin', type=Path, required=True)
    a = p.parse_args()
    asyncio.run(asyncio.wait_for(collect(a.out_dir, a.engine_bin.resolve(strict=True)), 900))
