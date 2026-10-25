#!/usr/bin/env python3
"""Synthetic-system profiling, frozen calibration and nine-round paired summaries."""
import argparse
from collections import Counter
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import re
import statistics
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/replay'))
import p1_profile_estimator_v7 as estimator
spec=importlib.util.spec_from_file_location('frozen97',ROOT/'scripts/analysis/97_analyze_p1_explicit_instructions_v5.py')
old=importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
read,rows,sha,dump=old.read,old.rows,old.sha,old.dump

def percentile(values,p):
    if not values: return None
    ordered=sorted(values); position=(len(ordered)-1)*p/100; low=int(position)
    return ordered[low]+(ordered[min(low+1,len(ordered)-1)]-ordered[low])*(position-low)

def dist(values):
    values=[v for v in values if v is not None]
    return {'n':len(values),'min':min(values) if values else None,'p50':percentile(values,50),'p95':percentile(values,95),'max':max(values) if values else None,'mean':statistics.mean(values) if values else None}

def quality(ref,row):
    old.prompt_facts_match(ref)
    expected=['|'.join(x) for x in old.facts.derive_expected(ref)]
    assert ref['expected_lines']==expected and ref['expected_answer']=='\n'.join(expected)
    result=old.assess(ref,row.get('content') or '')
    result['completed_and_correct']=row['terminal']=='completed' and result['business_correct']
    return result

def check_row(ref,row):
    assert row['prompt']==ref['prompt'] and row['input_tokens']==ref['input_tokens']
    assert not old.facts.FORBIDDEN_IN_REPLAY & row.keys()
    if row['terminal']=='completed':
        final=row['native_final']; settings=final['generation_settings']
        assert row['cache_prompt'] is False and row['cached_prompt_tokens']==0
        assert row['new_prompt_tokens']==final['tokens_evaluated']==row['input_tokens']
        assert row['output_tokens_known'] is True and row['output_tokens']==row['timings']['predicted_n']==final['tokens_predicted']
        assert final['truncated'] is False and 0<row['output_tokens']<=row['max_tokens']
        assert settings['grammar']==row['grammar']==old.expected_grammar(ref)
        assert settings['temperature']==0 and settings['ignore_eos'] is False and settings['grammar_lazy'] is False

def profile_stats(group):
    return {'requests':len(group),'input_tokens':dist([r['input_tokens'] for r in group]),'output_tokens':dist([r['output_tokens'] for r in group]),
      'completion_ms':dist([r['completion_from_planned_ms'] for r in group]),'ttft_ms':dist([r['ttft_ms'] for r in group]),
      'native_prefill_ms':dist([(r.get('timings') or {}).get('prompt_ms') for r in group]),'native_decode_ms':dist([(r.get('timings') or {}).get('predicted_ms') for r in group]),
      'completed_correct':sum(r['quality']['completed_and_correct'] for r in group),'correct_fields':sum(r['quality']['target_fields_correct'] for r in group),'planned_fields':sum(r['quality']['target_fields_expected'] for r in group),
      'stop_types':dict(Counter(r['native_final']['stop_type'] for r in group))}

def metrics_peaks(path):
    samples=rows(path); collected={'processing':[],'deferred':[]}
    for sample in samples:
        for line in sample.get('response_body','').splitlines():
            match=re.fullmatch(r'llamacpp:requests_(processing|deferred)\s+([0-9.eE+\-]+)',line)
            if match: collected[match[1]].append(float(match[2]))
    return {'samples':len(samples),'errors':sum('error' in s for s in samples),'processing_peak':max(collected['processing'],default=None),'deferred_peak':max(collected['deferred'],default=None),'deferred_positive_samples':sum(v>0 for v in collected['deferred'])}

