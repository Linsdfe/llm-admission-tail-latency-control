"""Bounded P1 real-feedback client-side prototype, never formal C++ M/B3."""
import argparse,asyncio,hashlib,importlib.util,json,sys,time,traceback,shutil
from collections import defaultdict,Counter
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/'scripts/replay'))
def module(name,path):
 s=importlib.util.spec_from_file_location(name,ROOT/path); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
v8=module('live_v8_helpers','scripts/replay/79j_run_p1_target_mix_v8.py'); v9=module('live_v9_features','scripts/replay/79k_run_p1_format_calibration_v9.py'); v10=module('live_v10_branches','scripts/replay/79l_run_p1_grammar_upper_v10.py')
base=v8.base
import p1_live_workload_v11 as workload
import p1_controlled_replay_v11 as client
import p1_preflight_v11 as preflight
from p1_live_control_v11 import Controller,quantile
NEW=['scripts/replay/79m_run_p1_live_mechanism_v11.py','scripts/replay/p1_live_workload_v11.py','scripts/replay/p1_controlled_replay_v11.py','scripts/replay/p1_live_control_v11.py','scripts/replay/p1_preflight_v11.py','configs/p1-live-mechanism-v11.json','docs/p1/live-mechanism-protocol-v11.md','tests/unit/test_p1_live_mechanism_v11.py','tests/integration/verify_p1_controlled_client_v11.py','scripts/analysis/105_analyze_p1_live_mechanism_v11.py']
PRIOR=ROOT/'results/raw/development/20261026-0930-p1-format-calibration-v9-r2'
def history_sources(out):
 result={}; hashes={}
 for p in sorted((ROOT/'results/raw').rglob('evaluation-plan.jsonl')):
  if out in p.parents: continue
  hashes[p.relative_to(ROOT).as_posix()]=base.prior.sha(p)
  for r in base.rows(p): result[preflight.digest(r['prompt'])]=r['req_id']
 return result,hashes
