"""Resume valid calibration after technical failure; never relabel reused data."""
import argparse,asyncio,hashlib,importlib.util,json,sys,time,traceback,shutil
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/'scripts/replay'))
s=importlib.util.spec_from_file_location('parent_helpers',ROOT/'scripts/replay/79m_run_p1_live_mechanism_v11.py'); parent=importlib.util.module_from_spec(s); s.loader.exec_module(parent)
v8,base,client,Controller=parent.v8,parent.base,parent.client,parent.Controller
PARENT=ROOT/'results/raw/development/20261026-1500-p1-live-mechanism-v11'
NEW=parent.NEW+['scripts/replay/79n_resume_p1_live_mechanism_v11_r2.py','docs/p1/live-mechanism-retry-v11-r2.md','scripts/analysis/106_analyze_p1_live_mechanism_v11_r2.py','tests/integration/audit_p1_live_readiness_v11_r2.py']
async def collect(out,engine):
 out=out.resolve(); out.mkdir(parents=True,exist_ok=False); started=time.monotonic(); cfg=base.read(ROOT/'configs/p1-live-mechanism-v11.json'); server=log=gpu=gpulog=None
 try:
  prep=base.read(ROOT/'results/raw/maintenance/20261026-p1-live-mechanism-preparation-v11-r2/summary.json'); assert prep['status']=='passed'
  for name,h in prep['source_sha256'].items(): assert base.prior.sha(ROOT/name)==h
  hashes=base.read(PARENT/'source-sha256.json'); hashes.update({p:base.prior.sha(ROOT/p) for p in NEW})
  for name,h in hashes.items():
   assert base.prior.sha(ROOT/name)==h; target=out/'source'/name; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes((ROOT/name).read_bytes())
  base.dump(out/'source-sha256.json',hashes); model=Path(cfg['model_path']); assert await asyncio.to_thread(base.prior.sha,model)==cfg['model_sha256']; runtime=base.prior.binary_inventory(engine); assert runtime==base.read(v8.RUN7/'engine-runtime-sha256.json')
  assert shutil.disk_usage(out).free>=10*1024**3
  copied={}
  for p in (PARENT/'calibration').rglob('*'):
   if p.is_file(): copied[p.relative_to(PARENT).as_posix()]=base.prior.sha(p)
  shutil.copytree(PARENT/'calibration',out/'calibration')
  for name in ['evaluation-plan.jsonl','public-plan.jsonl','prior-calibration-plan.jsonl','preflight.json','history-corpus-sha256.json','point-model.json','warm-history-sources.jsonl','calibration-artifact-before-development.json','materialization-attempts.jsonl']:
   shutil.copyfile(PARENT/name,out/name); copied[name]=base.prior.sha(PARENT/name)
  assert all(base.prior.sha(out/name)==h for name,h in copied.items()); base.dump(out/'reused-evidence-sha256.json',{'parent_run':PARENT.name,'new_calibration_in_this_attempt':0,'reused_calibration_measured':144,'files':copied})
  base.dump(out/'environment.json',{'started_at':datetime.now().astimezone().isoformat(),'config':cfg,'model_sha256':cfg['model_sha256'],'runtime':runtime,'source_parent':PARENT.name,'scope':'technical retry with reused calibration; shared client-side prototype','G1_passed':False,'final_test_accessed':False})
  gpulog=(out/'gpu-samples.csv').open('xb'); gpu=await asyncio.create_subprocess_exec('/usr/lib/wsl/lib/nvidia-smi','--query-gpu=timestamp,memory.used,utilization.gpu,temperature.gpu,power.draw','--format=csv','--loop-ms=1000',stdout=gpulog,stderr=asyncio.subprocess.STDOUT)
  artifact=base.read(out/'calibration-artifact-before-development.json'); reference=artifact['reference']; input_k=artifact['input_cost_coefficient']; output_k=artifact['output_cost_coefficient']; public=base.rows(out/'public-plan.jsonl'); histories=defaultdict(list)
  for r in base.rows(out/'warm-history-sources.jsonl'): histories[(r['category'],r['task_kind'])].append(r['error'])
  assert all(len(v)==32 for v in histories.values()); warm=base.rows(ROOT/'results/raw/development/20261026-0845-p1-target-mix-v8-r2/warmup-plan.jsonl'); rounds=[]
  async def run_round(name,seed,policy,multiplier,phase):
   nonlocal server,log
   v8.safety(out,started,cfg); assert all(base.prior.sha(ROOT/p)==h for p,h in hashes.items()); directory=out/'rounds'/name; directory.mkdir(parents=True); server,log=await v8.start(directory,engine,model,'original'); await v8.warm(directory,warm)
   source=[dict(r,input_cost_coefficient=input_k,output_cost_coefficient=output_k) for r in public if r['split']=='development' and r['seed']==seed]; trace=v8.target_mix.expand_trace(source); base.write_rows(directory/'plan.jsonl',trace)
   controller=Controller(policy,reference*multiplier,histories,directory/'server.log'); result=await client.replay(trace,directory/'replay',base.prior.URL,100,180,180,50,360,controller=controller)
   audit=base.audit_replay(directory/'replay',trace); base.dump(directory/'recording-audit.json',audit); assert not audit['flags'] and not controller.fatal
   actual=base.rows(directory/'replay/client.jsonl'); assert not any(r.get('control_error') for r in actual)
   accounting=await v8.finish(directory,server,log,[r['req_id'] for r in trace]+['warm0','warm1'],'original',[r['req_id'] for r in actual if r['terminal']=='completed']+['warm0','warm1']); log=None
   events=base.rows(directory/'replay/control/events.jsonl'); reasons=Counter(e['reason'] for e in events if e['event']=='permit' and e['category']=='background')
   own_fits=sum(r['category']=='background' and r['input_tokens']*input_k+min(r['max_tokens'],max(r['prediction'],r['grammar_ceiling'] or 0))*output_k<=reference*multiplier for r in trace)
   item={'name':name,'seed':seed,'policy':policy,'phase':phase,'multiplier':multiplier,'terminal_counts':result['terminal_counts'],'background_permit_reasons':dict(reasons),'background_own_cost_fits':own_fits,'accounting':accounting}; rounds.append(item); base.dump(directory/'manifest.json',item); base.dump(out/'batch-status.json',{'status':'running_rounds','rounds_completed':len(rounds),'last_round':item,'elapsed_seconds':time.monotonic()-started}); print('ROUND '+json.dumps(item),flush=True); return item
  selected=None
  for multiplier in (1.,1.5):
   item=await run_round(f'pilot-{str(multiplier).replace(".","p")}',32100,'B3_0',multiplier,'budget_feasibility')
   reasons=item['background_permit_reasons']
   if item['terminal_counts']['completed']==230 and reasons.get('normal',0)+reasons.get('empty',0)>=15 and item['background_own_cost_fits']>=15: selected=multiplier; break
  base.dump(out/'budget-selection-before-comparison.json',{'selected_multiplier':selected,'rule':'first1.0/1.5: all230 complete, >=15/30 non-aged, >=15/30 own-cost fits; preregistered before parent probe result','M_outcomes_used':False,'pre_probe_review_sha256':base.prior.sha(ROOT/'results/raw/maintenance/20261026-p1-live-mechanism-preparation-v11/pre-probe-design-review.json')})
  if selected is not None:
   for seed,order in [(32101,['B3_0','B3_1','M']),(32102,['M','B3_1','B3_0'])]:
    for policy in order: await run_round(f'{seed}-{policy}',seed,policy,selected,'paired_development')
  assert all(base.prior.sha(ROOT/p)==h for p,h in hashes.items()) and base.prior.binary_inventory(engine)==runtime and await asyncio.to_thread(base.prior.sha,model)==cfg['model_sha256']
  base.dump(out/'summary.json',{'status':'completed' if selected is not None else 'completed_feasibility_not_met','reused_calibration':144,'new_calibration_in_this_attempt':0,'rounds':rounds,'measured_requests':144+230*len(rounds),'new_measured_in_this_attempt':230*len(rounds),'warmups':2*(len(rounds)+1),'new_warmups_in_this_attempt':2*len(rounds),'cap_events':292+sum(r['accounting']['events'] for r in rounds),'new_cap_events_in_this_attempt':sum(r['accounting']['events'] for r in rounds),'selected_multiplier':selected,'elapsed_seconds':time.monotonic()-started,'source_model_runtime_unchanged':True,'scope':'client prototype technical retry; study totals include copied144 calibration once','G1_passed':False,'final_test_accessed':False,'finished_at':datetime.now().astimezone().isoformat()})
 except BaseException:
  (out/'error-traceback.txt').write_text(traceback.format_exc(),encoding='utf-8'); raise
 finally:
  if server is not None and log is not None: await base.prior.prior.engine.stop_server(server,log)
  if gpu is not None and gpu.returncode is None: gpu.terminate(); await gpu.wait()
  if gpulog is not None: gpulog.close()
if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--out-dir',type=Path,required=True); p.add_argument('--engine-bin',type=Path,required=True); a=p.parse_args(); asyncio.run(asyncio.wait_for(collect(a.out_dir,a.engine_bin.resolve(strict=True)),7200))
