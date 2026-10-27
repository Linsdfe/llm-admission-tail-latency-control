"""Independent32-case diagnostic audit; no new task acceptance claim."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re


def read(p):return json.loads(p.read_text(encoding='utf-8'))
def rows(p):return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s]


def audit(run):
    summary=read(run/'summary.json');assert summary['status']=='completed' and summary['measured_requests']==32
    checks=0
    def check(value,label):
        nonlocal checks
        assert value,label
        checks+=1
    for name,digest in read(run/'source-sha256.json').items():
        check(hashlib.sha256((run/'source'/name).read_bytes()).hexdigest()==digest,'execution source')
    plan=rows(run/'public-plan.jsonl'); fixtures={r['req_id']:r for r in read(run/'fixtures-evaluation-only.json')}
    check(len(plan)==32 and len(fixtures)==8 and len({r['prompt'] for r in plan})==16,'fixed32/16/8')
    freeze=read(run/'pre-measurement-freeze.json')
    check(hashlib.sha256((run/'public-plan.jsonl').read_bytes()).hexdigest()==freeze['plan_sha256'],'plan fixed before outputs')
    tokenized={r['request']['content']:json.loads(r['response_body'])['tokens'] for r in rows(run/'tokenization.jsonl')}
    check(len(tokenized)==16,'all paired input tokenizations')
    result=[]
    for i,p in enumerate(plan,1):
        actual=rows(run/'cases'/f'q{i:03d}'/'replay/client.jsonl')[0];ref=fixtures[p['source_req_id']]
        check(not {'expected_answer','expected_ids','facts_evaluation_only'}&p.keys(),'no gold in plan')
        check(p['input_tokens']==len(tokenized[p['prompt']])==actual['timings']['prompt_n'],'native tokenization')
        check(p['prompt']==actual['prompt']==actual['native_final']['prompt'],'actual input')
        settings=actual['native_final']['generation_settings']
        check(settings['ignore_eos'] is False and settings['temperature']==0 and actual['cached_prompt_tokens']==0,'fixed sampling')
        code=p['condition'];grammar=p.get('grammar','')
        check(settings.get('grammar','')==grammar and bool(grammar)==(code[1]=='G'),'native grammar switch')
        if code[0]=='T':check(p['prompt']==ref['prompt'],'original temporal prompt unchanged')
        else:
            check('09:10' not in p['prompt'] and '09:40' not in p['prompt'],'no clock in state view')
            for fact in ref['facts_evaluation_only']:
                check(fact['id']+'：当前状态='+max(fact['events_in_written_order'])[1] in p['prompt'],'public latest phrase retained')
        if grammar:check(grammar==ref['grammar'],'same public language, no gold restriction')
        check(actual['terminal']=='completed' and actual['output_tokens_known'],'all measured completions')
        check(actual['send_lag_ms']<=50 and p['input_tokens']+p['max_tokens']+1<=2048,'lag and context')
        ids=sorted(f['id'] for f in ref['facts_evaluation_only'] if f['current_down'])
        expected='\n'.join(ids) if ids else '无';check(expected==ref['expected_answer'],'independent truth')
        raw=actual['content'];trim=raw.strip();lines=trim.splitlines();known={f['id'] for f in ref['facts_evaluation_only']}
        valid=trim=='无' or bool(lines) and all(re.fullmatch(r'R[0-9]{4}',s) for s in lines) and lines==sorted(set(lines)) and set(lines)<=known
        natural=actual['finish_reason']=='stop'
        result.append({'req_id':p['req_id'],'source_req_id':p['source_req_id'],'fixture_index':p['fixture_index'],
                       'category':p['category'],'condition':code,'condition_position':p['condition_position'],
                       'prompt':p['prompt'],'expected_answer':expected,'content':raw,
                       'strict_correct':natural and raw==expected,'outer_whitespace_field_correct':bool(natural and valid and trim==expected),
                       'format_valid':bool(valid),'output_limit':actual['finish_reason']=='length','terminal':actual['terminal'],
                       'input_tokens':p['input_tokens'],'output_tokens':actual['output_tokens'],'finish_reason':actual['finish_reason']})
    cells={}
    for code in ('TG','TU','SG','SU'):
        group=[r for r in result if r['condition']==code]
        check(len(group)==8,'each arm denominator8')
        cells[code]={'total':8,**{k:sum(r[k] for r in group) for k in ('strict_correct','outer_whitespace_field_correct','format_valid','output_limit')},
                     'by_category':{c:{'total':4,'strict_correct':sum(r['strict_correct'] for r in group if r['category']==c),
                                      'field_correct':sum(r['outer_whitespace_field_correct'] for r in group if r['category']==c)} for c in ('interactive','background')}}
    paired=[];lookup={(r['source_req_id'],r['condition']):r for r in result}
    for first,second in [('TG','TU'),('SG','SU'),('TG','SG'),('TU','SU')]:
        for metric in ('strict_correct','outer_whitespace_field_correct'):
            counts=Counter()
            for req in fixtures:
                a,b=lookup[(req,first)][metric],lookup[(req,second)][metric]
                counts['improved' if not a and b else 'worse' if a and not b else 'both_correct' if a else 'both_wrong']+=1
            paired.append({'from':first,'to':second,'metric':metric,**dict(counts)})
    prior={r['source_req_id']:r for r in rows(run/'prior-TG-output.jsonl')}
    matches=sum(lookup[(req,'TG')]['content']==prior[req]['content'] for req in fixtures)
    events=[]
    for line in (run/'server.log').read_text(encoding='utf-8',errors='replace').splitlines():
        if 'SCHED_CAP_LAUNCH' in line or 'SCHED_CAP_RELEASE' in line:
            d=dict(x.split('=',1) for x in line.split() if '=' in x)
            events.append(('release' if 'SCHED_CAP_RELEASE' in line else 'launch',d['clientid']))
    launch=Counter(req for kind,req in events if kind=='launch');release=Counter(req for kind,req in events if kind=='release')
    check(launch==release and set(launch)=={r['req_id'] for r in plan}|{'warm0','warm1'},'native34 pairs')
    check(all(n==1 for n in launch.values()),'once native lifecycle')
    check(not summary['G1_passed'] and not summary['final_test_accessed'],'scope')
    return {'status':'passed_diagnostic_evidence_audit','checks':checks,'measured_requests':32,'warmups':2,
            'seen_fixtures':8,'distinct_prompts':16,'new_source_facts':0,'cells':cells,'paired':paired,
            'TG_exact_content_reproduced':matches,'TG_reproduction_denominator':8,
            'new_task_acceptance_evaluated':False,'subjective_score_unchanged':88,
            'G1_passed':False,'final_test_accessed':False,'request_results':result}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();result=audit(a.run_dir)
    with a.out.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!='request_results'},ensure_ascii=False))
