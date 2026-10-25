#!/usr/bin/env python3
"""Collect one fixed original/clear-instruction paired pilot; answers stay offline."""
import argparse
import asyncio
from collections import Counter
from copy import deepcopy
from datetime import datetime
import importlib.util
import json
import os
import re
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
import p1_verifiable_workload_v5 as workload
from p1_instruction_constraints_v5 import grammar_from_prompt
import p1_structured_replay_v4 as structured

CONFIG = ROOT/'configs/p1-model-adaptation-v6.json'
PROTOCOL = 'docs/p1/model-adaptation-pilot-protocol-v6.md'
V5 = ROOT/'results/raw/development/20261025-1645-p1-explicit-instructions-v5'
EVAL_FREEZE = ROOT/'results/raw/maintenance/20261025-p1-explicit-instructions-evaluation-freeze-v5-r2/summary.json'
NEW_SOURCES = ['scripts/replay/79h_run_p1_model_adaptation_v6.py', 'configs/p1-model-adaptation-v6.json',
 'tests/unit/test_p1_model_adaptation_runner_v6.py', PROTOCOL, 'docs/p1/model-adaptation-evaluation-rubric-v6.md']
TIMEOUT_SECONDS = 3600
SCOPE = 'Development adaptation/recheck of all24 previously seen v5 C prompts; A1.5B vs B3B fixed model artifacts. Four independent ABBA services; no new/unseen/final, calibration, load, scheduler or G1 claim.'
ROW_KEYS = ('req_id', 'category', 'prompt', 'input_tokens', 'input_token_bucket', 'input_token_range',
    'max_tokens', 'temperature', 'ignore_eos', 'token_ids_sha256', 'content_sha256', 'prompt_format',
    'tokenization', 'task_kind', 'template_version', 'source_id', 'split', 'seed',
    'content_id', 'pair_id', 'pair_index', 'arm', 'condition_order', 'condition_position', 'instruction_version','instruction_arm','source_req_id','source_pair_id','source_condition_order','source_kind_instance','kind_instance','block_id','group_id')
FORBIDDEN = {'expected_answer', 'expected_lines', 'oracle_evaluation_only', 'evaluation_only', 'target_ids',
    'source_records', 'left_records', 'right_records', 'oracle', 'evaluation'}
PAYLOAD_KEYS = {'prompt', 'n_predict', 'temperature', 'stream', 'ignore_eos', 'cache_prompt',
                'request_class', 'client_req_id'}


def completion_payload(row): return structured.completion_payload(row)

def frozen_sources():
    frozen=base.read(V5/'source-sha256.json')
    assert len(frozen)==69
    frozen.update(base.read(EVAL_FREEZE)['evaluation_source_sha256'])
    assert len(frozen)==72 and all(base.prior.sha(ROOT/p)==h for p,h in frozen.items())
    return frozen

def source_rows(cfg):
    path=V5/'evaluation-plan.jsonl'
    assert base.prior.sha(path)==cfg['source_evaluation_plan_sha256']
    selected=[r for r in base.rows(path) if r['arm']=='C']
    assert len(selected)==len({r['pair_id'] for r in selected})==24
    return selected

def block_rows(cfg,block_id,tokenizer=None):
    block=cfg['blocks'][block_id-1]
    result=[]
    for source in source_rows(cfg):
        if source['kind_instance'] not in block['kind_instances']: continue
        row=deepcopy(source)
        row.update(source_req_id=source['req_id'],source_pair_id=source['pair_id'],
          source_condition_order=source['condition_order'],source_kind_instance=source['kind_instance'],
          req_id='v6-'+source['pair_id']+'-'+block['arm'].lower(),pair_id='v6-'+source['pair_id'],
          arm=block['arm'],instruction_arm='C',block_id=block_id,group_id=block['group_id'],
          condition_order='AB' if block['group_id']==1 else 'BA',
          condition_position=1 if block_id in (1,3) else 2)
        if tokenizer is not None:
            ids=tokenizer(row['prompt'])
            row['input_tokens']=len(ids)
            row['token_ids_sha256']=budget.text_sha(json.dumps(ids,separators=(',',':')))
        result.append(row)
    assert len(result)==12
    return result

