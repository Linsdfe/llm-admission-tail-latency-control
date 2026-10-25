#!/usr/bin/env python3
"""Independent v6 audit using frozen v5 oracle; no v6 collector/98 imports."""
import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('frozen_v5_audit',ROOT/'tests/integration/audit_p1_explicit_instructions_v5.py')
old=importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
read,rows,sha,text_sha=old.read,old.rows,old.sha,old.text_sha

def audit(run,analysis):
    run=run.resolve(strict=True); n=0; evidence={}
    def check(value,label):
        nonlocal n
        n+=1
        if not value: raise AssertionError(label)
    def track(path): evidence[path.relative_to(ROOT).as_posix()]=sha(path)
    summary=read(run/'summary.json'); track(run/'summary.json')
    check(summary==read(run/'batch-status.json') and summary['status']=='completed','complete')
    check(summary['terminal_counts']['completed']==sum(summary['terminal_counts'].values())==48,'48 terminal')
    check(summary['completion_requests']==56 and summary['warmup_completions']==8 and summary['accounting']['events']==112,'counts')
    check(summary['server_exit_codes']==[0]*4 and summary['final_test_generated_or_read'] is False,'cleanup and isolation')
    cfg=read(ROOT/'configs/p1-model-adaptation-v6.json')
    source_path=ROOT/'results/raw/development/20261025-1645-p1-explicit-instructions-v5/evaluation-plan.jsonl'
    check(sha(source_path)==cfg['source_evaluation_plan_sha256']=='98b1373f43185131fbacc8e375b50e4ca3a094aa93c6238d3c4aeea45b631c48','source plan SHA')
    sources={r['req_id']:r for r in rows(source_path) if r['arm']=='C'}
    check(len(sources)==24,'all24 original clear tasks')
    plan,refs=rows(run/'plan.jsonl'),rows(run/'evaluation-plan.jsonl')
    check(len(plan)==len(refs)==len({r['req_id'] for r in refs})==48,'unique48')
    check([r['req_id'] for r in plan]==[r['req_id'] for r in refs],'root sequence')
    check(Counter(r['source_req_id'] for r in refs)=={i:2 for i in sources},'no filtered or new task')
    assessed={r['req_id']:r for r in read(analysis/'summary.json')['request_results']}
    scores=[]; full=read(analysis/'fulltexts.json'); full_by_id={r['req_id']:r for r in full}
    total_cap=0; max_lag=0; measured_paths=[]
    for bid,arm,kinds in ((1,'A',{0,3}),(2,'B',{0,3}),(3,'B',{1,2}),(4,'A',{1,2})):
        block=run/'blocks'/f'block{bid}'; bsummary=read(block/'summary.json')
        check(bsummary['status']=='completed' and bsummary['server_exit_code']==0,'block cleanup')
        check(bsummary['accounting']['events']==28,'block CAP count'); total_cap+=28
        blockrefs=rows(block/'evaluation-plan.jsonl'); blockplan=rows(block/'plan.jsonl')
        check(blockrefs==[r for r in refs if r['block_id']==bid] and blockplan==[r for r in plan if r['block_id']==bid],'block/root bytes meaning')
        check(len(blockrefs)==12 and all(r['arm']==arm and r['kind_instance'] in kinds for r in blockrefs),'ABBA group')
        check(sum(len(r['target_ids']) for r in blockrefs)==44,'block field denominator')
        check(Counter((r['category'],r['task_kind']) for r in blockrefs)=={(c,k):2 for c in ('interactive','background') for k in ('extract','check','organize')},'block kind balance')
        check(Counter((r['category'],r['input_token_bucket']) for r in blockrefs)=={(c,b):2 for c in ('interactive','background') for b in range(3)},'block input balance')
        check(Counter(v for r in blockrefs if r['task_kind']=='check' for _,v in old.independent_answer(r))=={'一致':7,'不一致':7},'block truth balance')
        environment=read(block/'environment.json')
        check(environment['model_sha256']==cfg['models'][arm]['sha256'] and environment['engine_commit']==old.ENGINE_COMMIT,'runtime identity')
        check(read(block/'model-sha256-after.json')['sha256']==environment['model_sha256'],'model unchanged')
        check(read(block/'engine-runtime-sha256-after.json')==read(block/'engine-runtime-sha256.json'),'binary unchanged')
        for name in ('client-gate','structured-client-gate'):
            gate=read(block/name/'summary.json'); check(gate['status']=='passed','client gate')
            for relative,digest in gate['source_sha256'].items(): check(sha(ROOT/relative)==digest,'gate source '+relative)
        mapping=read(block/'source-sha256.json'); check(len(mapping)==77,'77 frozen sources')
        check(mapping==read(run/'source-sha256.json'),'same77 allblocks')
        for relative,digest in mapping.items():
            check(sha(ROOT/relative)==digest and sha(block/'source'/relative)==digest,'source byte snapshot '+relative)
        command=read(block/'service/server-command.json')
        check(command[command.index('-lv')+1]=='4' and command[command.index('-ngl')+1]=='99','common flags')
        for flag,value in (('-c','4096'),('-np','2'),('-b','2048'),('-ub','512')): check(command[command.index(flag)+1]==value,'fixed '+flag)
        check(read(block/'service/server-policy-environment.json')=={'LLAMA_SERVER_SIMPLE_SCHEDULER':'0','LLAMA_SERVER_BACKGROUND_AGING_MS':'5000','LLAMA_SERVER_MAX_RUNNING_BACKGROUND':'0'},'prototype scheduler')
        log=(block/'service/server.log').read_text(encoding='utf-8',errors='replace'); layers=29 if arm=='A' else 37
        check(re.search(r'offloaded '+str(layers)+'/'+str(layers)+r' layers to GPU',log) is not None,'actual full offload')
        props=read(block/'service/props.json'); check(props['total_slots']==2 and props['default_generation_settings']['n_ctx']==2048,'actual slot context')
        tokenized=rows(block/'tokenization.jsonl'); check(len(tokenized)==13,'12 actual prompts plus1warm tokenizer')
        tokens={r['request']['content']:json.loads(r['response_body'])['tokens'] for r in tokenized}
        events=[]
        for line in log.splitlines():
            if 'SCHED_CAP_LAUNCH' in line or 'SCHED_CAP_RELEASE' in line:
                events.append(dict(item.split('=',1) for item in line.split() if '=' in item))
        check(len(events)==28,'native CAP rows')
        for warm in rows(block/'warmup/client.jsonl'):
            check(warm['terminal']=='completed' and warm['cache_prompt'] is False and warm['cached_prompt_tokens']==0,'warm complete/cache0')
        actualpaths=sorted((block/'cases').glob('q*/replay/client.jsonl')); measured_paths+=actualpaths
        check(len(actualpaths)==12,'all12 rawcases')
        refmap={r['req_id']:r for r in blockrefs}; planmap={r['req_id']:r for r in blockplan}
        for path in actualpaths:
            track(path); rr=rows(path); check(len(rr)==1,'one raw record'); row=rr[0]; ref=refmap[row['req_id']]; execution=planmap[row['req_id']]
            check(all(row[k]==v for k,v in execution.items()),'raw/client plan equality')
            check(not old.FORBIDDEN & row.keys() and not old.FORBIDDEN & execution.keys(),'oracle isolation')
            check(ref['instruction_arm']=='C' and ref['arm']==arm,'model/instruction separation')
            src=sources[ref['source_req_id']]
            for key in ('prompt','target_ids','expected_lines','max_tokens','task_kind','category','kind_instance','input_token_bucket'):
                check(ref[key]==src[key],'fixed source '+key)
            check(ref['condition_order']==('AB' if bid<=2 else 'BA') and ref['condition_position']==(1 if bid in (1,3) else 2),'pair order')
            ins=dict(ref,arm='C'); check(old.independent_prompt_matches(ins),'independent full prompt')
            expected=old.independent_answer(ref)
            check(['|'.join(v) for v in expected]==ref['expected_lines'],'independent source answer')
            ids=tokens[row['prompt']]
            check(text_sha(json.dumps(ids,separators=(',',':')))==row['token_ids_sha256'] and len(ids)==row['input_tokens'],'actual model token IDs')
            check(ids.count(151644)==3 and ids.count(151645)==2 and 151643 not in ids,'special tokens no duplicate BOS')
            low,high=cfg['input_token_buckets'][row['category']][row['input_token_bucket']]
            check(low<=len(ids)<=high and len(ids)+row['max_tokens']+1<=2048,'actual bucket/context')
            check(row['terminal']=='completed' and row['http_status']==200 and row['error'] is None,'complete no error')
            settings=row['native_final']['generation_settings']
            check(row['cache_prompt'] is False and row['cached_prompt_tokens']==0 and row['new_prompt_tokens']==len(ids)==row['native_final']['tokens_evaluated'],'actual cache/input')
            check(row['output_tokens_known'] is True and row['output_tokens']==row['timings']['predicted_n']==row['native_final']['tokens_predicted'] and 0<row['output_tokens']<=row['max_tokens'],'actual output')
            check(settings['temperature']==0 and settings['ignore_eos'] is False and settings['n_predict']==row['max_tokens'],'actual sampling')
            check(settings['grammar']==old.independent_grammar(row['prompt'],row['category']) and settings['grammar_lazy'] is False,'actual grammar')
            check(row['native_final']['truncated'] is False,'no truncation')
            max_lag=max(max_lag,row['send_lag_ms']); check(row['send_lag_ms']<=50,'send lag')
            own=[e for e in events if e['clientid']==row['req_id']]
            check(len(own)==2 and own[0]['id']==own[1]['id'] and own[0]['slot']==own[1]['slot'],'CAP pair')
            field=old.independent_fields(row['content'],expected,row['task_kind']); result=assessed[row['req_id']]
            for key,ind in (('business_correct','business_correct'),('schema_valid','syntax_valid'),('target_fields_correct','correct_fields'),('target_fields_expected','planned_fields')): check(result[key]==field[ind],'independent score '+key)
            check(full_by_id[row['req_id']]['content']==row['content'] and full_by_id[row['req_id']]['prompt']==row['prompt'],'fulltext derivation')
            check((path.parent.parent/'output.txt').read_text(encoding='utf-8')==row['content'],'text byte content')
            scores.append(dict(arm=arm,pair_id=ref['pair_id'],**field))
    result=read(analysis/'summary.json')
    counts=Counter()
    for pid in dict.fromkeys(r['pair_id'] for r in refs):
        pair={r['arm']:r['business_correct'] for r in scores if r['pair_id']==pid}; a,b=pair['A'],pair['B']
        key='both_correct' if a and b else 'both_wrong' if not a and not b else 'A_wrong_B_correct' if b else 'A_correct_B_wrong'
        counts[key]+=1
    check(all(result['pair_counts'][k]==counts[k] for k in result['pair_counts']),'independent fourcells')
    for arm in ('A','B'):
        group=[r for r in scores if r['arm']==arm]; check(len(group)==24,'24 scored perarm')
        check(sum(r['planned_fields'] for r in group)==88,'88 denominator')
        check(result['by_arm'][arm]['business_correct']==sum(r['business_correct'] for r in group),'arm business')
        check(result['by_arm'][arm]['target_fields_correct']==sum(r['correct_fields'] for r in group),'arm fields')
    return {'status':'passed','checks':n,'measured_records':len(measured_paths),'completion_requests':56,'cap_events':total_cap,'max_send_lag_ms':max_lag,'pair_counts':dict(counts),'evidence_sha256':evidence,'G1_passed':False}

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--run-dir',type=Path,required=True); p.add_argument('--analysis-dir',type=Path,required=True); p.add_argument('--out-dir',type=Path,required=True); a=p.parse_args()
    result=audit(a.run_dir,a.analysis_dir); a.out_dir.mkdir(parents=True,exist_ok=False)
    (a.out_dir/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='evidence_sha256'},ensure_ascii=False))
