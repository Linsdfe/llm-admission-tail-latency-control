"""Calibration-only format-aware candidates, without oracle values."""
import math
import statistics
from collections import defaultdict
CANDIDATES=('category_task_median','format_residual')
def key(row,candidate):
 return row['category']+':'+row['task_kind'] if candidate==CANDIDATES[0] else row['task_kind']
def fit(rows,candidate):
 assert rows and all(r['split']=='calibration' for r in rows)
 g=defaultdict(list)
 for r in rows: g[key(r,candidate)].append(r['output_tokens']-(r['format_tokens'] if candidate==CANDIDATES[1] else 0))
 return {k:statistics.median(v) for k,v in g.items()}
def predict(row,candidate,values):
 return min(row['max_tokens'],max(1,values[key(row,candidate)]+(row['format_tokens'] if candidate==CANDIDATES[1] else 0)))
def select(rows):
 assert len(rows)==48 and {r['split'] for r in rows}=={'calibration'}
 scores={}; residuals={}
 for c in CANDIDATES:
  errors=[]
  for seed in (29101,29102):
   values=fit([r for r in rows if r['seed']!=seed],c)
   errors += [r['output_tokens']-predict(r,c,values) for r in rows if r['seed']==seed]
  scores[c]=statistics.mean(abs(e) for e in errors); residuals[c]=errors
 chosen=min(CANDIDATES,key=lambda c:(scores[c],CANDIDATES.index(c)))
 return {'candidate':chosen,'values':fit(rows,chosen),'cross_validation_mae':scores,'margin':max(0,math.ceil(max(residuals[chosen]))),'calibration_requests':48}
def apply(row,model):
 p=predict(row,model['candidate'],model['values'])
 return {'prediction':p,'upper':min(row['max_tokens'],math.ceil(p+model['margin']))}