def check_design(full):
    assert len(full)==len({r['req_id'] for r in full})==48
    assert Counter(r['arm'] for r in full)=={'A':24,'B':24}
    for bid in range(1,5):
        group=[r for r in full if r['block_id']==bid]
        assert len(group)==12 and sum(len(r['target_ids']) for r in group)==44
        assert Counter((r['category'],r['task_kind']) for r in group)=={(c,k):2 for c in ('interactive','background') for k in ('extract','check','organize')}
        assert Counter((r['category'],r['input_token_bucket']) for r in group)=={(c,b):2 for c in ('interactive','background') for b in range(3)}
        assert Counter(v.split('|')[1] for r in group if r['task_kind']=='check' for v in r['expected_lines'])=={'一致':7,'不一致':7}
    for pair_id in dict.fromkeys(r['pair_id'] for r in full):
        pair=[r for r in full if r['pair_id']==pair_id]
        assert len(pair)==2 and {r['arm'] for r in pair}=={'A','B'}
        for key in ('prompt','content_sha256','target_ids','expected_lines','max_tokens','source_req_id','kind_instance'):
            assert pair[0][key]==pair[1][key],key
        assert ''.join(r['arm'] for r in pair)==pair[0]['condition_order']

def model_free_checks(cfg):
    frozen=frozen_sources()
    assert cfg['blocks']==[{'arm':'A','group_id':1,'kind_instances':[0,3]}, {'arm':'B','group_id':1,'kind_instances':[0,3]}, {'arm':'B','group_id':2,'kind_instances':[1,2]}, {'arm':'A','group_id':2,'kind_instances':[1,2]}]
    assert cfg['final_test_generation_enabled'] is False and cfg['allowed_splits']==['development']
    fixed=base.read(ROOT/'configs/p1-development-workload-v1.json')
    for key in ('max_output_tokens','sampling','input_token_buckets','slot_context_tokens','input_plus_output_safety_tokens','tokenizer_request','prompt_format'): assert cfg[key]==fixed[key],key
    full=[r for bid in range(1,5) for r in block_rows(cfg,bid)]
    check_design(full)
    for row in full: replay_row(row)
    return {'status':'passed','model_started':False,'unique_tasks':24,'measured_completions':48,
      'warmup_completions':8,'completion_requests':56,'expected_cap_events':112,
      'frozen_old_source_count':len(frozen),'scope':SCOPE}

def replay_row(full):
    row={k:full[k] for k in ROW_KEYS if k in full}
    row.update(arrival_offset_ms=0,cache_prompt=False,temperature=0.0,ignore_eos=False)
    row['grammar']=grammar_from_prompt(row['prompt'],row['category'])
    row['grammar_sha256']=budget.text_sha(row['grammar'])
    assert not FORBIDDEN & row.keys()
    assert set(completion_payload(row))==PAYLOAD_KEYS|{'grammar'}
    return row

def validate_plan(full,cfg):
    assert len(full)==12
    for row in full:
        assert row['instruction_arm']=='C' and row['arm'] in ('A','B')
        assert row['split']=='development' and row['seed']==25001
        low,high=cfg['input_token_buckets'][row['category']][row['input_token_bucket']]
        assert low<=row['input_tokens']<=high
        assert row['input_tokens']+row['max_tokens']+1<=2048
        assert row['max_tokens']==cfg['max_output_tokens'][row['category']]
        assert budget.text_sha(row['prompt'])==row['content_sha256']
    plan=[replay_row(row) for row in full]
    budget.validate(plan)
    return plan

