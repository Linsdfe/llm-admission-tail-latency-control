#!/usr/bin/env python3
"""Collect one new-seed paired output-grammar pilot; expected answers stay offline."""
import argparse
import asyncio
from collections import Counter
from copy import deepcopy
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
import p1_verifiable_workload_v4 as workload
from p1_output_constraints_v4 import grammar_from_prompt
import p1_structured_replay_v4 as structured

CONFIG = ROOT / 'configs/p1-output-constraints-v4.json'
PROTOCOL = 'docs/p1/output-constraints-pilot-protocol-v4.md'
V3 = ROOT / 'results/raw/development/20261025-1015-p1-verifiable-tasks-v3'
EVAL_FREEZE = ROOT / 'results/raw/maintenance/20261025-p1-verifiable-evaluation-freeze-v3/summary.json'
NEW_SOURCES = ['scripts/replay/79f_run_p1_output_constraints_v4.py',
    'scripts/replay/p1_verifiable_workload_v4.py', 'configs/p1-output-constraints-v4.json',
    'scripts/replay/p1_output_constraints_v4.py', 'scripts/replay/p1_structured_replay_v4.py',
    'tests/unit/test_p1_output_constraints_runner_v4.py',
    'tests/unit/test_p1_output_constraints_v4.py', PROTOCOL,
    'docs/p1/output-constraints-evaluation-rubric-v4.md']
TIMEOUT_SECONDS = 2400
SCOPE = ('One predeclared development seed24001: 24 new extract/check/organize tasks, '
    'each in adjacent U/G order-balanced pairs, 48 measured plus 2 warmups. '
    'Grammar uses only public final task IDs/order/count and the full legal value domain. '
    'Offline facts and expected answers never enter completion payload. '
    'No historical effect control, final data, fitting, scheduler comparison, G1 or load promotion.')
ROW_KEYS = ('req_id', 'category', 'prompt', 'input_tokens', 'input_token_bucket', 'input_token_range',
    'max_tokens', 'temperature', 'ignore_eos', 'token_ids_sha256', 'content_sha256', 'prompt_format',
    'tokenization', 'task_kind', 'template_version', 'source_id', 'split', 'seed',
    'content_id', 'pair_id', 'pair_index', 'arm', 'condition_order', 'condition_position')
FORBIDDEN = {'expected_answer', 'expected_lines', 'oracle_evaluation_only', 'evaluation_only', 'target_ids',
    'source_records', 'left_records', 'right_records', 'oracle', 'evaluation'}
PAYLOAD_KEYS = {'prompt', 'n_predict', 'temperature', 'stream', 'ignore_eos', 'cache_prompt',
                'request_class', 'client_req_id'}


def completion_payload(row):
    """Actual client constructor, also saved explicitly as reconstruction, not wire capture."""
    return structured.completion_payload(row)


def frozen_sources():
    frozen = base.read(V3 / 'source-sha256.json')
    assert len(frozen) == 46
    frozen.update(base.read(EVAL_FREEZE)['evaluation_source_sha256'])
    assert len(frozen) == 49
    assert all(base.prior.sha(ROOT / p) == h for p, h in frozen.items()), 'Old frozen v3 source changed'
    return frozen


def paired_evaluation_rows(unique):
    """Predeclare both conditions for every new task, never choose using output."""
    assert len(unique) == len({row['req_id'] for row in unique}) == 24
    result = []
    for index, full in enumerate(unique, 1):
        order = ('U','G') if full['kind_instance'] % 2 == 0 else ('G','U')
        pair_id = full['req_id']
        for position, condition in enumerate(order, 1):
            item = deepcopy(full)
            item.update(req_id=pair_id+'-'+condition.lower(), pair_id=pair_id,
                pair_index=index, arm=condition, condition_order=''.join(order),
                condition_position=position)
            result.append(item)
    return result


