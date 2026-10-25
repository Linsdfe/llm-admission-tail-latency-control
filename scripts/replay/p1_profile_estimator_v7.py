"""Two finite output-length candidates; fitting is calibration-only."""
from collections import defaultdict
import math
import statistics

CANDIDATES=('category_median','category_task_median')

def key(row,candidate):
    if candidate not in CANDIDATES: raise ValueError('Unknown candidate')
    return row['category'] if candidate==CANDIDATES[0] else row['category']+':'+row['task_kind']

def fit(rows,candidate):
    if not rows or any(r['split']!='calibration' for r in rows): raise ValueError('Fit only calibration')
    groups=defaultdict(list)
    for row in rows: groups[key(row,candidate)].append(row['output_tokens'])
    return {k:statistics.median(v) for k,v in groups.items()}

def predict(row,candidate,values):
    return min(row['max_tokens'],max(1,values[key(row,candidate)]))

def select(rows):
    if len(rows)!=24 or {r['split'] for r in rows}!={'calibration'}: raise ValueError('Exactly24 calibration required')
    scores={}; residuals={}
    for candidate in CANDIDATES:
        errors=[]
        for fold in range(4):
            train=[r for r in rows if r['kind_instance']!=fold]
            test=[r for r in rows if r['kind_instance']==fold]
            assert len(train)==18 and len(test)==6
            values=fit(train,candidate)
            errors.extend(r['output_tokens']-predict(r,candidate,values) for r in test)
        scores[candidate]=sum(abs(x) for x in errors)/24; residuals[candidate]=errors
    chosen=min(CANDIDATES,key=lambda c:(scores[c],CANDIDATES.index(c)))
    return {'candidate':chosen,'values':fit(rows,chosen),'cross_validation_mae':scores,
      'margin':max(0,math.ceil(max(residuals[chosen]))),'calibration_requests':24,
      'selection':'4fold kind_instance held out; tie prefers category median; no development fitting'}

def apply(row,model):
    point=predict(row,model['candidate'],model['values'])
    upper=min(row['max_tokens'],math.ceil(point+model['margin']))
    return {'prediction':point,'upper':upper}
