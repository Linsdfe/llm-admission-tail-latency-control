"""Retrospective fixed-feedback budget diagnostics, never native performance simulation."""
import argparse,hashlib,heapq,json,math,statistics
from collections import defaultdict,Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def read(p): return json.loads(p.read_text(encoding='utf-8'))
def rows(p): return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def quantile(values,p):
 x=sorted(values); h=(len(x)-1)*p; a=int(h); return x[a]+(x[min(a+1,len(x)-1)]-x[a])*(h-a)
def margin(history,window,q,fallback):
 return fallback if len(history)<32 else max(0,math.ceil(quantile(history[-window:],q)))
def length(point,reserve,cap,floor=None): return min(cap,max(point+reserve,floor if floor is not None else 0))
def admit(cost,budget,remaining,active=1,protected=False,hard=True):
 return hard and (active==0 or protected or remaining+cost<=budget)
def witness(a,b,budget):
 lo,hi=sorted((a,b))
 if hi-lo<1e-10 or lo>=budget: return None
 headroom=(lo+min(hi,budget))/2; remaining=budget-headroom
 assert remaining>0 and admit(a,budget,remaining)!=admit(b,budget,remaining)
 return {'budget_ms_proxy':budget,'remaining_ms_proxy':remaining,'cost_M':a,'cost_B3':b,'M_admit':admit(a,budget,remaining),'B3_admit':admit(b,budget,remaining),'native_observation':False}
