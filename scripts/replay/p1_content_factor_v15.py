"""Paired diagnostic views of seen v14 facts; public preprocessing only."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]


def config(): return json.loads((ROOT/'configs/p1-content-factor-diagnostic-v15.json').read_text(encoding='utf-8'))
def digest(s): return hashlib.sha256(s.encode('utf-8')).hexdigest()


def current_view(prompt):
    """Remove earlier events, without interpreting the status or reading gold."""
    records=re.findall(r'(?m)^(R[0-9]{4})：([^\n]+)$',prompt)
    if not records: raise ValueError('no public records')
    lines=[]
    for req,body in records:
        events=re.findall(r'(\d{2}):(\d{2}) ([^；\n]+)',body)
        if len(events)!=2: raise ValueError('two public events required')
        ranked=[(int(h)*60+int(m),text) for h,m,text in events]
        if ranked[0][0]==ranked[1][0]: raise ValueError('ambiguous time')
        lines.append(req+'：当前状态='+max(ranked)[1])
    instruction='只根据每条工单的当前状态判断。找出仍不可用的工单，编号升序，每行只写一个编号；没有就写无。不要解释。'
    return ('<|im_start|>system\n只按资料筛选，不补信息。<|im_end|>\n<|im_start|>user\n'
            +'\n'.join(lines)+'\n'+instruction+'<|im_end|>\n<|im_start|>assistant\n')


def plans(fixtures):
    cfg=config(); result=[]
    if [r['req_id'] for r in fixtures]!=cfg['source_req_ids']: raise ValueError('fixed8 fixture order required')
    for index,ref in enumerate(fixtures):
        for position,condition in enumerate(cfg['orders'][index%4],1):
            prompt=ref['prompt'] if condition[0]=='T' else current_view(ref['prompt'])
            row={'req_id':f'v15-g{index:02d}-{condition}','split':'development','purpose':cfg['purpose'],
                 'source_req_id':ref['req_id'],'source_content_sha256':ref['content_sha256'],
                 'fixture_index':index,'condition':condition,'condition_position':position,
                 'category':ref['category'],'task_kind':'select_current','prompt':prompt,
                 'content_sha256':digest(prompt),'max_tokens':cfg['max_tokens'][ref['category']],
                 'temperature':0,'ignore_eos':False,'cache_prompt':False,'arrival_offset_ms':0}
            if condition[1]=='G': row['grammar']=ref['grammar']
            result.append(row)
    return result


def validate(plan,fixtures):
    cfg=config(); by_source={r['req_id']:r for r in fixtures}
    if len(plan)!=32 or len({r['req_id'] for r in plan})!=32: raise ValueError('exactly32 unique requests')
    if Counter(r['condition'] for r in plan)!=Counter({c:8 for c in cfg['conditions']}): raise ValueError('four balanced arms')
    if len({r['content_sha256'] for r in plan})!=16: raise ValueError('exactly16 paired prompts')
    for row in plan:
        ref=by_source[row['source_req_id']]; code=row['condition']
        expected=ref['prompt'] if code[0]=='T' else current_view(ref['prompt'])
        if row['prompt']!=expected or row['content_sha256']!=digest(expected): raise ValueError('fixed public view')
        if (code[1]=='G')!=('grammar' in row) or ('grammar' in row and row['grammar']!=ref['grammar']): raise ValueError('exact grammar switch')
        if any(k in row for k in ('expected_answer','expected_ids','facts_evaluation_only','target_count_evaluation_only')): raise ValueError('gold in public plan')
        if row['input_tokens']+row['max_tokens']+1>2048 or row['input_tokens']<1: raise ValueError('context budget')
        if row['purpose']!='posthoc_factor_diagnostic' or row['split']!='development': raise ValueError('diagnostic only')
    return {'status':'passed','planned':32,'distinct_prompts':16,'seen_source_facts':8,'new_source_facts':0,
            'duplicate_policy':'registered paired reuse; exact source bytes checked','online_updates_enabled':False,
            'G1_passed':False,'final_test_accessed':False}


def quality(ref,actual):
    raw=actual.get('content',''); trimmed=raw.strip(); lines=trimmed.splitlines()
    expected=ref['expected_answer']; known={f['id'] for f in ref['facts_evaluation_only']}
    valid=trimmed=='无' or bool(lines) and all(re.fullmatch(r'R[0-9]{4}',s) for s in lines) and lines==sorted(set(lines)) and set(lines)<=known
    natural=actual['terminal']=='completed' and actual['finish_reason']=='stop'
    return {'strict_correct':natural and raw==expected,'outer_whitespace_field_correct':natural and valid and trimmed==expected,
            'format_valid':bool(valid),'natural_end':natural,'output_limit':actual.get('finish_reason')=='length',
            'expected_answer':expected,'content':raw,'invalid_format_lines':[] if valid else lines}