def replay_row(full):
    """Only execution metadata; grammar derives from actual public prompt alone."""
    required = {'req_id', 'category', 'prompt', 'input_tokens', 'input_token_bucket', 'max_tokens',
                'token_ids_sha256', 'content_sha256', 'task_kind', 'arm', 'pair_id'}
    assert required <= full.keys(), 'Missing materialized execution fields'
    assert full['arm'] in ('U','G')
    row = {key: full[key] for key in ROW_KEYS if key in full}
    row.update(arrival_offset_ms=0, cache_prompt=False, temperature=0.0, ignore_eos=False)
    if row['arm'] == 'G':
        row['grammar'] = grammar_from_prompt(row['prompt'],row['category'])
        row['grammar_sha256'] = budget.text_sha(row['grammar'])
    assert not (FORBIDDEN & row.keys())
    assert set(completion_payload(row)) == PAYLOAD_KEYS | ({'grammar'} if row['arm']=='G' else set())
    return row


def check_pair_design(full):
    assert len(full) == len({row['req_id'] for row in full}) == 48
    assert Counter(row['arm'] for row in full) == {'U':24,'G':24}
    firsts = full[::2]
    assert len({row['pair_id'] for row in firsts}) == 24
    assert Counter(row['condition_order'] for row in firsts) == {'UG':12,'GU':12}
    for index in range(0,48,2):
        left,right = full[index:index+2]
        assert left['pair_id'] == right['pair_id']
        assert left['arm']+right['arm'] == left['condition_order'] == right['condition_order']
        assert (left['condition_position'],right['condition_position']) == (1,2)
        assert left['pair_index'] == right['pair_index'] == index//2+1
        assert left['req_id']==left['pair_id']+'-'+left['arm'].lower()
        assert right['req_id']==right['pair_id']+'-'+right['arm'].lower()
        for key in ('prompt','content_id','content_sha256','token_ids_sha256','input_tokens','max_tokens',
                    'task_kind','category','input_token_bucket','expected_answer','expected_lines'):
            assert left[key] == right[key], 'Pair changed shared field: '+key
    balanced = Counter((row['category'],row['input_token_bucket'],row['condition_order']) for row in firsts)
    assert len(balanced)==12 and set(balanced.values())=={2}
    return firsts


def model_free_checks(cfg):
    old = base.read(budget.OLD / 'source-sha256.json')
    assert len(old) == 38 and all(base.prior.sha(ROOT / p) == h for p, h in old.items())
    frozen = frozen_sources()
    fixed = base.read(ROOT / 'configs/p1-development-workload-v1.json')
    for key in ('max_output_tokens', 'sampling', 'input_token_buckets', 'slot_context_tokens',
                'input_plus_output_safety_tokens', 'tokenizer_request', 'prompt_format'):
        assert cfg[key] == fixed[key], 'Changed registered common field: ' + key
    assert cfg['protocol_path'] == PROTOCOL and cfg['cache_prompt'] is False
    assert cfg['development']['seeds'] == [24001] and cfg['allowed_splits']==['development']
    assert cfg['final_test_generation_enabled'] is False
    assert cfg['comparison'] == {'conditions':['U','G'],'unconstrained':'U','public_instruction_grammar':'G',
        'pair_order':'kind_instance even UG, odd GU','unique_tasks':24,'measured_completions':48,
        'warmup_completions':2,'completion_requests':50,'expected_cap_events':100,'historical_v3_not_effect_control':True}
    descriptors = workload.build_descriptors(config=cfg, split='development', seed=24001)
    assert len(descriptors) == len({r['req_id'] for r in descriptors}) == 24
    cells = Counter((r['category'], r['input_token_bucket']) for r in descriptors)
    assert len(cells) == 6 and set(cells.values()) == {4}
    kinds = Counter(r['task_kind'] for r in descriptors)
    assert kinds == {'extract': 8, 'check': 8, 'organize': 8}
    cross = Counter((r['category'], r['task_kind'], r['input_token_bucket']) for r in descriptors)
    assert len(cross) == 18 and set(cross.values()) <= {1,2}
    # Synthetic input metadata is not a tokenizer result and never used to run.
    fixtures=[]
    for item in descriptors:
        material = workload.task_material(item,0)
        prompt = workload.render_candidate(item,0)
        fixtures.append(dict(item, **material, prompt=prompt, content_sha256=budget.text_sha(prompt),
            token_ids_sha256='model_free_not_tokenized', input_tokens=1))
    full = paired_evaluation_rows(fixtures)
    check_pair_design(full)
    projected = [replay_row(row) for row in full]
    assert sum('grammar' in row for row in projected)==24
    assert all(not FORBIDDEN & row.keys() for row in projected)
    for split in ('final','calibration'):
        try: workload.build_descriptors(config=cfg,split=split,seed=24001)
        except ValueError: pass
        else: raise AssertionError('Only registered new development pilot may be generated')
    return {'status':'passed','model_started':False,'unique_tasks':24,'measured_completions':48,
        'warmup_completions':2,'completion_requests':50,'expected_cap_events':100,
        'conditions':{'U':24,'G':24},'pair_orders':{'UG':12,'GU':12},'task_counts_unique':dict(kinds),
        'per_class_input_bucket_unique':4,'kind_cross_cells':18,'cache_prompt':False,
        'old_frozen_sources_unchanged':len(frozen),'offline_oracle_not_sent':True,'scope':SCOPE}


