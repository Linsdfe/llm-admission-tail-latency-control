#!/usr/bin/env python3
"""Collect one verifiable-task v3 pilot; expected answers stay offline."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/replay'))
spec = importlib.util.spec_from_file_location('v3_frozen_budget_helpers', ROOT / 'scripts/replay/79c_run_p1_output_budget_probe.py')
budget = importlib.util.module_from_spec(spec)
spec.loader.exec_module(budget)
base = budget.base
import p1_verifiable_workload_v3 as workload

CONFIG = ROOT / 'configs/p1-verifiable-workload-v3.json'
PROTOCOL = 'docs/p1/verifiable-task-pilot-protocol-v3.md'
NEW_SOURCES = ['scripts/replay/79c_run_p1_output_budget_probe.py',
    'scripts/replay/79e_run_p1_verifiable_tasks_v3.py',
    'scripts/replay/p1_verifiable_workload_v3.py', 'configs/p1-verifiable-workload-v3.json',
    'tests/unit/test_p1_verifiable_workload_v3.py', 'tests/unit/test_p1_verifiable_tasks_runner_v3.py', PROTOCOL,
    'docs/p1/verifiable-task-evaluation-rubric-v3.md']
TIMEOUT_SECONDS = 1200
SCOPE = ('One predeclared development v3 pilot: 24 extract/check/organize tasks plus 2 warmups. '
    'Offline facts and expected answers never enter the completion request payload. '
    'No final data, prediction fitting, scheduler comparison, automatic G1 or load promotion.')
ROW_KEYS = ('req_id', 'category', 'prompt', 'input_tokens', 'input_token_bucket', 'input_token_range',
    'max_tokens', 'temperature', 'ignore_eos', 'token_ids_sha256', 'content_sha256', 'prompt_format',
    'tokenization', 'task_kind', 'template_version', 'source_id', 'split', 'seed')
FORBIDDEN = {'expected_answer', 'expected_lines', 'oracle_evaluation_only', 'evaluation_only', 'target_ids',
    'source_records', 'left_records', 'right_records', 'oracle', 'evaluation'}
PAYLOAD_KEYS = {'prompt', 'n_predict', 'temperature', 'stream', 'ignore_eos', 'cache_prompt',
                'request_class', 'client_req_id'}


def completion_payload(row):
    """Reconstruct the frozen client's eight-key payload; not a wire capture."""
    return {'prompt': row['prompt'], 'n_predict': row['max_tokens'],
        'temperature': row.get('temperature', 0.0), 'stream': True, 'ignore_eos': False,
        'cache_prompt': row.get('cache_prompt', True), 'request_class': row['category'],
        'client_req_id': row['req_id']}


def replay_row(full):
    """Only pass allowlisted execution metadata to replay, never oracle fields."""
    required = {'req_id', 'category', 'prompt', 'input_tokens', 'input_token_bucket', 'max_tokens',
                'token_ids_sha256', 'content_sha256', 'task_kind'}
    assert required <= full.keys(), 'Missing materialized execution fields'
    row = {key: full[key] for key in ROW_KEYS if key in full}
    row.update(arrival_offset_ms=0, cache_prompt=False, temperature=0.0, ignore_eos=False)
    assert not (FORBIDDEN & row.keys())
    assert set(completion_payload(row)) == PAYLOAD_KEYS
    return row