def analyze(run,out):
    run=run.resolve(strict=True); out=out.resolve(); assert not out.exists()
    status=read(run/'summary.json'); cfg=read(run/'environment.json')['config']
    assert status['status']=='completed' and status['measured_requests']==312 and status['completion_requests']==332 and status['load_rounds']==9
    assert status['all_services_exit_zero'] is True and status['final_test_generated_or_read'] is False
    refs=rows(run/'evaluation-plan.jsonl'); reference={r['req_id']:r for r in refs}; assert len(reference)==96
    evidence={n:sha(run/n) for n in ('summary.json','evaluation-plan.jsonl','environment.json','source-sha256.json','calibration-model.json')}
    profiles=[]; fulltexts=[]
    for path in sorted((run/'profile/cases').glob('q*/replay/client.jsonl')):
        one=rows(path); assert len(one)==1
        row=one[0]; ref=reference[row['req_id']]; check_row(ref,row)
        assert row['terminal']=='completed'
        result=dict(row,quality=quality(ref,row)); profiles.append(result)
        fulltexts.append({'phase':'profile','round':None,'req_id':row['req_id'],'reference':ref,'content':row['content'],'terminal':row['terminal'],'quality':result['quality']})
        evidence[path.relative_to(run).as_posix()]=sha(path)
    assert len(profiles)==96 and Counter(r['split'] for r in profiles)=={'calibration':24,'development':72}
    selected=read(run/'calibration-model.json')
    assert selected==estimator.select([r for r in profiles if r['split']=='calibration'])
    predicted=rows(run/'profile-predictions.jsonl'); by_pred={r['req_id']:r for r in predicted}
    assert len(by_pred)==96
    errors=[]
    for row in profiles:
        expectation=estimator.apply(row,selected); got=by_pred[row['req_id']]
        assert all(got[k]==v for k,v in expectation.items())
        if row['split']=='development': errors.append(dict(got,error=row['output_tokens']-got['prediction']))
    cal=[r['output_tokens'] for r in profiles if r['split']=='calibration']
    tau=percentile(cal,85)
    prediction={'requests':72,'candidate':selected['candidate'],'calibration_cv_mae':selected['cross_validation_mae'],'margin':selected['margin'],
      'development_mae':statistics.mean(abs(r['error']) for r in errors),'development_bias_actual_minus_prediction':statistics.mean(r['error'] for r in errors),
      'upper_coverage':sum(r['output_tokens']<=r['upper'] for r in errors),'point_at_cap':sum(r['prediction']>=r['max_tokens'] for r in errors),'upper_at_cap':sum(r['upper']>=r['max_tokens'] for r in errors),
      'calibration_output_p85':tau,'p85_short':sum(r['prediction']<=tau for r in errors),'p85_long':sum(r['prediction']>tau for r in errors),
      'old64_short':sum(r['prediction']<=64 for r in errors),'old64_long':sum(r['prediction']>64 for r in errors),'values':selected['values'],'rows':errors}
    rounds=[]
    for spec in cfg['execution_order']:
        name=f"r{spec['repeat']}-{spec['policy']}"; directory=run/'rounds'/name
        plan=rows(directory/'plan.jsonl'); actual=rows(directory/'replay/client.jsonl'); summary=read(directory/'replay/summary.json')
        assert len(plan)==len(actual)==24 and {r['req_id'] for r in plan}=={r['req_id'] for r in actual}
        result=[]
        for row in actual:
            ref=reference[row['req_id']]; check_row(ref,row); q=quality(ref,row); result.append(dict(row,quality=q))
            fulltexts.append({'phase':'load','round':name,'req_id':row['req_id'],'reference':ref,'content':row['content'],'terminal':row['terminal'],'quality':q})
        correct=sum(r['quality']['completed_and_correct'] for r in result); metrics=metrics_peaks(directory/'metrics-samples.jsonl')
        byclass={}
        for category in ('interactive','background'):
            c=[r for r in result if r['category']==category]
            byclass[category]={'planned':len(c),'completed':sum(r['terminal']=='completed' for r in c),'first_content_observed':sum(r['first_content_offset_ms'] is not None for r in c),
              'total_first_p95_ms':summary['classes'][category]['total_first_p95_ms'],'completion_p95_ms':summary['classes'][category]['completion_from_planned_p95_ms'],'completion_max_ms':summary['classes'][category]['completion_from_planned_max_ms'],
              'correct':sum(r['quality']['completed_and_correct'] for r in c)}
        rounds.append({'round':name,**spec,'planned':24,'terminal_counts':summary['terminal_counts'],'classes':byclass,'all_plan_correct':correct,'correct_fields':sum(r['quality']['target_fields_correct'] for r in result),'planned_fields':sum(r['quality']['target_fields_expected'] for r in result),
          'last_end_seconds':summary['last_end_seconds'],'drain_seconds':summary['drain_elapsed_seconds'],'completed_in_window':round(summary['window_completion_throughput_rps']*12),
          'planned_still_inflight_at_12s':sum(r['arrival_offset_ms']<12000<=r['end_offset_ms'] for r in result),
          'output_tokens':summary['known_completed_output_tokens'],'output_tps':summary['output_tps_whole_run_including_drain'],'correct_tasks_per_second':correct/summary['last_end_seconds'],
          'peak_queue_metrics':metrics,'quality_by_request':{r['req_id']:r['quality']['completed_and_correct'] for r in result}})
        evidence[(directory/'replay/client.jsonl').relative_to(run).as_posix()]=sha(directory/'replay/client.jsonl')
        evidence[(directory/'metrics-samples.jsonl').relative_to(run).as_posix()]=sha(directory/'metrics-samples.jsonl')
    pairs=[]
    for repeat in range(1,4):
        group={r['policy']:r for r in rounds if r['repeat']==repeat}; original=group['original']
        for policy in ('simple','capped'):
            current=group[policy]; a=original['classes']['interactive']['total_first_p95_ms']; b=current['classes']['interactive']['total_first_p95_ms']
            bg0=original['classes']['background']['completion_p95_ms']; bg1=current['classes']['background']['completion_p95_ms']
            pairs.append({'repeat':repeat,'policy':policy,'interactive_p95_reduction_ms':a-b if a is not None and b is not None else None,'interactive_p95_reduction_fraction':1-b/a if a and b is not None else None,
              'background_completion_p95_increase_fraction':bg1/bg0-1 if bg0 and bg1 is not None else None,
              'output_rate_loss_fraction':1-current['output_tps']/original['output_tps'] if original['output_tps'] and current['output_tps'] is not None else None,
              'correct_tasks_change':current['all_plan_correct']-original['all_plan_correct'],'original_correct':original['all_plan_correct'],'policy_correct':current['all_plan_correct']})
    result={'status':'completed_synthetic_system_development_evaluation','run_id':run.name,'analyzed_at':datetime.now().astimezone().isoformat(),'analysis_script_sha256':sha(Path(__file__)),
      'counts':{'profile':96,'calibration':24,'development_profile':72,'load':216,'rounds':9,'measured':312,'warmup':20},
      'profile_by_split_class':{split:{c:profile_stats([r for r in profiles if r['split']==split and r['category']==c]) for c in ('interactive','background')} for split in ('calibration','development')},
      'profile_by_kind':{c:{k:profile_stats([r for r in profiles if r['category']==c and r['task_kind']==k]) for k in ('extract','check','organize')} for c in ('interactive','background')},
      'prediction':prediction,'rounds':rounds,'paired_differences':pairs,'by_policy':{p:{'interactive_p95_ms':dist([r['classes']['interactive']['total_first_p95_ms'] for r in rounds if r['policy']==p]),'background_p95_ms':dist([r['classes']['background']['completion_p95_ms'] for r in rounds if r['policy']==p]),'output_tps':dist([r['output_tps'] for r in rounds if r['policy']==p]),'correct_tasks':dist([r['all_plan_correct'] for r in rounds if r['policy']==p])} for p in ('original','simple','capped')},
      'G1_passed':False,'final_test_accessed':False,'limits':['Synthetic field tasks; no application necessity or model-quality pass.','Calibration24, new-content development72 share public C templates; no unseen-template claim.','Three fixed finite-burst paired repetitions; no sustained capacity, significance, equivalence or population reliability claim.','Policies can alter outputs/quality through batching; no filtering or forced length matching.','No formal hard KV ledger, NN, B3 or M implementation/comparison.']}
    out.mkdir(parents=True,exist_ok=False); dump(out/'summary.json',result); dump(out/'fulltexts.json',fulltexts); dump(out/'evidence-sha256.json',evidence)
    (out/'analysis-source.py').write_bytes(Path(__file__).read_bytes())
    lines=['# 3B合成系统负载v7逐轮结果','','3个开发种子，每个种子3种策略独立服务。技术完成与事实正确分开，3次重复只作探索。','','|轮次|聊天计划至首内容P95(ms)|后台完成P95(ms)|全程输出token/s|正确完成/全部24|待执行请求采样峰值|','|---|---:|---:|---:|---:|---:|']
    for r in rounds: lines.append(f"|{r['round']}|{r['classes']['interactive']['total_first_p95_ms']}|{r['classes']['background']['completion_p95_ms']}|{r['output_tps']}|{r['all_plan_correct']}/24|{r['peak_queue_metrics']['deferred_peak']}|")
    (out/'metrics.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    dump(out/'report-sha256.json',{p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file()})
    return {'status':result['status'],'profile':result['profile_by_split_class'],'prediction':{k:v for k,v in prediction.items() if k!='rows'},'by_policy':result['by_policy'],'paired':pairs}

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--run-dir',type=Path,required=True); p.add_argument('--out-dir',type=Path,required=True); a=p.parse_args(); print(json.dumps(analyze(a.run_dir,a.out_dir),ensure_ascii=False))