def validate_plan(full,cfg):
    unique=check_pair_design(full)
    cells=Counter((row['category'],row['input_token_bucket']) for row in unique)
    assert len(cells)==6 and set(cells.values())=={4}
    assert Counter(row['task_kind'] for row in unique)=={'extract':8,'check':8,'organize':8}
    assert len(Counter((row['category'],row['task_kind'],row['input_token_bucket']) for row in unique))==18
    for row in full:
        assert row.get('evaluation_only',row.get('oracle_evaluation_only')) is True
        assert row['split']=='development' and row['seed']==24001
        assert isinstance(row['expected_answer'],str) and row['expected_answer']
        assert row['expected_answer']=='\n'.join(row['expected_lines'])
        low,high=cfg['input_token_buckets'][row['category']][row['input_token_bucket']]
        assert low<=row['input_tokens']<=high
        assert row['input_tokens']+row['max_tokens']+1<=2048
        assert row['max_tokens']==cfg['max_output_tokens'][row['category']]
        assert budget.text_sha(row['prompt'])==row['content_sha256']
    plan=[replay_row(row) for row in full]
    budget.validate(plan)
    return plan


def diagnostics(row):
    final = row['native_final']
    fields = ('req_id', 'task_kind', 'category', 'input_tokens', 'input_token_bucket', 'max_tokens',
        'output_tokens', 'terminal', 'cache_prompt', 'new_prompt_tokens', 'cached_prompt_tokens',
        'pair_id', 'arm', 'condition_order', 'condition_position')
    return {key: row[key] for key in fields} | {'stop_type': final.get('stop_type'),
        'context_truncated': final.get('truncated'), 'content': row['content'],
        'content_sha256': budget.text_sha(row['content']),
        'offline_oracle_evaluation': 'pending_after_all_48_records_complete_no_collector_quality_verdict'}


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
        structured_gate_dir = args.structured_client_gate_dir.resolve(strict=True)
        structured_gate = base.read(structured_gate_dir/'summary.json')
        assert structured_gate['status']=='passed'
        assert structured_gate['source_sha256']
        assert all(base.prior.sha(ROOT/p)==h for p,h in structured_gate['source_sha256'].items())
        assert 'scripts/replay/p1_structured_replay_v4.py' in structured_gate['source_sha256']
        assert 'scripts/replay/p1_output_constraints_v4.py' in structured_gate['source_sha256']
        shutil.copytree(structured_gate_dir,out/'structured-client-gate')
        old = frozen_sources()
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
            descriptors = workload.build_descriptors(config=cfg, split='development', seed=24001)
            unique = await asyncio.to_thread(workload.materialize_trace, descriptors, tokenizer, cfg)
            full = paired_evaluation_rows(unique)
            plan = validate_plan(full,cfg)
            warm_prompt = '<|im_start|>system\n你是简洁的助手。<|im_end|>\n<|im_start|>user\n请说准备好了。<|im_end|>\n<|im_start|>assistant\n'
            warm_tokens = await asyncio.to_thread(tokenizer, warm_prompt)
            warm = [{'req_id':f'warm{i}', 'category':'interactive', 'arrival_offset_ms':i*500,
                'prompt':warm_prompt, 'input_tokens':len(warm_tokens), 'max_tokens':8,
                'temperature':0, 'ignore_eos':False, 'cache_prompt':False} for i in range(2)]
            base.write_rows(out/'evaluation-plan.jsonl', full)
            base.write_rows(out/'plan.jsonl', plan)
            base.write_rows(out/'warmup-plan.jsonl', warm)
            base.dump(out/'tokenization-summary.json', {'calls':tokenizer.count, 'unique_measured_prompts':24, 'measured_completions':48,
                'warmup_prompts':1, 'final_test_generated_or_read':False})
        finally:
            tokenizer.close()
            tokenizer = None
        await asyncio.wait_for(structured.replay(warm,out/'warmup',base.prior.URL,1,15,10,50,360), timeout=60)
        warm_audit = base.audit_replay(out/'warmup', warm)
        assert not warm_audit['flags'] and warm_audit['terminal_counts']['completed'] == 2
        assert all(r['cached_prompt_tokens'] == 0 and r['cache_prompt'] is False for r in base.rows(out/'warmup/client.jsonl'))
        await base.await_idle(service,'after-warmup')
        base.dump(out/'warmup-audit.json', warm_audit | {'cache_zero_checked':True,
            'cap_pairs':budget.cap_pairs(service/'server.log',['warm0','warm1'])})
        client_sha = base.prior.sha(ROOT/'scripts/replay/p1_structured_replay_v4.py')
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
            await structured.replay([row],directory/'replay',base.prior.URL,.01,45,45,50,360)
            audit = base.audit_replay(directory/'replay',[row])
            base.dump(directory/'audit.json',audit)
            assert not audit['flags'] and audit['terminal_counts']['completed'] == 1, 'Retain failing request; stop later collection'
            observed = base.rows(directory/'replay/client.jsonl')[0]
            assert observed['cache_prompt'] is False and observed['cached_prompt_tokens'] == 0
            assert observed['new_prompt_tokens'] == observed['input_tokens']
            assert observed['native_final']['generation_settings']['grammar'] == row.get('grammar','')
            assert observed['native_final']['generation_settings']['grammar_lazy'] is False
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
            print(f'DONE {index}/48 {row["req_id"]} kind={row["task_kind"]} output={result["output_tokens"]}/{row["max_tokens"]} stop={result["stop_type"]}',flush=True)
        await base.await_idle(service,'after-all')
        await base.prior.prior.engine.stop_server(server,log)
        log = None
        base.dump(out/'service-stop.json', {'pid':server.pid, 'server_exit_code':server.returncode, 'stopped_owned_process_only':True})
        assert server.returncode == 0
        accounting = base.gate.verify_cap_events(service/'server.log',0)
        assert accounting['events'] == 100
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
            'unique_tasks':24, 'measured_completions':48, 'warmup_completions':2, 'completion_requests':50,
            'conditions':{'U':24,'G':24}, 'pair_orders':{'UG':12,'GU':12},
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
    parser.add_argument('--structured-client-gate-dir',type=Path,default=ROOT/'results/raw/client-verification/20261025-p1-structured-client-v4')
    args = parser.parse_args()
    if args.config.resolve() != CONFIG.resolve():
        parser.error('Only the registered v4 configuration is accepted')
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