def model_free_checks(cfg):
    old = base.read(budget.OLD / 'source-sha256.json')
    assert len(old) == 38 and all(base.prior.sha(ROOT / p) == h for p, h in old.items())
    fixed = base.read(ROOT / 'configs/p1-development-workload-v1.json')
    for key in ('max_output_tokens', 'sampling', 'input_token_buckets', 'slot_context_tokens',
                'input_plus_output_safety_tokens', 'tokenizer_request', 'prompt_format'):
        assert cfg[key] == fixed[key], 'Changed registered common field: ' + key
    assert cfg['protocol_path'] == PROTOCOL and cfg['cache_prompt'] is False
    assert cfg['development']['seeds'] == [23001]
    assert cfg['final_test_generation_enabled'] is False
    descriptors = workload.build_descriptors(config=cfg, split='development', seed=23001)
    assert len(descriptors) == len({r['req_id'] for r in descriptors}) == 24
    cells = Counter((r['category'], r['input_token_bucket']) for r in descriptors)
    assert len(cells) == 6 and set(cells.values()) == {4}
    kinds = Counter(r['task_kind'] for r in descriptors)
    assert kinds == {'extract': 8, 'check': 8, 'organize': 8}
    cross = Counter((r['category'], r['task_kind'], r['input_token_bucket']) for r in descriptors)
    assert len(cross) == 18 and set(cross.values()) <= {1, 2}, 'Kinds must span all input cells'
    for split in ('final', 'calibration'):
        try:
            workload.build_descriptors(config=cfg, split=split, seed=23001)
        except ValueError:
            pass
        else:
            raise AssertionError('Only the registered development pilot may be generated')
    return {'status': 'passed', 'model_started': False, 'measured_completions': 24,
        'warmup_completions': 2, 'completion_requests': 26, 'expected_cap_events': 52,
        'task_counts': dict(kinds), 'per_class_input_bucket': 4, 'kind_cross_cells': 18,
        'cache_prompt': False, 'old_frozen_sources_unchanged': 38,
        'offline_oracle_not_sent': True, 'scope': SCOPE}


def validate_plan(full, cfg):
    assert len(full) == len({r['req_id'] for r in full}) == 24
    cells = Counter((r['category'], r['input_token_bucket']) for r in full)
    assert len(cells) == 6 and set(cells.values()) == {4}
    assert Counter(r['task_kind'] for r in full) == {'extract':8, 'check':8, 'organize':8}
    assert len(Counter((r['category'],r['task_kind'],r['input_token_bucket']) for r in full)) == 18
    for row in full:
        assert row.get('evaluation_only', row.get('oracle_evaluation_only')) is True
        assert row['split'] == 'development' and row['seed'] == 23001
        assert isinstance(row['expected_answer'], str) and row['expected_answer']
        assert row['expected_answer'] == '\n'.join(row['expected_lines'])
        low, high = cfg['input_token_buckets'][row['category']][row['input_token_bucket']]
        assert low <= row['input_tokens'] <= high
        assert row['input_tokens'] + row['max_tokens'] + 1 <= 2048
        assert row['max_tokens'] == cfg['max_output_tokens'][row['category']]
        assert budget.text_sha(row['prompt']) == row['content_sha256']
    plan = [replay_row(row) for row in full]
    budget.validate(plan)
    return plan


def diagnostics(row):
    final = row['native_final']
    fields = ('req_id', 'task_kind', 'category', 'input_tokens', 'input_token_bucket', 'max_tokens',
        'output_tokens', 'terminal', 'cache_prompt', 'new_prompt_tokens', 'cached_prompt_tokens')
    return {key: row[key] for key in fields} | {'stop_type': final.get('stop_type'),
        'context_truncated': final.get('truncated'), 'content': row['content'],
        'content_sha256': budget.text_sha(row['content']),
        'offline_oracle_evaluation': 'pending_after_all_24_records_complete_no_collector_quality_verdict'}


