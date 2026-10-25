#!/usr/bin/env python3
"""Independent full-row audit; no new collector, generator, estimator or99 imports."""
import argparse
from collections import Counter,defaultdict
import importlib.util
import json
import math
from pathlib import Path
import re
import statistics
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('frozen_v5_audit',ROOT/'tests/integration/audit_p1_explicit_instructions_v5.py')
old=importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
read,rows,sha,text_sha=old.read,old.rows,old.sha,old.text_sha

def quantile(values,p):
    ordered=sorted(values); pos=(len(ordered)-1)*p; lo=int(pos)
    return ordered[lo]+(ordered[min(lo+1,len(ordered)-1)]-ordered[lo])*(pos-lo)

def independent_model(cal):
    def fit(group,candidate):
        buckets=defaultdict(list)
        for row in group:
            key=row['category'] if candidate=='category_median' else row['category']+':'+row['task_kind']
            buckets[key].append(row['output_tokens'])
        return {k:statistics.median(v) for k,v in buckets.items()}
    candidates=['category_median','category_task_median']; scores={}; residuals={}
    for candidate in candidates:
        errors=[]
        for fold in range(4):
            train=[r for r in cal if r['kind_instance']!=fold]; model=fit(train,candidate)
            for row in [r for r in cal if r['kind_instance']==fold]:
                key=row['category'] if candidate==candidates[0] else row['category']+':'+row['task_kind']
                errors.append(row['output_tokens']-min(row['max_tokens'],max(1,model[key])))
        scores[candidate]=sum(abs(e) for e in errors)/24; residuals[candidate]=errors
    chosen=min(candidates,key=lambda x:(scores[x],candidates.index(x)))
    return chosen,fit(cal,chosen),scores,max(0,math.ceil(max(residuals[chosen])))

