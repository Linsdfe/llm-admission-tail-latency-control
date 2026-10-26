"""Target-weighted cutoff, fixed-corpus expansion and model-free order witnesses."""
from collections import Counter
from fractions import Fraction
import hashlib
import json
import statistics
from pathlib import Path
import p1_profile_estimator_v7 as estimator

TARGET={'interactive':[67,67,66],'background':[10,10,10]}

def weighted_quantile(values,weights,p=Fraction(85,100)):
    if not values or len(values)!=len(weights) or any(w<0 for w in weights) or sum(weights)<=0: raise ValueError('Invalid weighted data')
    boundary=p*sum(weights); cumulative=Fraction(0)
    for value,weight in sorted(zip(values,weights)):
        cumulative+=weight
        if cumulative>=boundary: return value
    raise AssertionError('Unreachable weighted cutoff')

def percentile(values,p):
    ordered=sorted(values); position=(len(ordered)-1)*p; lo=int(position)
    return ordered[lo]+(ordered[min(lo+1,len(ordered)-1)]-ordered[lo])*(position-lo)

def reference_artifact(root):
    run=root/'results/raw/development/20261025-2240-p1-system-workload-v7'
    receipt=json.loads((root/'results/raw/maintenance/20261025-p1-system-workload-delivery-v7/verification-index.json').read_text(encoding='utf-8'))
    sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    cal=[]; sources={}
    cal_paths=sorted(p for p in (run/'profile/cases').glob('q*/replay/client.jsonl') if 'v7-cal-27001-' in p.parent.parent.name)
    assert len(cal_paths)==24
    for path in cal_paths:
        one=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        assert len(one)==1
        assert one[0]['split']=='calibration'
        relative=path.relative_to(run).as_posix(); assert sha(path)==receipt['raw_files'][relative]
        sources[path.relative_to(root).as_posix()]=sha(path); cal+=one
    assert len(cal)==24 and Counter((r['category'],r['input_token_bucket']) for r in cal)=={(c,b):4 for c in TARGET for b in range(3)}
    model_path=run/'calibration-model.json'; assert sha(model_path)==receipt['raw_files']['calibration-model.json']
    sources[model_path.relative_to(root).as_posix()]=sha(model_path)
    weights=[Fraction(TARGET[r['category']][r['input_token_bucket']],4) for r in cal]
    return {'status':'frozen_calibration_only','point_model':json.loads(model_path.read_text(encoding='utf-8')),
      'weighted_cutoff':weighted_quantile([r['output_tokens'] for r in cal],weights),
      'ordinary_cutoff':percentile([r['output_tokens'] for r in cal],.85),'target_bucket_counts':TARGET,
      'weight_sum':str(sum(weights)),'calibration_requests':24,'source_sha256':sources,
      'selection':'Existing target200/30 weighting applied to v7 calibration only; no v8 fitting'}

def classify(row,artifact):
    result=estimator.apply(row,artifact['point_model'])
    return dict(result,predicted_class='short' if result['prediction']<=artifact['weighted_cutoff'] else 'long')

def expand_trace(corpus):
    assert len(corpus)==24
    out=[]
    for category,count in [('interactive',200),('background',30)]:
        pools={b:sorted((r for r in corpus if r['category']==category and r['input_token_bucket']==b),key=lambda r:r['req_id']) for b in range(3)}
        assert all(len(p)==4 for p in pools.values())
        for index in range(count):
            bucket=index%3; source=pools[bucket][(index//3)%4]
            arrival=index*500 if category=='interactive' else (index//3)*10000+250+(index%3)*50
            out.append(dict(source,req_id=source['req_id']+f'-occ{index:03d}',source_req_id=source['req_id'],arrival_offset_ms=arrival))
    out.sort(key=lambda r:(r['arrival_offset_ms'],r['req_id']))
    assert len(out)==len({r['req_id'] for r in out})==230
    assert Counter((r['category'],r['input_token_bucket']) for r in out)=={(c,b):TARGET[c][b] for c in TARGET for b in range(3)}
    assert {r['source_req_id'] for r in out}=={r['req_id'] for r in corpus}
    return out

def simple_order(tasks,now,aging=5000):
    def priority(r): return 1 if r['category']=='interactive' else 0 if now-r['enqueued_ms']>=aging else 2
    return [r['req_id'] for r in sorted(tasks,key=lambda r:(priority(r),r['enqueued_ms'],r['task_id']))]

def adaptation_order(tasks,now,state,aging=5000,gamma=.8):
    short=sorted((r for r in tasks if r['category']=='interactive'),key=lambda r:(r['enqueued_ms'],r['req_id']))
    long=sorted((r for r in tasks if r['category']=='background'),key=lambda r:(r['enqueued_ms'],r['req_id']))
    history=state['history'][-128:]; theta=percentile(history,gamma) if len(history)>=32 else aging
    eligible=[r for r in long if now-r['enqueued_ms']>theta]
    if state['debt'] and eligible:
        first=eligible[0]; ordered=[first]+short+[r for r in long if r['req_id']!=first['req_id']]
    else: ordered=short+long
    return {'order':[r['req_id'] for r in ordered],'theta':theta}

def ordering_witnesses():
    fixtures=[('fresh_no_debt',1000,800,{'debt':False,'c':0,'history':[0]}),
      ('aged_no_debt',6001,0,{'debt':False,'c':0,'history':[0]}),
      ('aged_with_debt',6001,0,{'debt':True,'c':5,'history':[0]*5}),
      ('history_threshold_with_debt',2000,1000,{'debt':True,'c':5,'history':[100]*32})]
    result=[]
    for name,now,b_arr,state in fixtures:
        tasks=[{'req_id':'B','task_id':1,'category':'background','enqueued_ms':b_arr},
          {'req_id':'I','task_id':2,'category':'interactive','enqueued_ms':now-50}]
        simple=simple_order(tasks,now); adapted=adaptation_order(tasks,now,state)
        result.append({'name':name,'now_ms':now,'tasks':tasks,'state':state,'simple_order':simple,'adaptation_order':adapted['order'],'theta':adapted['theta'],'different':simple!=adapted['order']})
    assert [r['different'] for r in result]==[False,True,False,True]
    return result
