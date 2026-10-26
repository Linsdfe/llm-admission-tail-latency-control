"""Supplementary independent public-feature, calibration and probe-selection audit.

Written during calibration, before live-prototype comparison; no thresholds change.
"""
import argparse,json,statistics,math,hashlib,itertools
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def read(p): return json.loads(p.read_text(encoding='utf-8'))
def rows(p): return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x]
def audit(run):
 checks=0
 def check(x,label):
  nonlocal checks
  checks+=1
  if not x: raise AssertionError(label)
 refs=rows(run/'evaluation-plan.jsonl'); prior=rows(run/'prior-calibration-plan.jsonl'); all_refs={r['req_id']:r for r in refs+prior}; features=rows(run/'calibration/format-tokenization.jsonl'); branch=rows(run/'calibration/grammar-branches.jsonl')
 check(len(features)==len(all_refs)==264,'all public features before measurements')
 for f in features:
  r=all_refs[f['req_id']]; placeholder='\n'.join(i+'|'+('一致' if r['task_kind']=='check' else '50') for i in r['target_ids'])
  check(f['request']=={'content':placeholder,'add_special':False,'parse_special':False},'placeholder independent of answer')
  check(len(json.loads(f['response_body'])['tokens'])==r['format_tokens']==f['format_tokens'],'feature token count')
 ceilings={}
 for b in branch:
  r=all_refs[b['req_id']]; ids=r['target_ids']; expected={'\n'.join(i+'|'+v for i,v in zip(ids,choice)) for choice in itertools.product(('一致','不一致'),repeat=len(ids))}
  check({item['request']['content'] for item in b['branches']}==expected and len(b['branches'])==2**len(ids),'all public grammar branches')
  check(all(not item['request']['add_special'] and not item['request']['parse_special'] for item in b['branches']),'literal grammar tokenization')
  ceiling=max(len(json.loads(item['response_body'])['tokens']) for item in b['branches'])+2; check(ceiling==b['ceiling'],'branch ceiling'); ceilings[tuple(ids)]=ceiling
 model=read(run/'point-model.json'); check(model==read(ROOT/'results/raw/development/20261026-0930-p1-format-calibration-v9-r2/calibration-model.json'),'point model unchanged')
 for r in refs+prior:
  check(r['prediction']==min(r['max_tokens'],r['format_tokens']+model['values'][r['task_kind']]),'public point prediction')
  check(r['grammar_ceiling']==(ceilings[tuple(r['target_ids'])] if r['task_kind']=='check' else None),'shared public ceiling')
 calrefs=[r for r in refs if r['split']=='calibration']; pre=[]; decode=[]
 for i,r in enumerate(calrefs,1):
  a=rows(run/'calibration/cases'/f'q{i:03d}'/'replay/client.jsonl')[0]; check(a['source_req_id']==r['req_id'],'calibration mapping'); pre.append(a['timings']['prompt_ms']/a['input_tokens']); decode.append(a['timings']['predicted_ms']/a['output_tokens'])
 input_k=statistics.median(pre); output_k=statistics.median(decode); artifact=read(run/'calibration-artifact-before-development.json')
 check(input_k==artifact['input_cost_coefficient'] and output_k==artifact['output_cost_coefficient'],'cost fit calibration only')
 values={c:sorted(r['input_tokens']*input_k+min(r['max_tokens'],max(r['prediction'],r['grammar_ceiling'] or 0))*output_k for r in calrefs if r['category']==c) for c in ('interactive','background')}
 for c,x in values.items():
  h=(len(x)-1)*.75; lo=int(h); value=x[lo]+(x[min(lo+1,len(x)-1)]-x[lo])*(h-lo); check(abs(value-artifact['class_p75'][c])<1e-8,'class percentile')
 check(artifact['reference']==max(artifact['class_p75'].values()),'reference considers both classes')
 summary=read(run/'summary.json'); pilot=[r for r in summary['rounds'] if r['phase']=='budget_feasibility']; comparison=[r for r in summary['rounds'] if r['phase']=='paired_development']
 selected=summary['selected_multiplier']; check(not pilot and len(comparison)==6 and selected==1.0,'fixed inherited budget no v12 search')
 check([(r['seed'],r['policy']) for r in comparison]==[(32101,p) for p in ('B3_0','B3_1','M')]+[(32102,p) for p in ('M','B3_1','B3_0')],'same primary order')
 for item in summary['rounds']:
  directory=run/'rounds'/item['name']; actual={r['req_id']:r for r in rows(directory/'replay/client.jsonl')}; events=rows(directory/'replay/control/events.jsonl'); last_decision={}; initial=next(e for e in events if e['event']=='initial'); check(all(len(h)==32 for h in initial['history'].values()),'32 actual warm samples every round')
  for e in events:
   if e['event']=='decision': last_decision[e['req_id']]=e
   elif e['event']=='permit':
    d=last_decision[e['req_id']]; chosen=d['shadow'][item['policy']]; check(chosen['admit'] and chosen['reason']==e['reason'] and chosen['cost']==e['cost'],'actual permit follows selected policy')
   elif e['event']=='feedback':
    a=actual[e['req_id']]; check(a['native_release_confirm_offset_ms']<=e['observed_ms'] and a['end_offset_ms']<=a['native_release_confirm_offset_ms'],'real release then feedback')
 return {'status':'passed','checks':checks,'scope':'Public features and r2 supplementary rule registered before original probe result; reused calibration not new samples.','G1_passed':False,'final_test_accessed':False}
if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--run-dir',type=Path,required=True); p.add_argument('--out',type=Path,required=True); a=p.parse_args(); result=audit(a.run_dir)
 with a.out.open('x',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2)
 print(json.dumps(result,ensure_ascii=False))
