"""Serial format calibration; preserve source snapshots and freeze before development."""
import argparse,asyncio,hashlib,importlib.util,json,sys,time,traceback,urllib.request
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/'scripts/replay'))
spec=importlib.util.spec_from_file_location('v8',ROOT/'scripts/replay/79j_run_p1_target_mix_v8.py'); v8=importlib.util.module_from_spec(spec); spec.loader.exec_module(v8)
import p1_format_workload_v9 as workload
import p1_format_estimator_v9 as estimator
base=v8.base
NEW=['scripts/replay/79k_run_p1_format_calibration_v9.py','scripts/replay/p1_format_workload_v9.py','scripts/replay/p1_format_estimator_v9.py','configs/p1-format-calibration-v9.json','docs/p1/format-calibration-protocol-v9.md','scripts/analysis/101_analyze_p1_format_v9.py','tests/unit/test_p1_format_v9.py']
def format_feature(ref,directory):
 from p1_instruction_constraints_v5 import parse_public_task
 public=parse_public_task(ref['prompt'],ref['category'])
 text='\n'.join(i+'|'+('一致' if public['task_kind']=='check' else '50') for i in public['target_ids'])
 body={'content':text,'add_special':False,'parse_special':False}; started=time.monotonic_ns()
 req=urllib.request.Request(base.prior.URL+'/tokenize',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=30) as response: raw=response.read().decode(); assert response.status==200
 tokens=json.loads(raw)['tokens']; assert tokens and all(type(x)is int for x in tokens)
 record={'req_id':ref['req_id'],'request':body,'response_body':raw,'format_tokens':len(tokens),'elapsed_ms':(time.monotonic_ns()-started)/1e6,'oracle_read':False}
 with (directory/'format-tokenization.jsonl').open('a',encoding='utf-8') as f: f.write(json.dumps(record,ensure_ascii=False)+'\n')
 return len(tokens)
async def collect(out,engine):
 out=out.resolve(); out.mkdir(parents=True,exist_ok=False); cfg=base.read(ROOT/'configs/p1-format-calibration-v9.json'); started=time.monotonic(); server=log=tokenizer=None
 try:
  hashes=base.read(ROOT/'results/raw/development/20261026-0845-p1-target-mix-v8-r2/source-sha256.json')
  hashes.update({p:base.prior.sha(ROOT/p) for p in NEW})
  for p,h in hashes.items():
   assert base.prior.sha(ROOT/p)==h
   dest=out/'source'/p; dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes((ROOT/p).read_bytes())
  base.dump(out/'source-sha256.json',hashes)
  model=Path(cfg['model_path']); assert base.prior.sha(model)==cfg['model_sha256']
  runtime=base.prior.binary_inventory(engine); assert runtime==base.read(v8.RUN7/'engine-runtime-sha256.json')
  ehash=base.read(v8.RUN7/'engine-source-sha256.json'); assert all(base.prior.sha(engine.parents[2]/p)==h for p,h in ehash.items())
  base.dump(out/'environment.json',{'started_at':datetime.now().astimezone().isoformat(),'config':cfg,'engine_runtime_sha256':runtime,'engine_source_sha256':ehash,'model_sha256':cfg['model_sha256'],'final_test_accessed':False})
  v8.safety(out,started,cfg); assert v8.shutil.disk_usage(out).free>=10*1024**3
  server,log=await v8.start(out,engine,model,'original'); tokenizer=base.Tokenizer(out)
  refs=[]
  for split in cfg['allowed_splits']:
   for seed in cfg[split]['seeds']: refs+=await asyncio.to_thread(workload.materialize_trace,workload.build_descriptors(cfg,split,seed),tokenizer,cfg)
  assert len(refs)==len({r['content_sha256'] for r in refs})==96
  prior={r['content_sha256'] for r in base.rows(ROOT/'results/raw/development/20261026-0845-p1-target-mix-v8-r2/evaluation-plan.jsonl')}; assert not prior & {r['content_sha256'] for r in refs}
  for ref in refs: ref['format_tokens']=await asyncio.to_thread(format_feature,ref,out)
  base.write_rows(out/'evaluation-plan.jsonl',refs)
  plan=[v8.replay_row(r) for r in refs]; base.write_rows(out/'plan.jsonl',plan)
  warm=base.rows(ROOT/'results/raw/development/20261026-0845-p1-target-mix-v8-r2/warmup-plan.jsonl'); await v8.warm(out,warm)
  tokenizer.close(); tokenizer=None; observed=[]
  for index,(ref,row) in enumerate(zip(refs,plan),1):
   v8.safety(out,started,cfg)
   if index==49:
    model_fit=estimator.select(observed); base.dump(out/'calibration-model.json',model_fit)
    base.write_rows(out/'development-predictions-before-data.jsonl',[{'req_id':r['req_id'],**estimator.apply(r,model_fit)} for r in refs[48:]])
   case=out/'cases'/f'q{index:03d}'; case.mkdir(parents=True)
   await v8.structured.replay([row],case/'replay',base.prior.URL,.01,45,45,50,360)
   audit=base.audit_replay(case/'replay',[row]); base.dump(case/'audit.json',audit); assert not audit['flags'] and audit['terminal_counts']['completed']==1
   actual=base.rows(case/'replay/client.jsonl')[0]; assert actual['cached_prompt_tokens']==0
   observed.append({**ref,'output_tokens':actual['output_tokens']})
   await base.await_idle(case,'after-request')
   base.dump(out/'batch-status.json',{'status':'running','completed':index,'planned':96,'elapsed_seconds':time.monotonic()-started})
   print(f'CASE {index}/96 {ref["split"]} {ref["req_id"]} output={actual["output_tokens"]} format={ref["format_tokens"]}',flush=True)
  acc=await v8.finish(out,server,log,[r['req_id'] for r in plan]+['warm0','warm1'],'original'); log=None
  assert all(base.prior.sha(ROOT/p)==h for p,h in hashes.items()); assert base.prior.binary_inventory(engine)==runtime and base.prior.sha(model)==cfg['model_sha256']
  assert all(base.prior.sha(engine.parents[2]/p)==h for p,h in ehash.items())
  base.dump(out/'summary.json',{'status':'completed','measured':96,'calibration':48,'development':48,'warmups':2,'cap_events':acc['events'],'elapsed_seconds':time.monotonic()-started,'source_unchanged':True,'model_runtime_engine_unchanged':True,'service_exit_zero':True,'final_test_accessed':False,'G1_passed':False,'finished_at':datetime.now().astimezone().isoformat()})
 except BaseException:
  (out/'error-traceback.txt').write_text(traceback.format_exc(),encoding='utf-8'); raise
 finally:
  if tokenizer is not None: tokenizer.close()
  if server is not None and log is not None: await base.prior.prior.engine.stop_server(server,log)
if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--out-dir',type=Path,required=True); p.add_argument('--engine-bin',type=Path,required=True); a=p.parse_args()
 asyncio.run(asyncio.wait_for(collect(a.out_dir,a.engine_bin.resolve(strict=True)),1800))