def expand(corpus):
 result=[]
 for category,count in [('interactive',200),('background',30)]:
  pools={b:sorted([r for r in corpus if r['category']==category and r['input_token_bucket']==b],key=lambda r:r['req_id']) for b in range(3)}
  assert all(len(v)==4 for v in pools.values())
  for i in range(count):
   source=pools[i%3][(i//3)%4]; arrival=i*500 if category=='interactive' else (i//3)*10000+250+(i%3)*50
   result.append({**source,'occurrence':i,'arrival_ms':arrival})
 return sorted(result,key=lambda r:(r['arrival_ms'],r['req_id']))
def analyze(out):
 run=ROOT/'results/raw/development/20261026-0930-p1-format-calibration-v9-r2'; refs=rows(run/'evaluation-plan.jsonl'); model=read(run/'calibration-model.json')
 v10=read(ROOT/'docs/experiments/20261026-1035-p1-grammar-upper-v10-r2/corpus.json'); ceiling={}
 for r in v10:
  if r['task_kind']=='check':
   key=tuple(r['target_ids']); assert key not in ceiling or ceiling[key]==r['grammar_ceiling']; ceiling[key]=r['grammar_ceiling']
 evidence={p.relative_to(ROOT).as_posix():sha(p) for p in [run/'evaluation-plan.jsonl',run/'calibration-model.json',ROOT/'docs/experiments/20261026-1035-p1-grammar-upper-v10-r2/corpus.json',Path(__file__),ROOT/'docs/p1/margin-mechanism-audit-protocol-v1.md']}
 for index,r in enumerate(refs,1):
  p=run/'cases'/f'q{index:03d}'/'replay/client.jsonl'; actual=rows(p)[0]; evidence[p.relative_to(ROOT).as_posix()]=sha(p)
  r.update(actual_tokens=actual['output_tokens'],solo_completion_ms=actual['completion_from_planned_ms'],native=actual['timings'])
  r['point']=min(r['max_tokens'],max(1,r['format_tokens']+model['values'][r['task_kind']]))
  r['floor']=ceiling[tuple(r['target_ids'])] if r['split']=='development' and r['task_kind']=='check' else None
 cal=refs[:48]; dev=refs[48:]; assert {r['split'] for r in cal}=={'calibration'} and {r['split'] for r in dev}=={'development'}
 input_k=statistics.median(r['native']['prompt_ms']/r['native']['prompt_n'] for r in cal); output_k=statistics.median(r['native']['predicted_ms']/r['actual_tokens'] for r in cal)
 weighted=sorted((r['input_tokens']*input_k+r['point']*output_k,(200 if r['category']=='interactive' else 30)/24) for r in cal)
 total=sum(w for _,w in weighted); cumulative=0
 for reference,w in weighted:
  cumulative+=w
  if cumulative>=.75*total: break
 groups=[]; decisions=[]; witnesses=[]
 for seed in (29201,29202):
  trace=expand([r for r in dev if r['seed']==seed]); assert len(trace)==230
  for window in (32,128):
   for q in (.8,.95):
    history=defaultdict(list)
    for r in cal: history[(r['category'],r['task_kind'])].append(max(0,r['actual_tokens']-r['point']))
    pending=[]; per_margin=defaultdict(list)
    for seq,r in enumerate(trace):
     now=r['arrival_ms']
     while pending and pending[0][0]<now:
      finish,order,bucket,error=heapq.heappop(pending); history[bucket].append(error)
     key=(r['category'],r['task_kind']); n=len(history[key]); m=margin(history[key],window,q,model['margin']); effective=length(r['point'],m,r['max_tokens'],r['floor'])
     record={'seed':seed,'window':window,'quantile':q,'sequence':seq,'source_req_id':r['req_id'],'category':r['category'],'task_kind':r['task_kind'],'arrival_ms':now,'history_count':n,'fallback':n<32,'M_margin':m,'point':r['point'],'floor':r['floor'],'M_length':effective,'B3_zero_length':length(r['point'],0,r['max_tokens'],r['floor'])}; decisions.append(record)
     for b in (0,1,2,4,8,16):
      b_len=length(r['point'],b,r['max_tokens'],r['floor']); per_margin[b].append((effective-b_len,record))
      ca=r['input_tokens']*input_k+effective*output_k; cb=r['input_tokens']*input_k+b_len*output_k
      for multiplier in (.5,.75,1):
       example=witness(ca,cb,reference*multiplier)
       if example is not None and not any(x['seed']==seed and x['window']==window and x['quantile']==q and x['B3_margin']==b and x['budget_multiplier']==multiplier for x in witnesses): witnesses.append({**example,'seed':seed,'window':window,'quantile':q,'B3_margin':b,'budget_multiplier':multiplier,'source_req_id':r['req_id'],'state':'constructed ordinary nonempty queue, one free slot, hard capacity permits, no protection'})
     heapq.heappush(pending,(now+r['solo_completion_ms'],seq,key,max(0,r['actual_tokens']-r['point'])))
    for b,values in per_margin.items():
     diffs=[v for v,_ in values]; groups.append({'seed':seed,'window':window,'quantile':q,'B3_margin':b,'diagnostic_occurrences':230,'same_length':sum(abs(v)<1e-9 for v in diffs),'different_length':sum(abs(v)>=1e-9 for v in diffs),'M_minus_B3_token_min':min(diffs),'M_minus_B3_token_max':max(diffs)})
 base_covered=sum(r['actual_tokens']<=length(r['point'],0,r['max_tokens'],r['floor']) for r in dev)
 result={'status':'completed_retrospective_mechanism_audit','independent_new_measurements':0,'source_dev_tasks':48,'source_seeds':2,'fixed_feedback_occurrences_per_M_config':460,'M_configs':4,'diagnostic_decisions':len(decisions),'shared_cost_proxy':{'input_ms_per_token':input_k,'output_ms_per_token':output_k,'weighted_calibration_p75_reference':reference},'source_dev_actual_within_B3_zero_shared_floor':base_covered,'source_dev_denominator':48,'M_margin_distribution':dict(Counter(r['M_margin'] for r in decisions)),'M_fallback_decisions':sum(r['fallback'] for r in decisions),'positive_M_margin_above_B3_zero':sum(r['M_length']>r['B3_zero_length'] for r in decisions),'numeric_source_positive_residuals':sum(r['actual_tokens']>r['point'] for r in dev if r['task_kind']!='check'),'groups':groups,'constructed_witnesses':witnesses,'G1_passed':False,'final_test_accessed':False,'limits':['Recorded solo outputs and external completion clocks are reused, not counterfactual native outcomes.','Budget differences are not observed admission differences; constructed states not proven reachable.','No P95, throughput, M-value or population equivalence inference.','Six history buckets and coefficient definitions clarify a proposal, not existing production implementation.']}
 out.mkdir(parents=True,exist_ok=False)
 for name,value in [('summary.json',result),('diagnostic-decisions.json',decisions),('evidence-sha256.json',evidence)]: (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 (out/'analysis-source.py').write_bytes(Path(__file__).read_bytes())
 (out/'report-sha256.json').write_text(json.dumps({p.name:sha(p) for p in out.iterdir() if p.is_file()},indent=2)+'\n',encoding='utf-8')
 return {k:v for k,v in result.items() if k not in ('groups','constructed_witnesses','limits')}
if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--out-dir',type=Path,required=True); a=p.parse_args(); print(json.dumps(analyze(a.out_dir),ensure_ascii=False))
