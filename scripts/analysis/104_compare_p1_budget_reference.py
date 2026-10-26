"""Class-balanced reference-cost diagnostic; no native service or final data."""
import argparse,json,statistics,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def read(p): return json.loads(p.read_text(encoding='utf-8'))
def quantile(values,p):
 x=sorted(values); h=(len(x)-1)*p; a=int(h); return x[a]+(x[min(a+1,len(x)-1)]-x[a])*(h-a)
def main(out):
 audit=read(ROOT/'docs/experiments/20261026-p1-margin-mechanism-audit-v1/summary.json'); coeff=audit['shared_cost_proxy']; refs=read(ROOT/'docs/experiments/20261026-0930-p1-format-calibration-v9-r2/corpus.json'); model=read(ROOT/'results/raw/development/20261026-0930-p1-format-calibration-v9-r2/calibration-model.json'); v10=read(ROOT/'docs/experiments/20261026-1035-p1-grammar-upper-v10-r2/corpus.json')
 ceilings={tuple(r['target_ids']):r['grammar_ceiling'] for r in v10 if r['task_kind']=='check'}
 def cost(r,dev=False):
  point=r['format_tokens']+model['values'][r['task_kind']]
  if dev and r['task_kind']=='check': point=max(point,ceilings[tuple(r['target_ids'])])
  return r['input_tokens']*coeff['input_ms_per_token']+point*coeff['output_ms_per_token']
 class_q={c:quantile([cost(r) for r in refs[:48] if r['category']==c],.75) for c in ('interactive','background')}; balanced=max(class_q.values()); counts=[]
 for name,reference in [('v2_target_weighted_p75',coeff['weighted_calibration_p75_reference']),('v3_max_class_p75',balanced)]:
  for multiple in (.5,.75,1):
   budget=reference*multiple
   counts.append({'reference_rule':name,'reference_ms_proxy':reference,'multiplier':multiple,'budget_ms_proxy':budget,**{c:{'source_requests':24,'own_cost_exceeds_budget':sum(cost(r,True)>budget for r in refs[48:] if r['category']==c)} for c in ('interactive','background')}})
 result={'status':'retrospective_reference_diagnostic','new_GPU_measurements':0,'class_calibration_p75_ms_proxy':class_q,'proposed_reference':balanced,'rows':counts,'source':'v9 calibration only sets proposed reference; v9 development used to describe cost feasibility, not fit or native performance.','interpretation':'Own cost above budget means ordinary nonempty-queue soft admission cannot admit this request even with zero remaining cost; common empty/protected bypass can still serve it. This is not observed starvation.','final_test_accessed':False}
 out.mkdir(parents=True,exist_ok=False); (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); (out/'analysis-source.py').write_bytes(Path(__file__).read_bytes()); (out/'report-sha256.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir()},indent=2)+'\n',encoding='utf-8'); return result
if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--out-dir',type=Path,required=True); a=p.parse_args(); print(json.dumps(main(a.out_dir),ensure_ascii=False))
