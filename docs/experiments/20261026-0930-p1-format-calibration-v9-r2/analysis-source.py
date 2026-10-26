"""Independent fact/tokenization/prediction audit; no v9 generator/estimator imports."""
import argparse,hashlib,importlib.util,json,math,statistics
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('old99',ROOT/'scripts/analysis/99_analyze_p1_system_workload_v7.py'); old=importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
read,rows,sha=old.read,old.rows,old.sha
def analyze(run,out):
 run=run.resolve(strict=True); checks=0
 def check(x,msg):
  nonlocal checks
  checks+=1
  if not x: raise AssertionError(msg)
 raw=read(run/'summary.json'); check(raw['status']=='completed' and raw['measured']==96 and raw['warmups']==2 and raw['cap_events']==196,'counts')
 for p,h in read(run/'source-sha256.json').items(): check(sha(ROOT/p)==sha(run/'source'/p)==h,'frozen '+p)
 refs=rows(run/'evaluation-plan.jsonl'); check(len(refs)==len({r['content_sha256'] for r in refs})==96,'unique corpus')
 features=rows(run/'format-tokenization.jsonl'); by={f['req_id']:f for f in features}; records=[]
 for index,ref in enumerate(refs,1):
  row=rows(run/'cases'/f'q{index:03d}'/'replay/client.jsonl')[0]; old.check_row(ref,row); q=old.quality(ref,row)
  check(row['terminal']=='completed' and row['output_tokens']==row['native_final']['tokens_predicted'],'native')
  public=old.old.facts.derive_expected(ref) # oracle used only to score, never predict
  feature=by[ref['req_id']]; payload=feature['request']
  placeholder='\n'.join(i+'|'+('一致' if ref['task_kind']=='check' else '50') for i in ref['target_ids'])
  check(payload=={'content':placeholder,'add_special':False,'parse_special':False},'public placeholder')
  check(len(json.loads(feature['response_body'])['tokens'])==ref['format_tokens']==feature['format_tokens'],'feature token count')
  check(row['send_lag_ms']<=50 and row['cached_prompt_tokens']==0,'lag/cache')
  records.append({'req_id':ref['req_id'],'split':ref['split'],'seed':ref['seed'],'category':ref['category'],'task_kind':ref['task_kind'],'max_tokens':ref['max_tokens'],'format_tokens':ref['format_tokens'],'output_tokens':row['output_tokens'],'content':row['content'],'quality':q})
 cal=records[:48]; dev=records[48:]; check({r['split'] for r in cal}=={'calibration'} and {r['split'] for r in dev}=={'development'},'split')
 check({r['template_family'] for r in refs[:48]}.isdisjoint({r['template_family'] for r in refs[48:]}),'family separation')
 def fit(group,c):
  g=defaultdict(list)
  for r in group: g[r['category']+':'+r['task_kind'] if c=='category_task_median' else r['task_kind']].append(r['output_tokens']-(0 if c=='category_task_median' else r['format_tokens']))
  return {k:statistics.median(v) for k,v in g.items()}
 def point(r,c,v): return min(r['max_tokens'],max(1,v[r['category']+':'+r['task_kind'] if c=='category_task_median' else r['task_kind']]+(0 if c=='category_task_median' else r['format_tokens'])))
 candidates=['category_task_median','format_residual']; errors={}; scores={}
 for c in candidates:
  errors[c]=[]
  for seed in (29101,29102):
   v=fit([r for r in cal if r['seed']!=seed],c)
   errors[c]+=[r['output_tokens']-point(r,c,v) for r in cal if r['seed']==seed]
  scores[c]=statistics.mean(abs(e) for e in errors[c])
 selected=min(candidates,key=lambda c:(scores[c],candidates.index(c))); values=fit(cal,selected); margin=max(0,math.ceil(max(errors[selected])))
 model=read(run/'calibration-model.json'); check(model=={'candidate':selected,'values':values,'cross_validation_mae':scores,'margin':margin,'calibration_requests':48},'independent calibration fit')
 predicted={r['req_id']:r for r in rows(run/'development-predictions-before-data.jsonl')}
 for r in dev:
  p=point(r,selected,values); upper=min(r['max_tokens'],math.ceil(p+margin)); check(predicted[r['req_id']]=={'req_id':r['req_id'],'prediction':p,'upper':upper},'frozen dev prediction'); r.update(prediction=p,upper=upper)
 summary={'status':'passed','run_id':run.name,'checks':checks,'counts':{'calibration':48,'development':48,'warmups':2},'model':model,'development':{'mae':statistics.mean(abs(r['output_tokens']-r['prediction']) for r in dev),'upper_coverage':sum(r['output_tokens']<=r['upper'] for r in dev),'point_at_cap':sum(r['prediction']>=r['max_tokens'] for r in dev),'upper_at_cap':sum(r['upper']>=r['max_tokens'] for r in dev),'correct':sum(r['quality']['completed_and_correct'] for r in dev)},'calibration_correct':sum(r['quality']['completed_and_correct'] for r in cal),'feature_tokenization_cost_ms':{'sum':sum(f['elapsed_ms'] for f in features),'median':statistics.median(f['elapsed_ms'] for f in features),'maximum':max(f['elapsed_ms'] for f in features)},'elapsed_seconds':raw['elapsed_seconds'],'G1_passed':False,'final_test_accessed':False,'limits':['48 dev source tasks, two seeds; empirical coverage only.','O and C instruction families were previously explored, but separated across new splits.','ID format, instruction and seed changes jointly; not isolated causal ID-effect.','No scheduling-performance or M/B3-value inference.']}
 out.mkdir(parents=True,exist_ok=False)
 for name,value in [('summary.json',summary),('corpus.json',refs),('request-results.json',records)]:
  (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 (out/'analysis-source.py').write_bytes(Path(__file__).read_bytes())
 (out/'report-sha256.json').write_text(json.dumps({p.name:sha(p) for p in out.iterdir() if p.is_file()},indent=2)+'\n',encoding='utf-8')
 return summary
if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--run-dir',type=Path,required=True); p.add_argument('--out-dir',type=Path,required=True); a=p.parse_args(); print(json.dumps(analyze(a.run_dir,a.out_dir),ensure_ascii=False))