def audit(run,analysis):
    run=run.resolve(strict=True); n=0; evidence={}
    def check(value,label):
        nonlocal n
        n+=1
        if not value: raise AssertionError(label)
    def track(p): evidence[p.relative_to(ROOT).as_posix()]=sha(p)
    status=read(run/'summary.json'); cfg=read(run/'environment.json')['config']; result=read(analysis/'summary.json')
    check(status==read(run/'batch-status.json') and status['status']=='completed','complete root')
    check((status['measured_requests'],status['completion_requests'],status['warmup_requests'])==(312,332,20),'312 plus20')
    check(status['final_test_generated_or_read'] is False and result['G1_passed'] is False,'final/G1 isolation')
    mapping=read(run/'source-sha256.json'); check(len(mapping)==87,'87 collector sources')
    for rel,digest in mapping.items(): check(sha(ROOT/rel)==sha(run/'source'/rel)==digest,'source snapshot '+rel)
    oldrun=ROOT/'results/raw/development/20261025-2210-p1-model-adaptation-v6'
    old_sources=read(oldrun/'source-sha256.json')
    for rel,digest in old_sources.items(): check(mapping[rel]==digest,'old frozen source '+rel)
    after=read(run/'fingerprints-after.json')
    check(after['model_sha256']==cfg['model_sha256']=='626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d','3B model fingerprint')
    check(after['engine_runtime_sha256']==read(run/'engine-runtime-sha256.json'),'runtime unchanged')
    gate=read(run/'structured-client-gate/summary.json'); check(gate['status']=='passed','native client gate')
    for rel,digest in gate['source_sha256'].items(): check(sha(ROOT/rel)==digest,'gate source '+rel)
    refs=rows(run/'evaluation-plan.jsonl'); reference={r['req_id']:r for r in refs}
    check(len(refs)==len(reference)==len({r['content_sha256'] for r in refs})==96,'96 distinct contents')
    check(Counter(r['split'] for r in refs)=={'calibration':24,'development':72},'split counts')
    check(Counter(r['seed'] for r in refs)=={s:24 for s in (27001,27101,27102,27103)},'seed counts')
    check(not {r['content_sha256'] for r in refs}&{r['content_sha256'] for r in rows(oldrun/'evaluation-plan.jsonl')},'v6 content not reused')
    tokenization=rows(run/'profile/tokenization.jsonl'); tokens={r['request']['content']:json.loads(r['response_body'])['tokens'] for r in tokenization}
    for ref in refs:
        check(old.independent_prompt_matches(ref),'public C prompt rebuilt from source')
        answer=old.independent_answer(ref)
        check(ref['expected_lines']==['|'.join(v) for v in answer] and ref['expected_answer']=='\n'.join(ref['expected_lines']),'independent reference')
        ids=tokens[ref['prompt']]
        check(ref['input_tokens']==len(ids) and ref['token_ids_sha256']==text_sha(json.dumps(ids,separators=(',',':'))),'actual token IDs')
        check(ids.count(151644)==3 and ids.count(151645)==2 and 151643 not in ids,'ChatML parsed')
        low,high=cfg['input_token_buckets'][ref['category']][ref['input_token_bucket']]
        check(low<=len(ids)<=high and len(ids)+ref['max_tokens']+1<=2048,'bucket/context')
    full=read(analysis/'fulltexts.json'); check(len(full)==312,'all312 derived fulltexts')
    derived={(x['phase'],x['round'],x['req_id']):x for x in full}
    profile=[]; round_values={}; cap_count=0; measured=0; lag_max=0
    directories=[('profile',None,run/'profile','original')]+[('load',f"r{s['repeat']}-{s['policy']}",run/'rounds'/f"r{s['repeat']}-{s['policy']}",s['policy']) for s in cfg['execution_order']]
    for phase,name,directory,policy in directories:
        check(read(directory/'service-stop.json')['exit_code']==0,'owned service exit0')
        props=read(directory/'props.json'); check(props['total_slots']==2 and props['default_generation_settings']['n_ctx']==2048,'slot context')
        command=read(directory/'server-command.json')
        for flag,value in (('-ngl','99'),('-c','4096'),('-np','2'),('-b','2048'),('-ub','512'),('-lv','4')): check(command[command.index(flag)+1]==value,'common '+flag)
        log=(directory/'server.log').read_text(encoding='utf-8',errors='replace')
        check('offloaded 37/37 layers to GPU' in log,'actual full offload')
        env=read(directory/'server-policy-environment.json'); cap=1 if policy=='capped' else 0
        check(env=={'LLAMA_SERVER_SIMPLE_SCHEDULER':'0' if policy=='original' else '1','LLAMA_SERVER_BACKGROUND_AGING_MS':'5000','LLAMA_SERVER_MAX_RUNNING_BACKGROUND':str(cap)},'actual policy flags')
        check(any('SCHED_CONFIG' in l and 'mode='+env['LLAMA_SERVER_SIMPLE_SCHEDULER'] in l and 'max_running_background='+str(cap) in l for l in log.splitlines()),'native policy confirm')
        events=[]
        for line in log.splitlines():
            if 'SCHED_CAP_LAUNCH' in line or 'SCHED_CAP_RELEASE' in line:
                item=dict(v.split('=',1) for v in line.split() if '=' in v); item['kind']='launch' if 'SCHED_CAP_LAUNCH' in line else 'release'; events.append(item)
        active={}; background=0
        for e in events:
            check(int(e['running_background_before'])==background,'CAP continuity')
            if e['kind']=='launch': check(e['slot'] not in active,'free native slot'); active[e['slot']]=e
            else: check(e['slot'] in active and active[e['slot']]['id']==e['id'],'release matching task'); active.pop(e['slot'])
            background=int(e['running_background_after']); check(background>=0 and (not cap or background<=cap),'cap bound')
        check(not active and background==0,'closed CAP'); cap_count+=len(events)
        warm=rows(directory/'warmup/client.jsonl'); check(len(warm)==2 and all(r['terminal']=='completed' and r['cached_prompt_tokens']==0 for r in warm),'2warm cache0')
        plan=rows(directory/'plan.jsonl'); expected=96 if phase=='profile' else 24; check(len(plan)==expected,'plan count')
        by_id={r['req_id']:r for r in plan}
        paths=sorted((directory/'cases').glob('q*/replay/client.jsonl')) if phase=='profile' else [directory/'replay/client.jsonl']
        actual=[r for path in paths for r in rows(path)]; check(len(actual)==expected,'all raw records')
        quality_values=[]
        for row in actual:
            measured+=1; ref=reference[row['req_id']]; check(all(row[k]==v for k,v in by_id[row['req_id']].items()),'trace actual equality')
            check(not old.FORBIDDEN & row.keys(),'oracle isolation')
            check(row['prompt']==ref['prompt'] and row['input_tokens']==ref['input_tokens'],'prompt profile/load fixed')
            if phase=='load':
                index=sorted(r['req_id'] for r in plan if r['category']==row['category']).index(row['req_id'])
                arrival=index*1000 if row['category']=='interactive' else (index//3)*3000+250+(index%3)*50
                check(row['arrival_offset_ms']==arrival and 'release_offset_ms' not in row,'open fixed arrivals')
            else: check(row['arrival_offset_ms']==0,'serial arrival0'); profile.append(row)
            check(row['terminal'] in ('completed','rejected','timeout','cancelled','failed','unfinished'),'all terminals')
            if row['send_lag_ms'] is not None: lag_max=max(lag_max,row['send_lag_ms']); check(row['send_lag_ms']<=50,'send lag')
            if row['terminal']=='completed':
                final=row['native_final']; settings=final['generation_settings']
                check(row['cache_prompt'] is False and row['cached_prompt_tokens']==0 and row['input_tokens']==row['new_prompt_tokens']==final['tokens_evaluated'],'native cache/input')
                check(row['output_tokens_known'] is True and row['output_tokens']==row['timings']['predicted_n']==final['tokens_predicted'] and 0<row['output_tokens']<=row['max_tokens'],'native output')
                check(settings['grammar']==old.independent_grammar(row['prompt'],row['category']) and settings['grammar_lazy'] is False,'native public grammar')
                check(settings['temperature']==0 and settings['ignore_eos'] is False and final['truncated'] is False,'native sampling/context')
                check(row['actual_send_offset_ms']<=row['first_content_offset_ms']<=row['end_offset_ms'] and abs(row['total_first_ms']-row['first_content_offset_ms']+row['arrival_offset_ms'])<.001,'first content timing')
                own=[e for e in events if e['clientid']==row['req_id']]; check(len(own)==2 and own[0]['kind']=='launch' and own[1]['kind']=='release','request CAP pair')
            fact=old.independent_fields(row.get('content') or '',old.independent_answer(ref),row['task_kind'])
            got=derived[(phase,name,row['req_id'])]
            check(got['content']==row.get('content','') and got['reference']==ref,'all fulltext and source values')
            for k,ind in (('business_correct','business_correct'),('schema_valid','syntax_valid'),('target_fields_correct','correct_fields'),('target_fields_expected','planned_fields')): check(got['quality'][k]==fact[ind],'independent quality '+k)
            check(got['quality']['completed_and_correct']==(row['terminal']=='completed' and fact['business_correct']),'correct completion only')
            quality_values.append((row,got['quality']))
        for path in paths: track(path)
        if phase=='load':
            reported=next(x for x in result['rounds'] if x['round']==name); summary=read(directory/'replay/summary.json')
            check(reported['terminal_counts']==dict(Counter(r['terminal'] for r in actual))|{s:0 for s in ()} or all(reported['terminal_counts'][s]==sum(r['terminal']==s for r in actual) for s in reported['terminal_counts']),'six-state denominator')
            correct=sum(q['completed_and_correct'] for _,q in quality_values)
            check(reported['all_plan_correct']==correct and reported['planned']==24,'quality whole-plan denominator')
            check(abs(reported['correct_tasks_per_second']-correct/max(r['end_offset_ms'] for r in actual)*1000)<1e-9,'correct task/s denominator')
            check(reported['planned_fields']==88 and reported['correct_fields']==sum(q['target_fields_correct'] for _,q in quality_values),'88 fields')
            for c in ('interactive','background'):
                complete=[r for r in actual if r['category']==c and r['terminal']=='completed']
                p95=quantile([r['total_first_ms'] for r in complete],.95) if complete else None
                value=reported['classes'][c]['total_first_p95_ms']
                check((value is None and p95 is None) or (value is not None and p95 is not None and abs(value-p95)<1e-6),'type7 completed P95')
            samples=rows(directory/'metrics-samples.jsonl'); check(samples and any('llamacpp:requests_deferred ' in x.get('response_body','') for x in samples),'native queue metrics sampled')
            round_values[name]=actual
    check(measured==312 and cap_count==status['cap_events'],'global counts/CAP')
    fit=read(run/'calibration-model.json'); selected,values,scores,margin=independent_model([r for r in profile if r['split']=='calibration'])
    check((fit['candidate'],fit['values'],fit['cross_validation_mae'],fit['margin'])==(selected,values,scores,margin),'independent calibration4fold selection')
    predictions=rows(run/'profile-predictions.jsonl'); pred={r['req_id']:r for r in predictions}
    for row in profile:
        k=row['category'] if selected=='category_median' else row['category']+':'+row['task_kind']; point=min(row['max_tokens'],max(1,values[k])); upper=min(row['max_tokens'],math.ceil(point+margin))
        check(pred[row['req_id']]['prediction']==point and pred[row['req_id']]['upper']==upper,'frozen profile prediction')
    for pair in result['paired_differences']:
        r0=next(r for r in result['rounds'] if r['repeat']==pair['repeat'] and r['policy']=='original'); r1=next(r for r in result['rounds'] if r['repeat']==pair['repeat'] and r['policy']==pair['policy'])
        check(pair['interactive_p95_reduction_ms']==r0['classes']['interactive']['total_first_p95_ms']-r1['classes']['interactive']['total_first_p95_ms'],'paired reduction')
        check(pair['correct_tasks_change']==r1['all_plan_correct']-r0['all_plan_correct'],'paired quality change')
    return {'status':'passed','checks':n,'measured_records':measured,'warmup_records':20,'cap_events':cap_count,'max_send_lag_ms':lag_max,'rounds':9,'frozen_sources':87,'G1_passed':False,'final_test_accessed':False,'evidence_sha256':evidence}

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--run-dir',type=Path,required=True); p.add_argument('--analysis-dir',type=Path,required=True); p.add_argument('--out-dir',type=Path,required=True); a=p.parse_args(); result=audit(a.run_dir,a.analysis_dir)
    a.out_dir.mkdir(parents=True,exist_ok=False); (a.out_dir/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='evidence_sha256'},ensure_ascii=False))
