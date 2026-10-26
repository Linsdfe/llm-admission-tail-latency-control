"""Independent public-branch upper check and frozen-point evaluation."""
import argparse,importlib.util,json,itertools,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
s=importlib.util.spec_from_file_location('old99',ROOT/'scripts/analysis/99_analyze_p1_system_workload_v7.py'); old=importlib.util.module_from_spec(s); s.loader.exec_module(old)
def analyze(run,out):
 read,rows,sha=old.read,old.rows,old.sha; count=0
 def check(x,msg):
  nonlocal count
  count+=1
  if not x: raise AssertionError(msg)
 raw=read(run/'summary.json'); check(raw['measured']==48 and raw['cap_events']==100 and raw['source_unchanged'],'raw counts')
 for p,h in read(run/'source-sha256.json').items(): check(sha(ROOT/p)==sha(run/'source'/p)==h,'source '+p)
 model=read(run/'calibration-model.json'); check(model==read(ROOT/'results/raw/development/20261026-0930-p1-format-calibration-v9-r2/calibration-model.json'),'frozen model')
 refs=rows(run/'evaluation-plan.jsonl'); predictions={r['req_id']:r for r in rows(run/'development-predictions-before-data.jsonl')}; features={r['req_id']:r for r in rows(run/'format-tokenization.jsonl')}; branches={r['req_id']:r for r in rows(run/'grammar-branches.jsonl')}; result=[]
 check(len(refs)==len({r['content_sha256'] for r in refs})==48,'unique development')
 for index,ref in enumerate(refs,1):
  actual=rows(run/'cases'/f'q{index:03d}'/'replay/client.jsonl')[0]; old.check_row(ref,actual); q=old.quality(ref,actual)
  check(ref['split']=='development' and actual['terminal']=='completed' and actual['send_lag_ms']<=50,'row terminal')
  f=features[ref['req_id']]; placeholder='\n'.join(i+'|'+('一致' if ref['task_kind']=='check' else '50') for i in ref['target_ids'])
  check(f['request']=={'content':placeholder,'add_special':False,'parse_special':False},'public feature')
  check(len(json.loads(f['response_body'])['tokens'])==ref['format_tokens'],'token feature')
  point=min(ref['max_tokens'],max(1,ref['format_tokens']+model['values'][ref['task_kind']])); upper=min(ref['max_tokens'],point+model['margin'])
  if ref['task_kind']=='check':
   b=branches[ref['req_id']]; expected={'\n'.join(i+'|'+v for i,v in zip(ref['target_ids'],values)) for values in itertools.product(('一致','不一致'),repeat=len(ref['target_ids']))}
   check({r['request']['content'] for r in b['branches']}==expected and len(b['branches'])==2**len(ref['target_ids']),'exhaustive public branches')
   check(all(not r['request']['add_special'] and not r['request']['parse_special'] for r in b['branches']),'no special tokens')
   ceiling=max(len(json.loads(r['response_body'])['tokens']) for r in b['branches'])+2; check(ceiling==ref['grammar_ceiling']==b['ceiling'],'ceiling')
   upper=min(ref['max_tokens'],max(upper,ceiling))
  check(predictions[ref['req_id']]=={'req_id':ref['req_id'],'prediction':point,'upper':upper},'predictions before output')
  result.append({'req_id':ref['req_id'],'task_kind':ref['task_kind'],'prediction':point,'upper':upper,'actual':actual['output_tokens'],'content':actual['content'],'quality':q})
 summary={'status':'passed','checks':count,'counts':{'development':48,'warmups':2},'development':{'mae':statistics.mean(abs(r['actual']-r['prediction']) for r in result),'upper_coverage':sum(r['actual']<=r['upper'] for r in result),'correct':sum(r['quality']['completed_and_correct'] for r in result)},'grammar_tokenization_requests':sum(len(b['branches']) for b in branches.values()),'elapsed_seconds':raw['elapsed_seconds'],'G1_passed':False,'final_test_accessed':False,'limits':['One prospective supplementary development check, two seeds, no unseen-template claim.','Branch enumeration is bounded to restricted check grammar, not arbitrary LLM text.','No M/B3 value or scheduling-performance conclusion.']}
 out.mkdir(parents=True,exist_ok=False)
 for name,value in [('summary.json',summary),('corpus.json',refs),('request-results.json',result)]: (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 (out/'analysis-source.py').write_bytes(Path(__file__).read_bytes()); (out/'report-sha256.json').write_text(json.dumps({p.name:sha(p) for p in out.iterdir() if p.is_file()},indent=2)+'\n',encoding='utf-8'); return summary
if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--run-dir',type=Path,required=True); p.add_argument('--out-dir',type=Path,required=True); a=p.parse_args(); print(json.dumps(analyze(a.run_dir,a.out_dir),ensure_ascii=False))
