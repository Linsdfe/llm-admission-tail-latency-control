"""Independent record/state audit for the client prototype, not a formal M/B3 trial."""
import argparse,importlib.util,json,hashlib,math,statistics
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def load(name,path):
 s=importlib.util.spec_from_file_location(name,ROOT/path); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
old=load('live_old99','scripts/analysis/99_analyze_p1_system_workload_v7.py'); cap=load('live_cap_gate','tests/integration/verify_background_cap_engine.py')
read,rows,sha=old.read,old.rows,old.sha
def percentile(values,p):
 x=sorted(values); h=(len(x)-1)*p; lo=int(h); return x[lo]+(x[min(lo+1,len(x)-1)]-x[lo])*(h-lo) if x else None
def analyze(run,out):
 run=run.resolve(strict=True); checks=0
 def check(x,label):
  nonlocal checks
  checks+=1
  if not x: raise AssertionError(label)
 reuse=read(run/'reused-evidence-sha256.json')
 for name,h in reuse['files'].items():
  assert sha(run/name)==h and sha(ROOT/'results/raw/development'/reuse['parent_run']/name)==h,'reused evidence changed'
 summary=read(run/'summary.json'); check(summary['status'] in ('completed','completed_feasibility_not_met'),'batch status')
 for name,h in read(run/'source-sha256.json').items(): check(sha(ROOT/name)==sha(run/'source'/name)==h,'source '+name)
 refs=rows(run/'evaluation-plan.jsonl'); refmap={r['req_id']:r for r in refs}; check(len(refs)==len({r['content_sha256'] for r in refs})==216,'new corpus')
 historical=set()
 for name,h in read(run/'history-corpus-sha256.json').items():
  check(sha(ROOT/name)==h,'history snapshot')
  historical.update(hashlib.sha256(r['prompt'].encode()).hexdigest() for r in rows(ROOT/name))
 check(not historical & {r['content_sha256'] for r in refs},'all-history disjoint')
 for r in refs: check(r['split']!='final' and r['input_token_range'][0]<=r['input_tokens']<=r['input_token_range'][1] and r['input_tokens']+r['max_tokens']+1<=2048,'plan domain')
 warm_sources=rows(run/'warm-history-sources.jsonl'); initial=defaultdict(list)
 for s in warm_sources:
  p=ROOT/s['source_path']; check(sha(p)==s['sha256'],'warm source bytes'); actual=rows(p)[0]
  check(actual['terminal']=='completed' and actual['output_tokens']==s['actual'] and s['error']==max(0,s['actual']-s['prediction']),'actual calibration feedback')
  initial[(s['category'],s['task_kind'])].append(s['error'])
 check(len(warm_sources)==len({s['req_id'] for s in warm_sources})==192 and len(initial)==6 and all(len(v)==32 for v in initial.values()),'six real32 warm buckets')
 results=[]; outputs=[]; cal=[]
 for i in range(1,145):
  actual=rows(run/'calibration/cases'/f'q{i:03d}'/'replay/client.jsonl')[0]; ref=refmap[actual['source_req_id']]; old.check_row(ref,actual); quality=old.quality(ref,actual); cal.append(quality['completed_and_correct'])
 check(cap.verify_cap_events(run/'calibration/server.log',0)['events']==292,'calibration CAP')
 for item in summary['rounds']:
  directory=run/'rounds'/item['name']; plan=rows(directory/'plan.jsonl'); actual=rows(directory/'replay/client.jsonl'); check(len(actual)==len(plan)==230,'round count'); byid={r['req_id']:r for r in actual}; planned={r['req_id']:r for r in plan}
  accounting=cap.verify_cap_events(directory/'server.log',0); check(accounting['events']==item['accounting']['events'],'round CAP'); expected={r['req_id'] for r in plan}|{'warm0','warm1'}; check({e['clientid'] for e in cap.cap_events(directory/'server.log')}<=expected,'native IDs')
  history={k:list(v) for k,v in initial.items()}; active={}; released=set(); seen_feedback=set(); reasons=Counter(); bgwait=[]; decision_counts=Counter(); updates=Counter(); decision_cost=[]; events=rows(directory/'replay/control/events.jsonl')
  for e in events:
   event=e['event']; req=e.get('req_id')
   if event=='native_release_seen': released.add(req)
   elif event=='decision':
    row=planned[req]; key=(row['category'],row['task_kind']); h=history[key]; pos=(len(h[-32:])-1)*.8; ordered=sorted(h[-32:]); lo=int(pos); m=1 if len(h)<32 else max(0,math.ceil(ordered[lo]+(ordered[min(lo+1,len(ordered)-1)]-ordered[lo])*(pos-lo)))
    check(e['history_count']==len(h) and set(e['active'])==set(active),'live history and active state')
    used=sum(v['cost'] for v in active.values()); check(abs(used-e['used_cost'])<1e-7,'outstanding cost')
    hard=len(active)<2 and row['input_tokens']+row['max_tokens']+1<=2048 and sum(v['reservation'] for v in active.values())+e['reservation']<=4096
    aged=row['category']=='background' and e['decision_clock_ms']-row['arrival_offset_ms']>=5000
    budget=None
    for policy,margin in [('B3_0',0),('B3_1',1),('M',m)]:
     shadow=e['shadow'][policy]; length=min(row['max_tokens'],max(row['prediction']+margin,row['grammar_ceiling'] or 0)); cost=row['input_tokens']*row['input_cost_coefficient']+length*row['output_cost_coefficient']
     check(shadow['margin']==margin and shadow['length']==length and abs(shadow['cost']-cost)<1e-7,'shadow effective cost')
     budget=next(x['budget'] for x in events if x['event']=='initial'); reason='hard_wait' if not hard else 'empty' if not active else 'aged' if aged else 'normal' if used+cost<=budget else 'soft_wait'
     check(shadow['reason']==reason and shadow['admit']==(reason not in ('soft_wait','hard_wait')),'conditional decision')
    decision_counts['total']+=1; decision_counts['M_vs_B3_0']+=e['shadow']['M']['admit']!=e['shadow']['B3_0']['admit']; decision_counts['M_vs_B3_1']+=e['shadow']['M']['admit']!=e['shadow']['B3_1']['admit']; decision_cost.append(e['decision_us'])
   elif event=='permit':
    check(req not in active,'single reservation'); active[req]={'cost':e['cost'],'reservation':e['reservation']}; check(len(active)<=2 and sum(v['reservation'] for v in active.values())<=4096,'external hard ledger')
    if e['category']=='background': reasons[e['reason']]+=1; bgwait.append(e['observed_ms']-planned[req]['arrival_offset_ms'])
   elif event=='return_reservation': check(req in active and req in released,'release before external return'); active.pop(req)
   elif event=='feedback':
    row=byid[req]; check(req in released and req not in seen_feedback and e['observed_ms']>=row['end_offset_ms'] and row['terminal']=='completed','past native feedback')
    check(e['actual']==row['output_tokens'] and e['error']==max(0,row['output_tokens']-planned[req]['prediction']),'feedback value'); key=(e['category'],e['task_kind']); history[key].append(e['error']); seen_feedback.add(req); updates[':'.join(key)]+=1
   elif event=='enqueue': check(e['arrival_lag_ms']<=50,'arrival lag')
   elif event=='final': check(not e['active'] and not e['pending'] and not e['fatal'],'clean control exit')
  check(not active and len(seen_feedback)==sum(r['terminal']=='completed' for r in actual),'full ledger and feedback closure')
  correct=0
  for r in actual:
   ref=refmap[r['source_req_id']]; old.check_row(ref,r); q=old.quality(ref,r); correct+=q['completed_and_correct']; check(r['send_lag_ms'] is None or r['send_lag_ms']<=50,'release-to-send lag')
   outputs.append({'round':item['name'],'req_id':r['req_id'],'source_req_id':r['source_req_id'],'category':r['category'],'terminal':r['terminal'],'output_tokens':r['output_tokens'],'total_first_ms':r['total_first_ms'],'completion_ms':r['completion_from_planned_ms'],'content':r['content'],'quality':q})
  end=max(100,max(r['end_offset_ms'] for r in actual)/1000); known=all(r['terminal']=='completed' and r['output_tokens_known'] for r in actual)
  result={**item,'interactive_p95_ms':percentile([r['total_first_ms'] for r in actual if r['category']=='interactive' and r['total_first_ms'] is not None],.95),'background_p95_ms':percentile([r['completion_from_planned_ms'] for r in actual if r['category']=='background' and r['terminal']=='completed'],.95),'full_completion':all(r['terminal']=='completed' for r in actual),'output_tps':sum(r['output_tokens'] for r in actual)/end if known else None,'correct':correct,'background_permit_reasons':dict(reasons),'background_normal_fraction':reasons['normal']/30,'background_max_hold_ms':max(bgwait,default=0),'native_feedback_updates':dict(updates),'observed_shadow_differences':dict(decision_counts),'decision_us_p95':percentile(decision_cost,.95)}; results.append(result)
 paired=[]
 for seed in (32101,32102):
  group={r['policy']:r for r in results if r['seed']==seed and r['phase']=='paired_development'}
  if not group: continue
  for policy in ('M','B3_1'):
   a,b=group[policy],group['B3_0']; paired.append({'seed':seed,'policy_vs_B3_0':policy,'interactive_p95_difference_ms':a['interactive_p95_ms']-b['interactive_p95_ms'],'background_p95_change_fraction':a['background_p95_ms']/b['background_p95_ms']-1,'output_rate_change_fraction':a['output_tps']/b['output_tps']-1 if a['output_tps'] is not None and b['output_tps'] is not None else None})
 result={'status':'passed_independent_prototype_audit','checks':checks,'run_id':run.name,'measured_requests':summary['measured_requests'],'warmups':summary['warmups'],'study_calibration':144,'new_calibration_in_attempt':0,'reused_calibration':144,'new_measured_in_attempt':summary['new_measured_in_this_attempt'],'prior_calibration':48,'initial_per_bucket':32,'calibration_correct':sum(cal),'selected_multiplier':summary['selected_multiplier'],'rounds':results,'paired_development':paired,'G1_passed':False,'final_test_accessed':False,'limits':['Client-side shared admission prototype, not formal C++ M/B3 or complete native KV ledger.','Two paired development seeds only; no population benefit/equivalence claim.','Conditional shadows share the actual state, not counterfactual completion timelines.','Completed-only latency quantiles are descriptive if any terminal failed; no joint success claim in that case.']}
 out.mkdir(parents=True,exist_ok=False)
 for name,value in [('summary.json',result),('corpus.json',refs),('request-results.json',outputs)]: (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 (out/'analysis-source.py').write_bytes(Path(__file__).read_bytes()); (out/'report-sha256.json').write_text(json.dumps({p.name:sha(p) for p in out.iterdir()},indent=2)+'\n',encoding='utf-8'); return {k:v for k,v in result.items() if k not in ('rounds','paired_development','limits')}
if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--run-dir',type=Path,required=True); p.add_argument('--out-dir',type=Path,required=True); a=p.parse_args(); print(json.dumps(analyze(a.run_dir,a.out_dir),ensure_ascii=False))