async def start_server(directory, engine_bin, model, strategy, aging_ms):
    command = [str(engine_bin), "-m", str(model), "--host", "127.0.0.1", "--port", "8080",
               "-ngl", "99", "-c", "4096", "-np", "2", "-b", "2048", "-ub", "512", "--metrics", "-lv", "4"]
    policy_env = base.prior.policy_environment(strategy, aging_ms)
    base.dump(directory / "server-command.json", command)
    base.dump(directory / "server-policy-environment.json", policy_env)
    log = (directory / "server.log").open("xb")
    proc = await asyncio.create_subprocess_exec(*command, env=dict(os.environ, **policy_env), stdout=log, stderr=asyncio.subprocess.STDOUT)
    try:
        for _ in range(120):
            if proc.returncode is not None:
                raise RuntimeError("Server exited during startup")
            try:
                await asyncio.to_thread(base.prior.fetch, "/health", directory / "health.json")
                await asyncio.to_thread(base.prior.fetch, "/props", directory / "props.json")
                return proc, log
            except Exception:
                await asyncio.sleep(1)
        raise RuntimeError("Server not ready within 120 seconds")
    except BaseException:
        await base.prior.prior.engine.stop_server(proc, log)
        raise


def diagnostics(row):
    final = row['native_final']
    fields = ('req_id', 'task_kind', 'category', 'input_tokens', 'input_token_bucket', 'max_tokens',
        'output_tokens', 'terminal', 'cache_prompt', 'new_prompt_tokens', 'cached_prompt_tokens',
        'pair_id', 'arm', 'condition_order', 'condition_position')
    return {key: row[key] for key in fields} | {'stop_type': final.get('stop_type'),
        'context_truncated': final.get('truncated'), 'content': row['content'],
        'content_sha256': budget.text_sha(row['content']),
        'offline_oracle_evaluation': 'pending_after_all_48_records_complete_no_collector_quality_verdict'}


