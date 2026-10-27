"""Fresh, bounded copying workload; answers stay outside the public plan."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import re

ROOT=Path(__file__).resolve().parents[2]
def config():return json.loads((ROOT/'configs/p1-copy-profile-v17.json').read_text(encoding='utf-8'))
def digest(s):return hashlib.sha256(s.encode('utf-8')).hexdigest()

def descriptors():
    cfg=config();result=[]
    for seed in cfg['allowed_seeds']:
        for category in ('interactive','background'):
            for kind in cfg['task_kinds']:
                for i,n in enumerate(cfg['target_counts'][category]):
                    result.append(dict(req_id=f'v17-dev-{seed}-{category[0]}-{kind}-{i}',seed=seed,
                        split='development',category=category,task_kind=kind,target_count=n,
                        index=i))
    return result

def render(d,extra):
    cfg=config()
    if d not in descriptors() or not 0<=extra<=cfg['extra_records_max']:raise ValueError('unregistered descriptor')
    rng=random.Random(d['req_id']);offset=100 if d['seed']==36101 else 1000
    records=[(f'R{offset+i}',str(rng.randint(10,99))) for i in range(d['target_count']+cfg['raw_extra_records'][d['index']])] 
    targets=[p[0] for p in records[:d['target_count']]];rng.shuffle(records)
    if d['task_kind']=='organize':rng.shuffle(targets)
    instruction=('提取以下编号对应的数值：' if d['task_kind']=='extract' else '按以下编号顺序整理对应的数值：')+','.join(targets)+'。每行ID|值，不写其他内容。'
    raw_prompt='<|im_start|>system\n只输出结果，无表头。<|im_end|>\n<|im_start|>user\n资料\n'+'\n'.join(a+'|'+b for a,b in records)+'\n'+instruction+'<|im_end|>\n<|im_start|>assistant\n'
    prompt=preprocess(raw_prompt)
    truth=dict(records);answer='\n'.join(a+'|'+truth[a] for a in targets)
    return dict(d,prompt=prompt,raw_prompt_evaluation_only=raw_prompt,raw_prompt_sha256=digest(raw_prompt),expected_answer=answer,source_records_evaluation_only=records,
                target_ids=targets,extra_records=extra,content_sha256=digest(prompt),
                input_token_range=cfg['input_ranges'][d['category']],
                max_tokens=cfg['max_tokens'][d['category']],temperature=0,ignore_eos=False,
                cache_prompt=False,arrival_offset_ms=0)

def public_targets(prompt):
    match=re.search(r'编号(?:顺序整理对应的数值|对应的数值)：(R[0-9]+(?:,R[0-9]+)*)。每行ID\|值，不写其他内容。',prompt)
    if not match:raise ValueError('missing public target instruction')
    return match[1].split(',')

def preprocess(raw):
    targets=set(public_targets(raw))
    records=re.findall(r'(?m)^(R[0-9]+)\|([1-9][0-9])$',raw)
    if not targets or not targets.issubset(dict(records)):raise ValueError('raw target missing')
    kept='\n'.join(a+'|'+b for a,b in records if a in targets)
    start=raw.index('资料\n')+len('资料\n');last=raw.index('\n提取以下编号') if '\n提取以下编号' in raw else raw.index('\n按以下编号')
    return raw[:start]+kept+raw[last:]

def public_rule(prompt):
    source=dict(re.findall(r'(?m)^(R[0-9]+)\|([1-9][0-9])$',prompt))
    targets=public_targets(prompt)
    if not source or len(targets)!=len(set(targets)) or any(t not in source for t in targets):raise ValueError('invalid public source')
    return '\n'.join(t+'|'+source[t] for t in targets)

def grammar(prompt):
    parts=[]
    for i,t in enumerate(public_targets(prompt)):
        if i:parts.append(json.dumps('\n'))
        parts.extend([json.dumps(t+'|'),'[1-9]','[0-9]'])
    return 'root ::= '+' '.join(parts)+'\n'

def materialize(d,tokenize):
    lo,hi=config()['input_ranges'][d['category']]
    # First feasible public input size; outputs have not been generated.
    left,right=0,config()['extra_records_max'];cache={}
    def get(n):
        if n not in cache:
            r=render(d,n);tokens=tokenize(r['prompt']);cache[n]=(r,tokens)
        return cache[n]
    while left<right:
        mid=(left+right)//2
        if len(get(mid)[1])<lo:left=mid+1
        else:right=mid
    r,tokens=get(left)
    if not lo<=len(tokens)<=hi:
        r,tokens=next((get(n) for n in range(config()['extra_records_max']+1) if lo<=len(get(n)[1])<=hi),(None,None))
        if r is None:raise ValueError('input bucket infeasible')
    r.update(input_tokens=len(tokens),token_ids_sha256=digest(json.dumps(tokens,separators=(',',':'))),grammar=grammar(r['prompt']))
    return r

def public_row(r):
    return {k:v for k,v in r.items() if k not in ('expected_answer','source_records_evaluation_only','target_ids','raw_prompt_evaluation_only')}

def validate(plan,history):
    if len(plan)!=64:raise ValueError('exactly64 tasks')
    seen=set()
    for r in plan:
        h=digest(r['prompt']);lo,hi=r['input_token_range']
        if h in history or h in seen or h!=r['content_sha256']:raise ValueError('duplicate prompt')
        if not lo<=r['input_tokens']<=hi or r['input_tokens']+r['max_tokens']+1>2048:raise ValueError('context or bucket')
        if r['grammar']!=grammar(r['prompt']) or {'expected_answer','source_records_evaluation_only'}&r.keys():raise ValueError('grammar or gold leakage')
        seen.add(h)
    if Counter((r['seed'],r['category'],r['task_kind']) for r in plan)!=Counter({(s,c,k):8 for s in (36101,36102) for c in ('interactive','background') for k in ('extract','organize')}):raise ValueError('cell balance')
    return dict(status='passed',unique_prompts=64,purpose='serial_task_profile',online_updates_enabled=False,final_test_accessed=False)

def quality(ref,actual):
    return dict(whole_correct=actual['terminal']=='completed' and actual['finish_reason']=='stop' and actual['content']==ref['expected_answer'],
                expected_answer=ref['expected_answer'],content=actual['content'],output_limit=actual['finish_reason']=='length')