async def collect(args, cfg):
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    completed = []
    server = log = sampler = sample_log = tokenizer = None
    base.dump(out / 'batch-status.json', {'status':'preparing', 'started_at':datetime.now().astimezone().isoformat()})
    try:
        base.dump(out / 'model-free-preflight.json', model_free_checks(cfg))
        assert shutil.disk_usage(out).free >= 10 * 1024**3
        base.free_port()
        running = subprocess.run(['nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader,nounits'],
            check=True, capture_output=True, text=True, timeout=15)
        base.dump(out / 'gpu-compute-process-preflight.json', {'stdout':running.stdout, 'stderr':running.stderr})
        assert not running.stdout.strip(), 'GPU compute processes already active; do not reuse or kill them'
        engine, model = args.engine_bin.resolve(strict=True), args.model.resolve(strict=True)
        engine_root = engine.parents[2]
        fixed = base.read(ROOT / 'configs/p1-development-validation-v1.json')
        runtime = base.prior.binary_inventory(engine)
        assert runtime == base.read(budget.OLD / 'engine-runtime-sha256.json')
        model_sha = await asyncio.to_thread(base.prior.sha, model)
        assert model_sha == fixed['model_sha256']
        commit = base.prior.git_output(engine_root, 'rev-parse', 'HEAD').strip()
        assert commit == fixed['engine_commit']
        gate_dir = args.client_gate_dir.resolve(strict=True)
        gate = base.read(gate_dir / 'summary.json')
        assert gate['status'] == 'passed'
        assert all(base.prior.sha(ROOT / p) == h for p,h in gate['source_sha256'].items())
        shutil.copytree(gate_dir, out/'client-gate')
        old = base.read(budget.OLD / 'source-sha256.json')
        hashes = {}
        for relative in list(old) + NEW_SOURCES:
            source = ROOT / relative
            target = out/'source'/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            hashes[relative] = base.prior.sha(source)
        assert all(hashes[p] == h for p,h in old.items())
        engine_hashes = base.read(budget.OLD/'engine-source-sha256.json')
        for relative,digest in engine_hashes.items():
            source = engine_root/relative
            assert base.prior.sha(source) == digest
            target = out/'engine-source'/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        base.dump(out/'source-sha256.json', hashes)
        base.dump(out/'engine-source-sha256.json', engine_hashes)
        base.dump(out/'engine-runtime-sha256.json', runtime)
        base.dump(out/'environment.json', {'recorded_at':datetime.now().astimezone().isoformat(),
            'python':sys.version, 'kernel':Path('/proc/version').read_text(), 'engine_commit':commit,
            'engine_binary':str(engine), 'model':str(model), 'model_sha256':model_sha,
            'project_commit':base.prior.git_output(ROOT,'rev-parse','HEAD').strip(),
            'project_working_tree':base.prior.git_output(ROOT,'status','--short'),
            'scope':SCOPE, 'protocol':PROTOCOL, 'config':cfg, 'cache_prompt':False,
            'batch_timeout_seconds':TIMEOUT_SECONDS})
        base.capture_gpu(out/'gpu-before.txt')
        sample_log = (out/'gpu-samples.csv').open('xb')
        sampler = await asyncio.create_subprocess_exec('nvidia-smi',
            '--query-gpu=timestamp,memory.used,utilization.gpu,temperature.gpu,power.draw', '--format=csv', '--loop-ms=1000',
            stdout=sample_log, stderr=asyncio.subprocess.STDOUT)
        service = out/'service'
        service.mkdir()
        server,log = await base.prior.start_server(service, engine, model, 'original', 5000)
        props = base.read(service/'props.json')
        assert props['total_slots'] == 2 and props['default_generation_settings']['n_ctx'] == 2048
        base.dump(out/'service-ownership.json', {'pid':server.pid, 'owner':'this_script_created_process', 'port_was_free_before_start':True})
        tokenizer = base.Tokenizer(out)
        try:
            descriptors = workload.build_descriptors(config=cfg, split='development', seed=23001)
            full = await asyncio.to_thread(workload.materialize_trace, descriptors, tokenizer, cfg)
            plan = validate_plan(full, cfg)
            warm_prompt = '<|im_start|>system\n你是简洁的助手。<|im_end|>\n<|im_start|>user\n请说准备好了。<|im_end|>\n<|im_start|>assistant\n'
            warm_tokens = await asyncio.to_thread(tokenizer, warm_prompt)
            warm = [{'req_id':f'warm{i}', 'category':'interactive', 'arrival_offset_ms':i*500,
                'prompt':warm_prompt, 'input_tokens':len(warm_tokens), 'max_tokens':8,
                'temperature':0, 'ignore_eos':False, 'cache_prompt':False} for i in range(2)]
            base.write_rows(out/'evaluation-plan.jsonl', full)
            base.write_rows(out/'plan.jsonl', plan)
            base.write_rows(out/'warmup-plan.jsonl', warm)
            base.dump(out/'tokenization-summary.json', {'calls':tokenizer.count, 'measured_prompts':24,
                'warmup_prompts':1, 'final_test_generated_or_read':False})
        finally:
            tokenizer.close()
            tokenizer = None
        await asyncio.wait_for(base.replay(warm,out/'warmup',base.prior.URL,1,15,10,50,360), timeout=60)
        warm_audit = base.audit_replay(out/'warmup', warm)
        assert not warm_audit['flags'] and warm_audit['terminal_counts']['completed'] == 2
        assert all(r['cached_prompt_tokens'] == 0 and r['cache_prompt'] is False for r in base.rows(out/'warmup/client.jsonl'))
        await base.await_idle(service,'after-warmup')
        base.dump(out/'warmup-audit.json', warm_audit | {'cache_zero_checked':True,
            'cap_pairs':budget.cap_pairs(service/'server.log',['warm0','warm1'])})
        client_sha = base.prior.sha(ROOT/'scripts/replay/p1_development_replay.py')
        for index,row in enumerate(plan,1):
            assert time.monotonic()-started < TIMEOUT_SECONDS
            assert shutil.disk_usage(out).free >= 4*1024**3
            assert sum(p.stat().st_size for p in out.rglob('*') if p.is_file()) < 512*1024**2
            assert all(base.prior.sha(ROOT/p) == h for p,h in hashes.items())
            directory = out/'cases'/f'q{index:02d}-{row["req_id"]}'
            directory.mkdir(parents=True)
            base.dump(directory/'manifest.json', {'status':'running', 'sequence_index':index,
                'req_id':row['req_id'], 'task_kind':row['task_kind'], 'oracle_evaluation':'pending_after_full_batch'})
            base.dump(directory/'completion-request-payload.json', {'evidence_kind':'reconstructed_from_frozen_client_not_actual_wire_capture',
                'client_source_sha256':client_sha, 'payload':completion_payload(row),
                'offline_fields_absent':True, 'isolation_evidence':'local HTTP capture unit plus frozen client SHA and actual native input token audit'})
            await base.replay([row],directory/'replay',base.prior.URL,.01,45,45,50,360)
            audit = base.audit_replay(directory/'replay',[row])
            base.dump(directory/'audit.json',audit)
            assert not audit['flags'] and audit['terminal_counts']['completed'] == 1, 'Retain failing request; stop later collection'
            observed = base.rows(directory/'replay/client.jsonl')[0]
            assert observed['cache_prompt'] is False and observed['cached_prompt_tokens'] == 0
            assert observed['new_prompt_tokens'] == observed['input_tokens']
            assert not (FORBIDDEN & observed.keys())
            await base.await_idle(directory,'after-request')
            audit.update(idle_verified=True, cache_zero_checked=True,
                cap_pairs=budget.cap_pairs(service/'server.log',[row['req_id']]))
            base.dump(directory/'audit.json',audit)
            result = diagnostics(observed)
            (directory/'output.txt').write_text(observed['content'],encoding='utf-8')
            base.dump(directory/'diagnostic.json',result)
            base.dump(directory/'manifest.json', {'status':'completed', 'sequence_index':index,
                'req_id':row['req_id'], 'task_kind':row['task_kind'], 'finished_at':datetime.now().astimezone().isoformat(),
                'oracle_evaluation':'pending_after_full_batch', 'audit':audit})
            completed.append({'directory':directory.relative_to(out).as_posix(), **result})
            base.dump(out/'diagnostics.json',completed)
            base.dump(out/'batch-status.json', {'status':'running', 'requests_completed':len(completed),
                'task_counts':dict(Counter(r['task_kind'] for r in completed)), 'elapsed_seconds':time.monotonic()-started})
            print(f'DONE {index}/24 {row["req_id"]} kind={row["task_kind"]} output={result["output_tokens"]}/{row["max_tokens"]} stop={result["stop_type"]}',flush=True)
        await base.await_idle(service,'after-all')
        await base.prior.prior.engine.stop_server(server,log)
        log = None
        base.dump(out/'service-stop.json', {'pid':server.pid, 'server_exit_code':server.returncode, 'stopped_owned_process_only':True})
        assert server.returncode == 0
        accounting = base.gate.verify_cap_events(service/'server.log',0)
        assert accounting['events'] == 52
        expected_ids = {r['req_id'] for r in plan} | {'warm0','warm1'}
        assert {e['clientid'] for e in base.gate.cap_events(service/'server.log')} == expected_ids
        base.dump(out/'cap-pairs.json',budget.cap_pairs(service/'server.log',sorted(expected_ids)))
        assert base.prior.binary_inventory(engine) == runtime
        assert all(base.prior.sha(ROOT/p) == h for p,h in hashes.items())
        assert all(base.prior.sha(engine_root/p) == h for p,h in engine_hashes.items())
        assert await asyncio.to_thread(base.prior.sha,model) == model_sha
        base.dump(out/'engine-runtime-sha256-after.json',base.prior.binary_inventory(engine))
        base.dump(out/'model-sha256-after.json',{'path':str(model),'sha256':model_sha})
        summary = {'status':'completed', 'study':cfg['study'], 'scope':SCOPE,
            'measured_completions':24, 'warmup_completions':2, 'completion_requests':26,
            'terminal_counts':{s:sum(r['terminal']==s for r in completed) for s in base.STATES},
            'task_counts':dict(Counter(r['task_kind'] for r in completed)), 'accounting':accounting,
            'server_exit_code':server.returncode, 'cache_prompt':False, 'all_cached_prompt_tokens_zero':True,
            'source_runtime_model_unchanged':True, 'expected_answers_offline_only':True,
            'http_payload_evidence':'reconstruction plus local HTTP capture gate; not real-run wire capture',
            'final_test_generated_or_read':False, 'oracle_evaluation':'pending_after_full_batch',
            'finished_at':datetime.now().astimezone().isoformat(), 'elapsed_seconds':time.monotonic()-started}
        base.dump(out/'summary.json',summary)
        base.dump(out/'batch-status.json',summary)
        print(json.dumps(summary,ensure_ascii=False),flush=True)
    except BaseException as exc:
        (out/'error-traceback.txt').write_text(traceback.format_exc(),encoding='utf-8')
        base.dump(out/'batch-status.json', {'status':'failed', 'requests_completed':len(completed),
            'error':repr(exc), 'finished_at':datetime.now().astimezone().isoformat(), 'elapsed_seconds':time.monotonic()-started})
        raise
    finally:
        if tokenizer is not None:
            tokenizer.close()
        if server is not None and log is not None:
            await base.prior.prior.engine.stop_server(server,log)
            base.dump(out/'service-stop.json', {'pid':server.pid, 'server_exit_code':server.returncode,
                'stopped_owned_process_only':True, 'cleanup_after_failed_or_interrupted_batch':True})
        if sampler is not None and sampler.returncode is None:
            sampler.terminate()
            await sampler.wait()
        if sample_log is not None:
            sample_log.close()
            base.capture_gpu(out/'gpu-after.txt')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir',type=Path)
    parser.add_argument('--config',type=Path,default=CONFIG)
    parser.add_argument('--model-free-check',action='store_true')
    parser.add_argument('--engine-bin',type=Path)
    parser.add_argument('--model',type=Path,default=base.prior.DEFAULT_MODEL)
    parser.add_argument('--client-gate-dir',type=Path,default=ROOT/'results/raw/client-verification/20261024-p1-development-client-v2')
    args = parser.parse_args()
    if args.config.resolve() != CONFIG.resolve():
        parser.error('Only the registered v3 configuration is accepted')
    cfg = base.read(args.config)
    if args.model_free_check:
        result = model_free_checks(cfg)
        result['source_sha256'] = {p:base.prior.sha(ROOT/p) for p in NEW_SOURCES}
        if args.out_dir is not None:
            args.out_dir.resolve().mkdir(parents=True,exist_ok=False)
            base.dump(args.out_dir.resolve()/'summary.json',result)
        print(json.dumps(result,ensure_ascii=False))
        return
    if args.engine_bin is None or args.out_dir is None:
        parser.error('--engine-bin and --out-dir are required for real collection')
    asyncio.run(asyncio.wait_for(collect(args,cfg),timeout=TIMEOUT_SECONDS))


if __name__ == '__main__':
    main()