async def collect_block(args, cfg):
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
        assert model_sha == cfg['models'][args.arm]['sha256']
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
        server,log = await start_server(service, engine, model, 'original', 5000)
        props = base.read(service/'props.json')
        assert props['total_slots'] == 2 and props['default_generation_settings']['n_ctx'] == 2048
        base.dump(out/'service-ownership.json', {'pid':server.pid, 'owner':'this_script_created_process', 'port_was_free_before_start':True})
        startup=(service/'server.log').read_text(encoding='utf-8',errors='replace')
        layers=cfg['models'][args.arm]['offload_layers']
        assert re.search(r'offloaded '+str(layers)+r'/'+str(layers)+r' layers to GPU',startup), 'Full GPU placement not verified'
        base.dump(service/'load-audit.json',{'arm':args.arm,'full_offload_layers':layers,'total_slots':props['total_slots'],'slot_context':2048,'log_verbosity':4})
        tokenizer = base.Tokenizer(out)
        try:
            full = await asyncio.to_thread(block_rows,cfg,args.block_id,tokenizer)
            plan = validate_plan(full,cfg)
            warm_prompt = '<|im_start|>system\n你是简洁的助手。<|im_end|>\n<|im_start|>user\n请说准备好了。<|im_end|>\n<|im_start|>assistant\n'
            warm_tokens = await asyncio.to_thread(tokenizer, warm_prompt)
            warm = [{'req_id':f'warm{i}', 'category':'interactive', 'arrival_offset_ms':i*500,
                'prompt':warm_prompt, 'input_tokens':len(warm_tokens), 'max_tokens':8,
                'temperature':0, 'ignore_eos':False, 'cache_prompt':False} for i in range(2)]
            base.write_rows(out/'evaluation-plan.jsonl', full)
            base.write_rows(out/'plan.jsonl', plan)
            base.write_rows(out/'warmup-plan.jsonl', warm)
            base.dump(out/'tokenization-summary.json', {'calls':tokenizer.count, 'unique_measured_prompts':12, 'measured_completions':12,
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
            print(f'BLOCK {args.block_id} DONE {index}/12 {row["req_id"]} kind={row["task_kind"]} output={result["output_tokens"]}/{row["max_tokens"]} stop={result["stop_type"]}',flush=True)
        await base.await_idle(service,'after-all')
        await base.prior.prior.engine.stop_server(server,log)
        log = None
        base.dump(out/'service-stop.json', {'pid':server.pid, 'server_exit_code':server.returncode, 'stopped_owned_process_only':True})
        assert server.returncode == 0
        accounting = base.gate.verify_cap_events(service/'server.log',0)
        assert accounting['events'] == 28
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
            'unique_tasks':12, 'measured_completions':12, 'warmup_completions':2, 'completion_requests':14,
            'conditions':{args.arm:12}, 'block_id':args.block_id,
            'terminal_counts':{s:sum(r['terminal']==s for r in completed) for s in base.STATES},
            'task_counts':dict(Counter(r['task_kind'] for r in completed)), 'accounting':accounting,
            'server_exit_code':server.returncode, 'cache_prompt':False, 'all_cached_prompt_tokens_zero':True,
            'source_runtime_model_unchanged':True, 'expected_answers_offline_only':True,'same_pair_grammar':True,'input_tokens_matched':False,
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


async def collect(args,cfg):
    from argparse import Namespace
    out=args.out_dir.resolve(); out.mkdir(parents=True,exist_ok=False)
    started=time.monotonic()
    base.dump(out/'batch-status.json',{'status':'preparing','started_at':datetime.now().astimezone().isoformat()})
    try:
        base.dump(out/'model-free-preflight.json',model_free_checks(cfg))
        full=[]; plans=[]; summaries=[]
        for bid,block in enumerate(cfg['blocks'],1):
            child=out/'blocks'/f'block{bid}'
            sub=Namespace(**vars(args)); sub.out_dir=child; sub.block_id=bid; sub.arm=block['arm']; sub.model=Path(cfg['models'][sub.arm]['path'])
            await collect_block(sub,cfg)
            summaries.append(base.read(child/'summary.json'))
            full.extend(base.rows(child/'evaluation-plan.jsonl')); plans.extend(base.rows(child/'plan.jsonl'))
            base.dump(out/'batch-status.json',{'status':'running','blocks_completed':bid,'measured_completed':len(plans),'elapsed_seconds':time.monotonic()-started})
        check_design(full)
        base.write_rows(out/'evaluation-plan.jsonl',full); base.write_rows(out/'plan.jsonl',plans)
        for name in ('source-sha256.json','engine-source-sha256.json','engine-runtime-sha256.json'):
            mapping=base.read(out/'blocks/block1'/name)
            assert all(base.read(out/'blocks'/f'block{bid}'/name)==mapping for bid in range(1,5))
            base.dump(out/name,mapping)
        summary={'status':'completed','study':cfg['study'],'scope':SCOPE,'unique_tasks':24,'measured_completions':48,'warmup_completions':8,'completion_requests':56,
          'conditions':{'A':24,'B':24},'pair_orders':{'AB':12,'BA':12},'accounting':{'events':sum(s['accounting']['events'] for s in summaries)},
          'server_exit_codes':[s['server_exit_code'] for s in summaries],'terminal_counts':{state:sum(s['terminal_counts'][state] for s in summaries) for state in base.STATES},
          'final_test_generated_or_read':False,'G1_passed':False,'elapsed_seconds':time.monotonic()-started,'finished_at':datetime.now().astimezone().isoformat()}
        base.dump(out/'summary.json',summary); base.dump(out/'batch-status.json',summary)
    except BaseException as exc:
        (out/'error-traceback.txt').write_text(traceback.format_exc(),encoding='utf-8')
        base.dump(out/'batch-status.json',{'status':'failed','error':repr(exc),'elapsed_seconds':time.monotonic()-started})
        raise

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir',type=Path)
    parser.add_argument('--config',type=Path,default=CONFIG)
    parser.add_argument('--model-free-check',action='store_true')
    parser.add_argument('--engine-bin',type=Path)
    parser.add_argument('--model',type=Path,default=base.prior.DEFAULT_MODEL)
    parser.add_argument('--client-gate-dir',type=Path,default=ROOT/'results/raw/client-verification/20261024-p1-development-client-v2')
    parser.add_argument('--structured-client-gate-dir',type=Path,default=ROOT/'results/raw/client-verification/20261025-p1-structured-client-v4-r2')
    args = parser.parse_args()
    if args.config.resolve() != CONFIG.resolve():
        parser.error('Only the registered v6 configuration is accepted')
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