async def collect(out,engine):
 out=out.resolve(); out.mkdir(parents=True,exist_ok=False); started=time.monotonic(); cfg=base.read(ROOT/'configs/p1-live-mechanism-v11.json'); server=log=tokenizer=gpu=gpulog=None; active=out
 try:
  hashes=base.read(ROOT/'results/raw/development/20261026-1035-p1-grammar-upper-v10-r2/source-sha256.json'); hashes.update({p:base.prior.sha(ROOT/p) for p in NEW})
  gate=base.read(ROOT/'results/raw/maintenance/20261026-p1-live-mechanism-preparation-v11/summary.json'); assert gate['status']=='passed'
  for name,h in gate['source_sha256'].items(): assert base.prior.sha(ROOT/name)==h
  for name,h in hashes.items():
   assert base.prior.sha(ROOT/name)==h; target=out/'source'/name; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes((ROOT/name).read_bytes())
  base.dump(out/'source-sha256.json',hashes); model=Path(cfg['model_path']); assert await asyncio.to_thread(base.prior.sha,model)==cfg['model_sha256']
  runtime=base.prior.binary_inventory(engine); assert runtime==base.read(v8.RUN7/'engine-runtime-sha256.json'); engine_hashes=base.read(v8.RUN7/'engine-source-sha256.json'); assert all(base.prior.sha(engine.parents[2]/p)==h for p,h in engine_hashes.items())
  assert shutil.disk_usage(out).free>=10*1024**3
  base.dump(out/'environment.json',{'started_at':datetime.now().astimezone().isoformat(),'config':cfg,'engine_runtime':runtime,'engine_source':engine_hashes,'model_sha256':cfg['model_sha256'],'scope':'P1 shared external client-side prototype, not formal C++ M/B3 or hard KV implementation','G1_passed':False,'final_test_accessed':False})
  gpulog=(out/'gpu-samples.csv').open('xb'); gpu=await asyncio.create_subprocess_exec('/usr/lib/wsl/lib/nvidia-smi','--query-gpu=timestamp,memory.used,utilization.gpu,temperature.gpu,power.draw','--format=csv','--loop-ms=1000',stdout=gpulog,stderr=asyncio.subprocess.STDOUT)
  active=out/'calibration'; active.mkdir(); server,log=await v8.start(active,engine,model,'original'); tokenizer=base.Tokenizer(active)
  known,history_hashes=history_sources(out); base.dump(out/'history-corpus-sha256.json',history_hashes); seen=set(known); refs=[]; attempts=[]
  for split in cfg['allowed_splits']:
   for seed in cfg[split]['seeds']:
    for descriptor in workload.build_descriptors(cfg,split,seed):
     for nonce in range(100):
      descriptor['resample_nonce']=nonce
      one=(await asyncio.to_thread(workload.materialize_trace,[descriptor],tokenizer,cfg))[0]; h=one['content_sha256']; attempts.append({'req_id':one['req_id'],'nonce':nonce,'sha256':h,'collision':h in seen})
      if h not in seen: seen.add(h); refs.append(one); break
     else: raise ValueError('No distinct source after100 registered fact resamples')
  prior=base.rows(PRIOR/'evaluation-plan.jsonl')[:48]; base.dump(out/'preflight.json',preflight.validate(refs,set(known),prior)); base.write_rows(out/'materialization-attempts.jsonl',attempts)
  model_fit=base.read(PRIOR/'calibration-model.json'); base.dump(out/'point-model.json',model_fit)
  all_refs=refs+prior; branch_cache={}
  for ref in all_refs:
   ref['format_tokens']=await asyncio.to_thread(v9.format_feature,ref,active); ref['prediction']=min(ref['max_tokens'],ref['format_tokens']+model_fit['values'][ref['task_kind']])
   if ref['task_kind']=='check':
    key=tuple(ref['target_ids'])
    if key not in branch_cache: branch_cache[key]=await asyncio.to_thread(v10.grammar_ceiling,ref,active)
    ref['grammar_ceiling']=branch_cache[key]
   else: ref['grammar_ceiling']=None
  base.write_rows(out/'evaluation-plan.jsonl',refs); base.write_rows(out/'prior-calibration-plan.jsonl',prior)
  public=[]
  for r in refs:
   row=v8.replay_row(r); row.update(prediction=r['prediction'],grammar_ceiling=r['grammar_ceiling']); public.append(row)
  base.write_rows(out/'public-plan.jsonl',public)
  warm=base.rows(ROOT/'results/raw/development/20261026-0845-p1-target-mix-v8-r2/warmup-plan.jsonl'); await v8.warm(active,warm); tokenizer.close(); tokenizer=None
  old_observed=[]
  for i,r in enumerate(prior,1):
   path=PRIOR/'cases'/f'q{i:03d}'/'replay/client.jsonl'; row=base.rows(path)[0]; assert row['terminal']=='completed' and row['output_tokens_known']; old_observed.append((r,row,path.relative_to(ROOT).as_posix(),base.prior.sha(path)))
  observed=[]
  calplan=[r for r in public if r['split']=='calibration']; assert len(calplan)==144
  for i,row in enumerate(calplan,1):
   v8.safety(out,started,cfg); case=active/'cases'/f'q{i:03d}'; case.mkdir(parents=True); await v8.structured.replay([row],case/'replay',base.prior.URL,.01,45,45,50,360)
   result=base.audit_replay(case/'replay',[row]); base.dump(case/'audit.json',result); assert not result['flags'] and result['terminal_counts']['completed']==1
   actual=base.rows(case/'replay/client.jsonl')[0]; assert actual['cached_prompt_tokens']==0; observed.append((next(r for r in refs if r['req_id']==row['req_id']),actual,(case/'replay/client.jsonl').relative_to(ROOT).as_posix(),base.prior.sha(case/'replay/client.jsonl')))
   await base.await_idle(case,'after-calibration'); base.dump(out/'batch-status.json',{'status':'calibrating','completed':i,'planned':144,'elapsed_seconds':time.monotonic()-started}); print(f'CAL {i}/144',flush=True)
  calibration_accounting=await v8.finish(active,server,log,[r['req_id'] for r in calplan]+['warm0','warm1'],'original'); log=None
  from statistics import median
  input_k=median(a['timings']['prompt_ms']/a['input_tokens'] for _,a,_,_ in observed); output_k=median(a['timings']['predicted_ms']/a['output_tokens'] for _,a,_,_ in observed)
  histories=defaultdict(list); feedback_sources=[]
  for r,a,path,h in old_observed+observed:
   error=max(0,a['output_tokens']-r['prediction']); histories[(r['category'],r['task_kind'])].append(error); feedback_sources.append({'req_id':r['req_id'],'category':r['category'],'task_kind':r['task_kind'],'prediction':r['prediction'],'actual':a['output_tokens'],'error':error,'source_path':path,'sha256':h})
  assert len(histories)==6 and all(len(v)==32 for v in histories.values())
  costs=defaultdict(list)
  for r,a,_,_ in observed:
   length=min(r['max_tokens'],max(r['prediction'],r['grammar_ceiling'] or 0)); costs[r['category']].append(r['input_tokens']*input_k+length*output_k)
  class75={c:quantile(v,.75) for c,v in costs.items()}; reference=max(class75.values())
  base.write_rows(out/'warm-history-sources.jsonl',feedback_sources); base.dump(out/'calibration-artifact-before-development.json',{'input_cost_coefficient':input_k,'output_cost_coefficient':output_k,'class_p75':class75,'reference':reference,'history_counts':{':'.join(k):len(v) for k,v in histories.items()},'new_calibration_measured':144,'prior_calibration':48,'point_model_refit':False})
  rounds=[]
  async def run_round(name,seed,policy,multiplier,phase):
   nonlocal server,log,active
   v8.safety(out,started,cfg); assert all(base.prior.sha(ROOT/p)==h for p,h in hashes.items()); active=out/'rounds'/name; active.mkdir(parents=True); server,log=await v8.start(active,engine,model,'original'); await v8.warm(active,warm)
   source=[]
   for r in public:
    if r['split']=='development' and r['seed']==seed: source.append(dict(r,input_cost_coefficient=input_k,output_cost_coefficient=output_k))
   trace=v8.target_mix.expand_trace(source); base.write_rows(active/'plan.jsonl',trace); controller=Controller(policy,reference*multiplier,histories,active/'server.log')
   result=await client.replay(trace,active/'replay',base.prior.URL,100,180,180,50,360,controller=controller)
   audit=base.audit_replay(active/'replay',trace); base.dump(active/'recording-audit.json',audit); assert not audit['flags'] and not controller.fatal
   actual=base.rows(active/'replay/client.jsonl'); assert not any(r.get('control_error') for r in actual)
   accounting=await v8.finish(active,server,log,[r['req_id'] for r in trace]+['warm0','warm1'],'original',[r['req_id'] for r in actual if r['terminal']=='completed']+['warm0','warm1']); log=None
   events=base.rows(active/'replay/control/events.jsonl'); reasons=Counter(e['reason'] for e in events if e['event']=='permit' and e['category']=='background')
   item={'name':name,'seed':seed,'policy':policy,'phase':phase,'multiplier':multiplier,'terminal_counts':result['terminal_counts'],'background_permit_reasons':dict(reasons),'recording_integrity':True,'accounting':accounting}; rounds.append(item); base.dump(active/'manifest.json',item)
   base.dump(out/'batch-status.json',{'status':'running_rounds','rounds_completed':len(rounds),'last_round':item,'elapsed_seconds':time.monotonic()-started}); print('ROUND '+json.dumps(item),flush=True); return item
  selected=None
  for multiplier in (1.,1.5):
   item=await run_round(f'pilot-{str(multiplier).replace(".","p")}',32100,'B3_0',multiplier,'budget_feasibility')
   if item['terminal_counts']['completed']==230 and item['background_permit_reasons'].get('normal',0)>=15: selected=multiplier; break
  base.dump(out/'budget-selection-before-comparison.json',{'selected_multiplier':selected,'rule':'first of1.0/1.5 with all230 complete and >=15/30 normal background permits','M_outcomes_used':False})
  if selected is not None:
   for seed,order in [(32101,['B3_0','B3_1','M']),(32102,['M','B3_1','B3_0'])]:
    for policy in order: await run_round(f'{seed}-{policy}',seed,policy,selected,'paired_development')
  assert all(base.prior.sha(ROOT/p)==h for p,h in hashes.items()) and base.prior.binary_inventory(engine)==runtime and await asyncio.to_thread(base.prior.sha,model)==cfg['model_sha256']
  base.dump(out/'summary.json',{'status':'completed' if selected is not None else 'completed_feasibility_not_met','new_calibration':144,'rounds':rounds,'measured_requests':144+230*len(rounds),'warmups':2*(len(rounds)+1),'cap_events':calibration_accounting['events']+sum(r['accounting']['events'] for r in rounds),'selected_multiplier':selected,'elapsed_seconds':time.monotonic()-started,'source_model_runtime_unchanged':True,'scope':'client-side P1 prototype, no formal C++ M/B3 claim','G1_passed':False,'final_test_accessed':False,'finished_at':datetime.now().astimezone().isoformat()})
 except BaseException:
  (out/'error-traceback.txt').write_text(traceback.format_exc(),encoding='utf-8'); raise
 finally:
  if tokenizer is not None: tokenizer.close()
  if server is not None and log is not None: await base.prior.prior.engine.stop_server(server,log)
  if gpu is not None and gpu.returncode is None: gpu.terminate(); await gpu.wait()
  if gpulog is not None: gpulog.close()
if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--out-dir',type=Path,required=True); p.add_argument('--engine-bin',type=Path,required=True); a=p.parse_args(); asyncio.run(asyncio.wait_for(collect(a.out_dir,a.engine_bin.resolve(strict=True)),7200))
