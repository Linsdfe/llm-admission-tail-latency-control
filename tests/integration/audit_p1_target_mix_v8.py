#!/usr/bin/env python3
"""Independent v8 full-occurrence audit; no new collector/generator/target/100 imports."""
import argparse
from collections import Counter,defaultdict
import importlib.util
import json
import math
from pathlib import Path
import re
import statistics
from fractions import Fraction
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
    check((status['measured_requests'],status['completion_requests'],status['warmup_requests'])==(2094,2114,20),'2094 plus20')
    check(status['final_test_generated_or_read'] is False and result['G1_passed'] is False,'final/G1 isolation')
    mapping=read(run/'source-sha256.json'); check(len(mapping)==96,'96 collector sources')
    for rel,digest in mapping.items(): check(sha(ROOT/rel)==sha(run/'source'/rel)==digest,'source snapshot '+rel)
    oldrun=ROOT/'results/raw/development/20261025-2240-p1-system-workload-v7'
    old_sources=read(oldrun/'source-sha256.json')
    for rel,digest in old_sources.items(): check(mapping[rel]==digest,'old frozen source '+rel)
    after=read(run/'fingerprints-after.json')
    check(after['model_sha256']==cfg['model_sha256']=='626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d','3B model fingerprint')
    check(after['engine_runtime_sha256']==read(run/'engine-runtime-sha256.json'),'runtime unchanged')
    gate=read(run/'structured-client-gate/summary.json'); check(gate['status']=='passed','native client gate')
    for rel,digest in gate['source_sha256'].items(): check(sha(ROOT/rel)==digest,'gate source '+rel)
    refs=rows(run/'evaluation-plan.jsonl'); reference={r['req_id']:r for r in refs}
    check(len(refs)==len(reference)==len({r['content_sha256'] for r in refs})==72,'72 distinct contents')
    check(Counter(r['split'] for r in refs)=={'development':72},'split counts')
    check(Counter(r['seed'] for r in refs)=={s:24 for s in (28101,28102,28103)},'seed counts')
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
    full=rows(analysis/'request-results.jsonl'); check(len(full)==2094,'all2094 normalized outputs')
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
        plan=rows(directory/'plan.jsonl'); expected=24 if phase=='profile' else 230; check(len(plan)==expected,'plan count')
        by_id={r['req_id']:r for r in plan}
        paths=sorted((directory/'cases').glob('q*/replay/client.jsonl')) if phase=='profile' else [directory/'replay/client.jsonl']
        actual=[r for path in paths for r in rows(path)]; check(len(actual)==expected,'all raw records')
        quality_values=[]
        for row in actual:
            measured+=1; ref=reference[row['source_req_id']]; check(all(row[k]==v for k,v in by_id[row['req_id']].items()),'trace actual equality')
            check(not old.FORBIDDEN & row.keys(),'oracle isolation')
            check(row['prompt']==ref['prompt'] and row['input_tokens']==ref['input_tokens'],'prompt profile/load fixed')
            if phase=='load':
                index=int(row['req_id'].rsplit('occ',1)[1])
                arrival=index*500 if row['category']=='interactive' else (index//3)*10000+250+(index%3)*50
                pool=sorted(r['req_id'] for r in refs if r['seed']==row['seed'] and r['category']==row['category'] and r['input_token_bucket']==index%3)
                check(len(pool)==4 and row['source_req_id']==pool[(index//3)%4],'fixed corpus occurrence mapping')
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
            check(got['content']==row.get('content','') and got['source_req_id']==ref['req_id'],'all fulltext and source values')
            for k,ind in (('business_correct','business_correct'),('schema_valid','syntax_valid'),('target_fields_correct','correct_fields'),('target_fields_expected','planned_fields')): check(got['quality'][k]==fact[ind],'independent quality '+k)
            check(got['quality']['completed_and_correct']==(row['terminal']=='completed' and fact['business_correct']),'correct completion only')
            quality_values.append((row,got['quality']))
        for path in paths: track(path)
        if phase=='load':
            reported=next(x for x in result['rounds'] if x['round']==name); summary=read(directory/'replay/summary.json')
            check(reported['terminal_counts']==dict(Counter(r['terminal'] for r in actual))|{s:0 for s in ()} or all(reported['terminal_counts'][s]==sum(r['terminal']==s for r in actual) for s in reported['terminal_counts']),'six-state denominator')
            correct=sum(q['completed_and_correct'] for _,q in quality_values)
            check(reported['all_plan_correct']==correct and reported['planned']==230,'quality whole-plan denominator')
            check(abs(reported['correct_tasks_per_second']-correct/max(r['end_offset_ms'] for r in actual)*1000)<1e-9,'correct task/s denominator')
            check(reported['planned_fields']==sum(len(reference[r['source_req_id']]['target_ids']) for r in actual) and reported['correct_fields']==sum(q['target_fields_correct'] for _,q in quality_values),'occurrence field denominator')
            for c in ('interactive','background'):
                complete=[r for r in actual if r['category']==c and r['terminal']=='completed']
                p95=quantile([r['total_first_ms'] for r in complete],.95) if complete else None
                value=reported['classes'][c]['total_first_p95_ms']
                check((value is None and p95 is None) or (value is not None and p95 is not None and abs(value-p95)<1e-6),'type7 completed P95')
                end95=quantile([r['completion_from_planned_ms'] for r in complete],.95) if complete else None
                value=reported['classes'][c]['completion_p95_ms']
                check((value is None and end95 is None) or (value is not None and end95 is not None and abs(value-end95)<1e-6),'type7 completion P95')
            if all(r['terminal']=='completed' and r['output_tokens_known'] for r in actual):
                rate=sum(r['output_tokens'] for r in actual)/max(r['end_offset_ms'] for r in actual)*1000
                check(abs(reported['output_tps']-rate)<1e-9,'whole-run output denominator')
            samples=rows(directory/'metrics-samples.jsonl'); check(samples and any('llamacpp:requests_deferred ' in x.get('response_body','') for x in samples),'native queue metrics sampled')
            round_values[name]=actual
    check(measured==2094 and cap_count==status['cap_events'],'global counts/CAP')
    artifact=read(run/'classification-artifact.json')
    cal=[]
    for path in sorted(p for p in (oldrun/'profile/cases').glob('q*/replay/client.jsonl') if 'v7-cal-27001-' in p.parent.parent.name):
        check(sha(path)==artifact['source_sha256'][path.relative_to(ROOT).as_posix()],'frozen calibration file')
        one=rows(path); check(len(one)==1 and one[0]['split']=='calibration','calibration only'); cal+=one
    check(len(cal)==24,'24 calibration authority')
    selected,values,scores,margin=independent_model(cal)
    fit=artifact['point_model']
    check((fit['candidate'],fit['values'],fit['cross_validation_mae'],fit['margin'])==(selected,values,scores,margin),'frozen v7 model independently refit from calibration')
    target_counts={'interactive':[67,67,66],'background':[10,10,10]}
    pairs=sorted((r['output_tokens'],Fraction(target_counts[r['category']][r['input_token_bucket']],4)) for r in cal)
    mass=sum(w for _,w in pairs); total=Fraction(0); cutoff=None
    for value,weight in pairs:
        total+=weight
        if total>=Fraction(85,100)*mass: cutoff=value; break
    check(mass==230 and cutoff==artifact['weighted_cutoff']==12,'independent target weighted cutoff')
    check(quantile([r['output_tokens'] for r in cal],.85)==artifact['ordinary_cutoff']==36,'ordinary diagnostic preserved')
    predictions={r['req_id']:r for r in rows(run/'corpus-predictions.jsonl')}; matrix={c:{'short':0,'long':0} for c in target_counts}
    for ref in refs:
        key=ref['category'] if selected=='category_median' else ref['category']+':'+ref['task_kind']
        point=min(ref['max_tokens'],max(1,values[key])); upper=min(ref['max_tokens'],math.ceil(point+margin)); label='short' if point<=cutoff else 'long'
        check(predictions[ref['req_id']]['prediction']==point and predictions[ref['req_id']]['upper']==upper and predictions[ref['req_id']]['predicted_class']==label,'frozen corpus prediction')
        matrix[ref['category']][label]+=1
        check(all(int(identity[1:])>=10 for identity in ref['target_ids']),'new fixed public ID namespace')
    check(matrix==result['classification']['corpus_business_by_predicted_class']=={'interactive':{'short':36,'long':0},'background':{'short':0,'long':36}},'classification overlap explicit')
    for item in full:
        p=predictions[item['source_req_id']]
        check(all(item[k]==p[k] for k in ('prediction','upper','predicted_class')),'occurrence prediction source')
    originals=[r for r in full if r['phase']=='load' and r['round'].endswith('-original')]
    known=[r for r in originals if r['output_tokens'] is not None]
    check(len(originals)==690 and result['classification']['original_occurrences_known']==len(known),'690 original occurrences')
    actual_error=sum(('short' if r['output_tokens']<=cutoff else 'long')!=r['predicted_class'] for r in known)
    mae=sum(abs(r['output_tokens']-r['prediction']) for r in known)/len(known)
    check(result['classification']['actual_output_class_errors']==actual_error and abs(result['classification']['output_mae_on_original_occurrences']-mae)<1e-9,'actual length classification and error')
    check(result['classification']['upper_coverage_on_original_occurrences']==sum(r['output_tokens']<=r['upper'] for r in known),'empirical coverage')
    witnesses=read(run/'ordering-witnesses.json'); check(len(witnesses)==4,'4 model-free witnesses')
    for witness in witnesses:
        now=witness['now_ms']; tasks=witness['tasks']; state=witness['state']
        def priority(row): return 1 if row['category']=='interactive' else 0 if now-row['enqueued_ms']>=5000 else 2
        simple=[r['req_id'] for r in sorted(tasks,key=lambda r:(priority(r),r['enqueued_ms'],r['task_id']))]
        history=state['history'][-128:]; theta=quantile(history,.8) if len(history)>=32 else 5000
        shorts=sorted([r for r in tasks if r['category']=='interactive'],key=lambda r:(r['enqueued_ms'],r['req_id']))
        longs=sorted([r for r in tasks if r['category']=='background'],key=lambda r:(r['enqueued_ms'],r['req_id']))
        aged=[r for r in longs if now-r['enqueued_ms']>theta]
        candidate=([aged[0]]+shorts+[r for r in longs if r!=aged[0]]) if state['debt'] and aged else shorts+longs
        check(witness['simple_order']==simple and witness['adaptation_order']==[r['req_id'] for r in candidate] and witness['theta']==theta,'independent hypothetical order')
    check([w['different'] for w in witnesses]==[False,True,False,True],'two logical differences not native implementation')
    for pair in result['paired_differences']:
        r0=next(r for r in result['rounds'] if r['repeat']==pair['repeat'] and r['policy']=='original'); r1=next(r for r in result['rounds'] if r['repeat']==pair['repeat'] and r['policy']==pair['policy'])
        check(pair['interactive_p95_reduction_ms']==r0['classes']['interactive']['total_first_p95_ms']-r1['classes']['interactive']['total_first_p95_ms'],'paired reduction')
        check(pair['correct_tasks_change']==r1['all_plan_correct']-r0['all_plan_correct'],'paired quality change')
        b0=r0['classes']['background']['completion_p95_ms']; b1=r1['classes']['background']['completion_p95_ms']
        if b0 and b1 is not None: check(abs(pair['background_completion_p95_increase_fraction']-(b1/b0-1))<1e-12,'paired background cost')
        if r0['output_tps'] and r1['output_tps'] is not None: check(abs(pair['output_rate_loss_fraction']-(1-r1['output_tps']/r0['output_tps']))<1e-12,'paired output rate cost')
    for policy in ('original','simple','capped'):
        group=[r for r in result['rounds'] if r['policy']==policy]
        check(len(group)==3 and result['by_policy'][policy]['interactive_p95_ms']['p50']==statistics.median(r['classes']['interactive']['total_first_p95_ms'] for r in group),'median of round P95')
        check(result['by_policy'][policy]['background_p95_ms']['p50']==statistics.median(r['classes']['background']['completion_p95_ms'] for r in group),'median background P95')
        check(result['by_policy'][policy]['correct_total']==sum(r['all_plan_correct'] for r in group),'policy correct total')
    return {'status':'passed','checks':n,'measured_records':measured,'warmup_records':20,'cap_events':cap_count,'max_send_lag_ms':lag_max,'rounds':9,'frozen_sources':96,'G1_passed':False,'final_test_accessed':False,'evidence_sha256':evidence}

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--run-dir',type=Path,required=True); p.add_argument('--analysis-dir',type=Path,required=True); p.add_argument('--out-dir',type=Path,required=True); a=p.parse_args(); result=audit(a.run_dir,a.analysis_dir)
    a.out_dir.mkdir(parents=True,exist_ok=False); (a.out_dir/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='evidence_sha256'},ensure_ascii=False))
