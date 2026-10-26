"""Independent replay arithmetic and witness checks without importing diagnostic code."""
import argparse,hashlib,json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def read(p): return json.loads(p.read_text(encoding='utf-8'))
def audit():
 folder=ROOT/'docs/experiments/20261026-p1-margin-mechanism-audit-v1'; summary=read(folder/'summary.json'); decisions=read(folder/'diagnostic-decisions.json'); refs=read(ROOT/'docs/experiments/20261026-0930-p1-format-calibration-v9-r2/corpus.json'); model=read(ROOT/'results/raw/development/20261026-0930-p1-format-calibration-v9-r2/calibration-model.json'); known={}; n=0
 def check(x,label):
  nonlocal n
  n+=1
  if not x: raise AssertionError(label)
 for name,digest in read(folder/'evidence-sha256.json').items(): check(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,'source bytes')
 for i,r in enumerate(refs,1):
  p=ROOT/'results/raw/development/20261026-0930-p1-format-calibration-v9-r2/cases'/f'q{i:03d}'/'replay/client.jsonl'; row=json.loads(p.read_text().splitlines()[0]); point=r['format_tokens']+model['values'][r['task_kind']]; known[r['req_id']]={**r,'error':max(0,row['output_tokens']-point),'duration':row['completion_from_planned_ms']}
 check(len(decisions)==1840,'diagnostic count')
 for seed in (29201,29202):
  for w in (32,128):
   for q in (.8,.95):
    group=[r for r in decisions if r['seed']==seed and r['window']==w and r['quantile']==q]; check(len(group)==230 and [r['sequence'] for r in group]==list(range(230)),'trace size')
    past=[]
    for row in group:
     src=known[row['source_req_id']]; key=(src['category'],src['task_kind']); cal=[r['error'] for r in known.values() if r['split']=='calibration' and (r['category'],r['task_kind'])==key]
     feedback=sorted((p for p in past if p[0]<row['arrival_ms'] and p[2]==key),key=lambda x:(x[0],x[1])); hist=cal+[p[3] for p in feedback]
     check(len(hist)==row['history_count'],'strictly past feedback count')
     if len(hist)<32: expected=model['margin']
     else:
      x=sorted(hist[-w:]); pos=(len(x)-1)*q; lo=int(pos); expected=max(0,math.ceil(x[lo]+(x[min(lo+1,len(x)-1)]-x[lo])*(pos-lo)))
     check(expected==row['M_margin'],'independent residual quantile')
     check(row['M_length']==min(src['max_tokens'],max(row['point']+expected,row['floor'] or 0)),'effective public floor')
     past.append((row['arrival_ms']+src['duration'],row['sequence'],key,src['error']))
 for w in summary['constructed_witnesses']:
  check((w['remaining_ms_proxy']+w['cost_M']<=w['budget_ms_proxy'])==w['M_admit'],'M conditional decision')
  check((w['remaining_ms_proxy']+w['cost_B3']<=w['budget_ms_proxy'])==w['B3_admit'] and w['M_admit']!=w['B3_admit'],'B3 conditional difference')
 check(sum(r['M_length']>r['B3_zero_length'] for r in decisions)==summary['positive_M_margin_above_B3_zero']==544,'aggregate B3 zero comparison')
 return {'status':'passed','checks':n,'scope':'Independent fixed-feedback arithmetic and constructed-state checks; not a real-engine M/B3 comparison.','G1_passed':False,'final_test_accessed':False}
if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--out',type=Path,required=True); a=p.parse_args(); result=audit()
 with a.out.open('x',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2)
 print(json.dumps(result,ensure_ascii=False))
