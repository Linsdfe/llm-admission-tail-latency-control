"""Read-only, independent checks of the64-case corpus and actual native completions."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

def read(p):return json.loads(p.read_text(encoding='utf-8'))
def rows(p):return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def audit(run):
    checks=0
    def check(x,label):
        nonlocal checks
        assert x,label
        checks+=1
    summary=read(run/'summary.json');check(summary['status']=='completed' and summary['measured_requests']==64,'batch complete')
    for p,h in read(run/'source-sha256.json').items():check(sha(run/'source'/p)==h,'execution sources')
    refs=rows(run/'evaluation-plan.jsonl');plan=rows(run/'public-plan.jsonl');features=rows(run/'public-features-before-data.jsonl')
    freeze=read(run/'pre-measurement-freeze.json')
    check(sha(run/'public-plan.jsonl')==freeze['plan_sha256'] and sha(run/'public-features-before-data.jsonl')==freeze['public_features_sha256'],'pre-output freeze')
    check(len(plan)==len(refs)==len(features)==len({r['content_sha256'] for r in plan})==64,'64 unique')
    tokens={r['request']['content']:json.loads(r['response_body'])['tokens'] for r in rows(run/'tokenization.jsonl')}
    output_tokens={r['request']['content']:json.loads(r['response_body'])['tokens'] for r in rows(run/'public-output-tokenization.jsonl')}
    results=[]
    for i,(p,r,f) in enumerate(zip(plan,refs,features),1):
        actual=rows(run/'cases'/f'q{i:03d}'/'replay/client.jsonl')[0]
        check(not {'expected_answer','source_records_evaluation_only','target_ids'}&p.keys(),'gold excluded')
        check(actual['prompt']==p['prompt']==r['prompt']==actual['native_final']['prompt'],'sent prompt')
        check(p['input_tokens']==len(tokens[p['prompt']])==actual['timings']['prompt_n'],'actual input length')
        lo,hi=p['input_token_range'];check(lo<=p['input_tokens']<=hi and p['input_tokens']+p['max_tokens']+1<=2048,'input and capacity')
        source=dict(r['source_records_evaluation_only']);answer='\n'.join(t+'|'+source[t] for t in r['target_ids'])
        check(answer==r['expected_answer']==f['public_rule_answer'],'independent reference and public rule')
        check(f['rule_output_tokens']==len(output_tokens[answer])+1,'pre-output point with EOS')
        setting=actual['native_final']['generation_settings']
        check(setting['grammar']==p['grammar'] and setting['ignore_eos'] is False and setting['temperature']==0,'native grammar and sampling')
        check(p['grammar'].count('[1-9]')==p['target_count'] and p['grammar'].count('[0-9]')==p['target_count'],'values unconstrained by gold')
        check(actual['cached_prompt_tokens']==0 and actual['output_tokens_known'] and actual['terminal']=='completed','complete and cache off')
        check(actual['send_lag_ms']<=50,'send lag')
        check(hashlib.sha256(p['prompt'].encode()).hexdigest()==p['content_sha256'],'content digest')
        correct=actual['finish_reason']=='stop' and actual['content']==answer
        results.append(dict(req_id=p['req_id'],seed=p['seed'],category=p['category'],task_kind=p['task_kind'],target_count=p['target_count'],
            input_tokens=p['input_tokens'],output_tokens=actual['output_tokens'],rule_output_tokens=f['rule_output_tokens'],
            predicted_ms=actual['timings']['predicted_ms'],prompt_ms=actual['timings']['prompt_ms'],
            whole_correct=correct,output_limit=actual['finish_reason']=='length',content=actual['content'],expected_answer=answer))
    cells={}
    for c in ('interactive','background'):
        for k in ('extract','organize'):
            group=[r for r in results if r['category']==c and r['task_kind']==k];check(len(group)==16,'four cells each16')
            cells[c+'/'+k]={'total':16,'whole_correct':sum(r['whole_correct'] for r in group)}
    group_class={c:{'total':32,'whole_correct':sum(r['whole_correct'] for r in results if r['category']==c),
        'distinct_correct_output_lengths':sorted({r['output_tokens'] for r in results if r['category']==c and r['whole_correct']})} for c in ('interactive','background')}
    count=sum(r['whole_correct'] for r in results)
    passed=count>=61 and all(g['whole_correct']>=30 and len(g['distinct_correct_output_lengths'])>=4 for g in group_class.values()) and all(g['whole_correct']>=15 for g in cells.values()) and all(sum(r['whole_correct'] for r in results if r['task_kind']==k)>=30 for k in ('extract','organize'))
    events=[]
    for line in (run/'server.log').read_text(encoding='utf-8',errors='replace').splitlines():
        if 'SCHED_CAP_LAUNCH' in line or 'SCHED_CAP_RELEASE' in line:
            fields=dict(s.split('=',1) for s in line.split() if '=' in s);events.append(('release' if 'SCHED_CAP_RELEASE' in line else 'launch',fields['clientid']))
    launch=Counter(req for kind,req in events if kind=='launch');release=Counter(req for kind,req in events if kind=='release')
    check(launch==release and set(launch)=={p['req_id'] for p in plan}|{'warm0','warm1'} and all(n==1 for n in launch.values()),'66 native lifecycle pairs')
    check(summary['service_exit_zero'] and not summary['G1_passed'] and not summary['final_test_accessed'],'exit and stage')
    return dict(status='passed_evidence_audit',checks=checks,whole_correct=count,total=64,warmups=2,cells=cells,by_category=group_class,
        rule_exact_actual_length=sum(r['output_tokens']==r['rule_output_tokens'] for r in results),
        output_limits=sum(r['output_limit'] for r in results),task_acceptance_passed=passed,
        performance_repeated_runs=1,scheduler_comparison_started=False,G1_passed=False,request_results=results)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    result=audit(a.run_dir)
    with a.out.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!='request_results'},ensure_ascii=False))
