#!/usr/bin/env python3
"""Append-only fixed3B synthetic profiling and three-policy finite-burst pilot."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/replay'))
spec=importlib.util.spec_from_file_location('frozen_v6_helpers',ROOT/'scripts/replay/79h_run_p1_model_adaptation_v6.py')
v6=importlib.util.module_from_spec(spec); spec.loader.exec_module(v6)
base,budget,structured=v6.base,v6.budget,v6.structured
import p1_system_workload_v7 as workload
import p1_profile_estimator_v7 as estimator
from p1_instruction_constraints_v5 import grammar_from_prompt
CONFIG=ROOT/'configs/p1-system-workload-v7.json'
NEW_SOURCES=['scripts/replay/79i_run_p1_system_workload_v7.py','scripts/replay/p1_system_workload_v7.py',
 'scripts/replay/p1_profile_estimator_v7.py','configs/p1-system-workload-v7.json',
 'docs/p1/system-workload-amendment-v7.md','docs/p1/system-workload-pilot-protocol-v7.md',
 'tests/unit/test_p1_system_workload_v7.py']
RUN6=ROOT/'results/raw/development/20261025-2210-p1-model-adaptation-v6'

def frozen_sources():
    old=base.read(RUN6/'source-sha256.json')
    old.update(base.read(ROOT/'results/raw/maintenance/20261025-p1-model-adaptation-preparation-v6/summary.json')['evaluation_source_sha256'])
    assert len(old)==80 and all(base.prior.sha(ROOT/p)==h for p,h in old.items())
    return old

def replay_row(full):
    row={k:full[k] for k in v6.ROW_KEYS if k in full}
    row.update(arrival_offset_ms=0,cache_prompt=False,temperature=0.0,ignore_eos=False)
    row['grammar']=grammar_from_prompt(row['prompt'],row['category'])
    row['grammar_sha256']=budget.text_sha(row['grammar'])
    assert not v6.FORBIDDEN & row.keys()
    assert set(structured.completion_payload(row))==v6.PAYLOAD_KEYS|{'grammar'}
    return row

def make_trace(plan):
    assert len(plan)==24
    out=[]
    for category in ('interactive','background'):
        group=sorted((r for r in plan if r['category']==category),key=lambda r:r['req_id'])
        assert len(group)==12
        for i,row in enumerate(group):
            arrival=i*1000 if category=='interactive' else (i//3)*3000+250+(i%3)*50
            out.append(dict(row,arrival_offset_ms=arrival))
    out.sort(key=lambda r:(r['arrival_offset_ms'],r['req_id']))
    assert max(r['arrival_offset_ms'] for r in out)<12000
    structured.validate(out)
    return out

def model_free(cfg):
    old=frozen_sources(); descriptors=[]
    assert cfg['comparison']=={'model':'fixed_3B','instruction_arm':'C','serial_profile_requests':96,'load_rounds':9,'load_requests':216,'measured_requests':312,'warmup_requests':20,'completion_requests':332,'expected_cap_events':664}
    assert cfg['allowed_splits']==['calibration','development'] and cfg['final_test_generation_enabled'] is False
    assert [x['policy'] for x in cfg['execution_order']]==['original','simple','capped','simple','capped','original','capped','original','simple']
    assert (cfg['measurement_seconds'],cfg['drain_seconds'],cfg['request_timeout_seconds'])==(12,90,90)
    for split in cfg['allowed_splits']:
        for seed in cfg[split]['seeds']:
            one=workload.build_descriptors(cfg,split,seed)
            assert len(one)==24 and Counter((r['category'],r['input_token_bucket']) for r in one)=={(c,b):4 for c in ('interactive','background') for b in range(3)}
            minimal=[]
            for d in one:
                prompt=workload.render_candidate(d,0,'C')
                minimal.append(replay_row(dict(d,prompt=prompt,input_tokens=1,token_ids_sha256='model_free_not_tokenized',content_sha256=budget.text_sha(prompt),arm='C')))
            make_trace(minimal); descriptors+=one
    assert len(descriptors)==len({r['req_id'] for r in descriptors})==96
    for split in ('final','test'):
        try: workload.build_descriptors(cfg,split,99999)
        except ValueError: pass
        else: raise AssertionError('Final generation permitted')
    return {'status':'passed','model_started':False,'old_frozen_sources_unchanged':len(old),'profile_requests':96,'load_rounds':9,'load_requests':216,'measured_requests':312,'warmups':20,'completion_requests':332,'expected_cap_events':664,'final_test_accessed':False}

def safety(out,started,cfg):
    assert time.monotonic()-started<cfg['batch_timeout_seconds']
    assert shutil.disk_usage(out).free>=4*1024**3
    assert sum(p.stat().st_size for p in out.rglob('*') if p.is_file())<1024**3

def capture_gpu(path):
    with path.open('xb') as output:
        subprocess.run(['/usr/lib/wsl/lib/nvidia-smi'],stdout=output,stderr=subprocess.STDOUT,check=True,timeout=15)

async def start(directory,engine,model,policy):
    base.free_port()
    proc=subprocess.run(['/usr/lib/wsl/lib/nvidia-smi','--query-compute-apps=pid','--format=csv,noheader,nounits'],capture_output=True,text=True,check=True)
    base.dump(directory/'gpu-compute-process-preflight.json',{'stdout':proc.stdout,'stderr':proc.stderr}); assert not proc.stdout.strip()
    server,log=await v6.start_server(directory,engine,model,policy,5000)
    try:
        props=base.read(directory/'props.json')
        assert props['total_slots']==2 and props['default_generation_settings']['n_ctx']==2048
        assert re.search(r'offloaded 37/37 layers to GPU',(directory/'server.log').read_text(encoding='utf-8',errors='replace'))
        base.dump(directory/'ownership.json',{'pid':server.pid,'created_by_this_script':True,'actual_full_gpu_layers':37,'policy':policy})
        return server,log
    except BaseException:
        await base.prior.prior.engine.stop_server(server,log)
        raise

async def warm(directory,warm_plan):
    await structured.replay(warm_plan,directory/'warmup',base.prior.URL,1,15,15,50,360)
    audit=base.audit_replay(directory/'warmup',warm_plan)
    assert not audit['flags'] and audit['terminal_counts']['completed']==2
    await base.await_idle(directory,'after-warmup')
    base.dump(directory/'warmup-audit.json',audit)

async def finish(directory,server,log,expected_ids,policy,required_ids=None):
    await base.await_idle(directory,'after-all')
    await base.prior.prior.engine.stop_server(server,log)
    base.dump(directory/'service-stop.json',{'pid':server.pid,'exit_code':server.returncode,'stopped_owned_process_only':True})
    assert server.returncode==0
    accounting=base.gate.verify_cap_events(directory/'server.log',1 if policy=='capped' else 0)
    actual_ids={e['clientid'] for e in base.gate.cap_events(directory/'server.log')}
    assert set(expected_ids)>=actual_ids>=set(expected_ids if required_ids is None else required_ids)
    assert accounting['events']==len(actual_ids)*2
    base.dump(directory/'cap-pairs.json',budget.cap_pairs(directory/'server.log',sorted(actual_ids)))
    base.dump(directory/'accounting.json',accounting)
    return accounting

async def sample_metrics(directory,stop):
    with (directory/'metrics-samples.jsonl').open('x',encoding='utf-8') as output:
        while not stop.is_set():
            start_ns=time.monotonic_ns()
            try:
                def fetch():
                    with urllib.request.urlopen(base.prior.URL+'/metrics',timeout=5) as response: return response.read().decode('utf-8')
                raw=await asyncio.to_thread(fetch)
                record={'monotonic_ns':start_ns,'elapsed_ms':(time.monotonic_ns()-start_ns)/1e6,'response_body':raw}
            except Exception as exc: record={'monotonic_ns':start_ns,'error':repr(exc)}
            await asyncio.to_thread(output.write,json.dumps(record,ensure_ascii=False)+'\n')
            await asyncio.to_thread(output.flush)
            try: await asyncio.wait_for(stop.wait(),timeout=.25)
            except asyncio.TimeoutError: pass

async def collect(args,cfg):
    out=args.out_dir.resolve(); out.mkdir(parents=True,exist_ok=False)
    started=time.monotonic(); server=log=sampler=samplelog=tokenizer=None; active=None
    stages=[]
    base.dump(out/'batch-status.json',{'status':'preparing','started_at':datetime.now().astimezone().isoformat()})
    try:
        base.dump(out/'model-free-preflight.json',model_free(cfg)); assert shutil.disk_usage(out).free>=10*1024**3
        engine=args.engine_bin.resolve(strict=True); model=Path(cfg['model_path']).resolve(strict=True); engine_root=engine.parents[2]
        assert base.prior.git_output(engine_root,'rev-parse','HEAD').strip()==cfg['engine_commit']
        runtime=base.prior.binary_inventory(engine); assert runtime==base.read(RUN6/'engine-runtime-sha256.json')
        modelsha=await asyncio.to_thread(base.prior.sha,model); assert modelsha==cfg['model_sha256']
        old=frozen_sources(); hashes=dict(old); hashes.update({p:base.prior.sha(ROOT/p) for p in NEW_SOURCES}); assert len(hashes)==87
        for relative,digest in hashes.items():
            target=out/'source'/relative; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes((ROOT/relative).read_bytes())
        base.dump(out/'source-sha256.json',hashes)
        ehash=base.read(RUN6/'engine-source-sha256.json')
        for relative,digest in ehash.items():
            assert base.prior.sha(engine_root/relative)==digest
            target=out/'engine-source'/relative; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes((engine_root/relative).read_bytes())
        base.dump(out/'engine-source-sha256.json',ehash); base.dump(out/'engine-runtime-sha256.json',runtime)
        gate=ROOT/'results/raw/client-verification/20261025-p1-structured-client-v4-r2'; g=base.read(gate/'summary.json')
        assert g['status']=='passed' and all(base.prior.sha(ROOT/p)==h for p,h in g['source_sha256'].items())
        shutil.copytree(gate,out/'structured-client-gate')
        base.dump(out/'environment.json',{'recorded_at':datetime.now().astimezone().isoformat(),'python':sys.version,'kernel':Path('/proc/version').read_text(),'engine_binary':str(engine),'engine_commit':cfg['engine_commit'],'model':str(model),'model_sha256':modelsha,'config':cfg,'project_commit':base.prior.git_output(ROOT,'rev-parse','HEAD').strip(),'scope':'Fixed3B synthetic profiling and existing prototype policies; no final, G1 or M/B3.'})
        capture_gpu(out/'gpu-before.txt')
        samplelog=(out/'gpu-samples.csv').open('xb')
        sampler=await asyncio.create_subprocess_exec('/usr/lib/wsl/lib/nvidia-smi','--query-gpu=timestamp,memory.used,utilization.gpu,temperature.gpu,power.draw','--format=csv','--loop-ms=1000',stdout=samplelog,stderr=asyncio.subprocess.STDOUT)
        profile=out/'profile'; profile.mkdir(); active=profile
        server,log=await start(profile,engine,model,'original')
        tokenizer=base.Tokenizer(profile); refs=[]
        for split in cfg['allowed_splits']:
            for seed in cfg[split]['seeds']:
                one=await asyncio.to_thread(workload.materialize_trace,workload.build_descriptors(cfg,split,seed),tokenizer,cfg)
                assert len(one)==24
                refs+=one
        assert len(refs)==len({r['content_sha256'] for r in refs})==96
        oldhashes={r['content_sha256'] for r in base.rows(RUN6/'evaluation-plan.jsonl')}
        assert not oldhashes & {r['content_sha256'] for r in refs}
        plan=[replay_row(r) for r in refs]
        warm_prompt='<|im_start|>system\n你是简洁的助手。<|im_end|>\n<|im_start|>user\n请说准备好了。<|im_end|>\n<|im_start|>assistant\n'
        warmtokens=await asyncio.to_thread(tokenizer,warm_prompt)
        warmplan=[{'req_id':f'warm{i}','category':'interactive','arrival_offset_ms':i*500,'prompt':warm_prompt,'input_tokens':len(warmtokens),'max_tokens':8,'temperature':0,'ignore_eos':False,'cache_prompt':False} for i in range(2)]
        base.dump(profile/'tokenization-summary.json',{'calls':tokenizer.count,'unique_profile_prompts':96,'warmup_prompt':1,'final_test_generated_or_read':False})
        tokenizer.close(); tokenizer=None
        base.write_rows(out/'evaluation-plan.jsonl',refs); base.write_rows(profile/'plan.jsonl',plan); base.write_rows(out/'warmup-plan.jsonl',warmplan)
        await warm(profile,warmplan)
        observed=[]
        for index,row in enumerate(plan,1):
            safety(out,started,cfg)
            case=profile/'cases'/f'q{index:03d}-{row["req_id"]}'; case.mkdir(parents=True)
            base.dump(case/'payload-reconstruction.json',{'evidence_kind':'reconstruction_not_wire_capture','payload':structured.completion_payload(row)})
            await structured.replay([row],case/'replay',base.prior.URL,.01,45,45,50,360)
            audit=base.audit_replay(case/'replay',[row]); base.dump(case/'audit.json',audit)
            assert not audit['flags'] and audit['terminal_counts']['completed']==1
            actual=base.rows(case/'replay/client.jsonl')[0]; assert actual['cached_prompt_tokens']==0 and actual['cache_prompt'] is False
            assert actual['native_final']['generation_settings']['grammar']==row['grammar']
            await base.await_idle(case,'after-request')
            observed.append(actual)
            base.dump(out/'batch-status.json',{'status':'profiling','completed':index,'planned':96,'elapsed_seconds':time.monotonic()-started})
            print(f'PROFILE {index}/96 {row["req_id"]} output={actual["output_tokens"]} total_ms={actual["completion_from_planned_ms"]:.1f}',flush=True)
        acc=await finish(profile,server,log,[r['req_id'] for r in plan]+['warm0','warm1'],'original'); log=None; stages.append({'name':'profile','measured':96,'warmup':2,'accounting':acc})
        model_fit=estimator.select([r for r in observed if r['split']=='calibration'])
        base.dump(out/'calibration-model.json',model_fit)
        predictions=[{k:r[k] for k in ('req_id','split','category','task_kind','max_tokens','output_tokens')}|estimator.apply(r,model_fit) for r in observed]
        base.write_rows(out/'profile-predictions.jsonl',predictions)
        for spec in cfg['execution_order']:
            safety(out,started,cfg); assert all(base.prior.sha(ROOT/p)==h for p,h in hashes.items())
            seed,policy,repeat=spec['seed'],spec['policy'],spec['repeat']
            trace=make_trace([r for r in plan if r['split']=='development' and r['seed']==seed])
            name=f'r{repeat}-{policy}'; directory=out/'rounds'/name; directory.mkdir(parents=True); active=directory
            server,log=await start(directory,engine,model,policy)
            await warm(directory,warmplan)
            base.write_rows(directory/'plan.jsonl',trace)
            stop_metrics=asyncio.Event(); metric_job=asyncio.create_task(sample_metrics(directory,stop_metrics))
            try: await structured.replay(trace,directory/'replay',base.prior.URL,12,90,90,50,360)
            finally:
                stop_metrics.set(); await metric_job
            audit=base.audit_replay(directory/'replay',trace); base.dump(directory/'audit.json',audit)
            assert not audit['flags'],'Recording integrity failure; preserve and stop'
            for actual in base.rows(directory/'replay/client.jsonl'):
                if actual['terminal']=='completed':
                    assert actual['cached_prompt_tokens']==0 and actual['native_final']['generation_settings']['grammar']==actual['grammar']
            required=[r['req_id'] for r in base.rows(directory/'replay/client.jsonl') if r['terminal']=='completed']+['warm0','warm1']
            acc=await finish(directory,server,log,[r['req_id'] for r in trace]+['warm0','warm1'],policy,required); log=None
            stages.append({'name':name,**spec,'measured':24,'warmup':2,'terminal_counts':audit['terminal_counts'],'accounting':acc})
            base.dump(directory/'manifest.json',{'status':'completed','source_mapping':'../../source-sha256.json',**stages[-1]})
            base.dump(out/'batch-status.json',{'status':'load_running','rounds_completed':len(stages)-1,'rounds_planned':9,'elapsed_seconds':time.monotonic()-started})
            print(f'ROUND {len(stages)-1}/9 {name} {audit["terminal_counts"]}',flush=True)
        assert all(base.prior.sha(ROOT/p)==h for p,h in hashes.items())
        assert all(base.prior.sha(engine_root/p)==h for p,h in ehash.items())
        assert base.prior.binary_inventory(engine)==runtime
        assert await asyncio.to_thread(base.prior.sha,model)==modelsha
        base.dump(out/'fingerprints-after.json',{'source_unchanged':True,'engine_source_unchanged':True,'engine_runtime_sha256':runtime,'model_sha256':modelsha})
        summary={'status':'completed','study':cfg['study'],'profile_requests':96,'load_rounds':9,'load_requests':216,'measured_requests':312,'warmup_requests':20,'completion_requests':332,'cap_events':sum(s['accounting']['events'] for s in stages),'stages':stages,'source_count':87,'all_services_exit_zero':True,'final_test_generated_or_read':False,'G1_passed':False,'elapsed_seconds':time.monotonic()-started,'finished_at':datetime.now().astimezone().isoformat()}
        assert summary['cap_events']<=664
        base.dump(out/'summary.json',summary); base.dump(out/'batch-status.json',summary)
    except BaseException as exc:
        (out/'error-traceback.txt').write_text(traceback.format_exc(),encoding='utf-8')
        base.dump(out/'batch-status.json',{'status':'failed','error':repr(exc),'completed_stages':stages,'elapsed_seconds':time.monotonic()-started})
        raise
    finally:
        if tokenizer is not None: tokenizer.close()
        if server is not None and log is not None:
            await base.prior.prior.engine.stop_server(server,log)
            base.dump(active/'cleanup-after-failure.json',{'pid':server.pid,'exit_code':server.returncode,'owned_service_only':True})
        if sampler is not None and sampler.returncode is None: sampler.terminate(); await sampler.wait()
        if samplelog is not None: samplelog.close(); capture_gpu(out/'gpu-after.txt')

def main():
    p=argparse.ArgumentParser(); p.add_argument('--out-dir',type=Path); p.add_argument('--engine-bin',type=Path); p.add_argument('--model-free-check',action='store_true'); a=p.parse_args(); cfg=base.read(CONFIG)
    if a.model_free_check: print(json.dumps(model_free(cfg),ensure_ascii=False)); return
    if a.out_dir is None or a.engine_bin is None: p.error('--out-dir and --engine-bin required')
    asyncio.run(asyncio.wait_for(collect(a,cfg),cfg['batch_timeout_seconds']))

if __name__=='__main__': main()
