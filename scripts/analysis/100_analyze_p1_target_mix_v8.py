#!/usr/bin/env python3
"""Target200/30 development: normalized source corpus and all occurrence outputs."""
import argparse
from collections import Counter,defaultdict
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import statistics
import sys
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/'scripts/replay'))
import p1_target_mix_v8 as target
spec=importlib.util.spec_from_file_location('frozen99',ROOT/'scripts/analysis/99_analyze_p1_system_workload_v7.py')
old=importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
read,rows,sha,dump=old.read,old.rows,old.sha,old.dump

def analyze(run,out):
    run=run.resolve(strict=True); out=out.resolve(); assert not out.exists()
    raw=read(run/'summary.json'); cfg=read(run/'environment.json')['config']
    assert raw['status']=='completed' and raw['measured_requests']==2094 and raw['completion_requests']==2114
    assert raw['all_services_exit_zero'] is True and raw['final_test_generated_or_read'] is False
    corpus=rows(run/'evaluation-plan.jsonl'); refs={r['req_id']:r for r in corpus}; assert len(refs)==72
    artifact=read(run/'classification-artifact.json'); assert artifact==target.reference_artifact(ROOT)
    assert read(run/'ordering-witnesses.json')==target.ordering_witnesses()
    predictions=rows(run/'corpus-predictions.jsonl'); pred={r['req_id']:r for r in predictions}
    assert len(pred)==72
    matrix={c:{'short':0,'long':0} for c in ('interactive','background')}; ordinary=Counter()
    for ref in corpus:
        result=target.classify(ref,artifact); assert all(pred[ref['req_id']][k]==v for k,v in result.items())
        matrix[ref['category']][result['predicted_class']]+=1
        ordinary['short' if result['prediction']<=artifact['ordinary_cutoff'] else 'long']+=1
    outputs=[]; profiles=[]; evidence={n:sha(run/n) for n in ('summary.json','environment.json','evaluation-plan.jsonl','corpus-plan.jsonl','classification-artifact.json','ordering-witnesses.json')}
    def assess(row,phase,round_name):
        ref=refs[row['source_req_id']]; old.check_row(ref,row); q=old.quality(ref,row)
        item={k:row.get(k) for k in ('req_id','source_req_id','seed','category','task_kind','input_token_bucket','input_tokens','output_tokens','terminal','content','arrival_offset_ms','actual_send_offset_ms','first_content_offset_ms','end_offset_ms','total_first_ms','completion_from_planned_ms','timings')}
        item.update(phase=phase,round=round_name,prediction=pred[ref['req_id']]['prediction'],predicted_class=pred[ref['req_id']]['predicted_class'],upper=pred[ref['req_id']]['upper'],quality=q)
        outputs.append(item); return item
    for path in sorted((run/'profile/cases').glob('q*/replay/client.jsonl')):
        one=rows(path); assert len(one)==1
        item=assess(one[0],'profile',None); profiles.append(dict(one[0],quality=item['quality']))
        evidence[path.relative_to(run).as_posix()]=sha(path)
    assert len(profiles)==24
    rounds=[]; originals=[]
    for spec in cfg['execution_order']:
        name=f"r{spec['repeat']}-{spec['policy']}"; directory=run/'rounds'/name
        plan=rows(directory/'plan.jsonl'); actual=rows(directory/'replay/client.jsonl'); summary=read(directory/'replay/summary.json')
        assert len(plan)==len(actual)==230
        by_id={r['req_id']:r for r in plan}
        assert set(by_id)=={r['req_id'] for r in actual}
        assessed=[]
        for row in actual:
            assert all(row[k]==v for k,v in by_id[row['req_id']].items())
            assessed.append(assess(row,'load',name))
        if spec['policy']=='original': originals+=assessed
        byclass={}
        for category in ('interactive','background'):
            group=[r for r in assessed if r['category']==category]
            byclass[category]={'planned':len(group),'completed':sum(r['terminal']=='completed' for r in group),'first_content_observed':sum(r['first_content_offset_ms'] is not None for r in group),
              'total_first_p95_ms':summary['classes'][category]['total_first_p95_ms'],'completion_p95_ms':summary['classes'][category]['completion_from_planned_p95_ms'],'completion_max_ms':summary['classes'][category]['completion_from_planned_max_ms'],
              'correct':sum(r['quality']['completed_and_correct'] for r in group)}
        correct=sum(r['quality']['completed_and_correct'] for r in assessed)
        rounds.append({'round':name,**spec,'planned':230,'terminal_counts':summary['terminal_counts'],'classes':byclass,'all_plan_correct':correct,
          'correct_fields':sum(r['quality']['target_fields_correct'] for r in assessed),'planned_fields':sum(r['quality']['target_fields_expected'] for r in assessed),
          'last_end_seconds':summary['last_end_seconds'],'drain_seconds':summary['drain_elapsed_seconds'],'completed_in_window':round(summary['window_completion_throughput_rps']*100),
          'planned_still_inflight_at_100s':sum(r['arrival_offset_ms']<100000<=r['end_offset_ms'] for r in assessed),
          'output_tokens':summary['known_completed_output_tokens'],'output_tps':summary['output_tps_whole_run_including_drain'],'correct_tasks_per_second':correct/summary['last_end_seconds'],
          'peak_queue_metrics':old.metrics_peaks(directory/'metrics-samples.jsonl')})
        for p in (directory/'replay/client.jsonl',directory/'metrics-samples.jsonl'): evidence[p.relative_to(run).as_posix()]=sha(p)
    assert len(outputs)==2094 and len(originals)==690
    pairs=[]
    for repeat in range(1,4):
        group={r['policy']:r for r in rounds if r['repeat']==repeat}; baseline=group['original']
        for policy in ('simple','capped'):
            current=group[policy]; i0=baseline['classes']['interactive']['total_first_p95_ms']; i1=current['classes']['interactive']['total_first_p95_ms']; b0=baseline['classes']['background']['completion_p95_ms']; b1=current['classes']['background']['completion_p95_ms']
            pairs.append({'repeat':repeat,'policy':policy,'interactive_p95_reduction_ms':i0-i1 if i0 is not None and i1 is not None else None,'interactive_p95_reduction_fraction':1-i1/i0 if i0 and i1 is not None else None,
              'background_completion_p95_increase_fraction':b1/b0-1 if b0 and b1 is not None else None,'output_rate_loss_fraction':1-current['output_tps']/baseline['output_tps'] if baseline['output_tps'] and current['output_tps'] is not None else None,
              'correct_tasks_change':current['all_plan_correct']-baseline['all_plan_correct']})
    known=[r for r in originals if r['output_tokens'] is not None]
    errors=[r['output_tokens']-r['prediction'] for r in known]
    unique_outputs=defaultdict(set); ever_wrong=set()
    for row in originals:
        unique_outputs[row['source_req_id']].add(row['content'])
        if not row['quality']['completed_and_correct']: ever_wrong.add(row['source_req_id'])
    result={'status':'completed_target_mix_development_evaluation','run_id':run.name,'analyzed_at':datetime.now().astimezone().isoformat(),'analysis_script_sha256':sha(Path(__file__)),
      'counts':{'unique_corpus':72,'profile':24,'load':2070,'measured':2094,'warmup':20,'per_round':230,'rounds':9,'per_policy':690},
      'classification':{'weighted_cutoff':artifact['weighted_cutoff'],'ordinary_cutoff':artifact['ordinary_cutoff'],'corpus_business_by_predicted_class':matrix,'ordinary_corpus_counts':dict(ordinary),
        'information_overlap':'All current weighted labels equal business class; this is not equivalence of whole policies.',
        'original_occurrences_known':len(known),'actual_output_class_errors':sum(('short' if r['output_tokens']<=artifact['weighted_cutoff'] else 'long')!=r['predicted_class'] for r in known),
        'output_mae_on_original_occurrences':statistics.mean(abs(e) for e in errors),'upper_coverage_on_original_occurrences':sum(r['output_tokens']<=r['upper'] for r in known),
        'point_at_cap':sum(r['prediction']>=refs[r['source_req_id']]['max_tokens'] for r in originals),'upper_at_cap':sum(r['upper']>=refs[r['source_req_id']]['max_tokens'] for r in originals),
        'unique_sources_ever_wrong_under_original':len(ever_wrong),'unique_sources_multiple_distinct_outputs_under_original':sum(len(v)>1 for v in unique_outputs.values())},
      'profile_by_class':{c:old.profile_stats([r for r in profiles if r['category']==c]) for c in ('interactive','background')},'ordering_witnesses':read(run/'ordering-witnesses.json'),
      'rounds':rounds,'paired_differences':pairs,'by_policy':{p:{'interactive_p95_ms':old.dist([r['classes']['interactive']['total_first_p95_ms'] for r in rounds if r['policy']==p]),'background_p95_ms':old.dist([r['classes']['background']['completion_p95_ms'] for r in rounds if r['policy']==p]),'output_tps':old.dist([r['output_tps'] for r in rounds if r['policy']==p]),'correct_total':sum(r['all_plan_correct'] for r in rounds if r['policy']==p)} for p in ('original','simple','capped')},
      'G1_passed':False,'final_test_accessed':False,'limits':['Three paired development seeds only; no formal joint, equivalence or value guarantee.','72 unique source tasks expanded with replacement; 690 per-policy occurrences are correlated.','New IDs from R10; fixed public instructions and grammar, no unseen-template generalization.','Cutoff and predictor read frozen v7 calibration only; all v8 outcomes held out from selection.','Model-free ordering witnesses are not native NN implementation or observed trigger frequency.','Fixed100s arrival plan throughput is not saturation capacity. No formal hard ledger or M/B3 comparison.']}
    out.mkdir(parents=True,exist_ok=False); dump(out/'summary.json',result); dump(out/'corpus.json',corpus); dump(out/'evidence-sha256.json',evidence)
    with (out/'request-results.jsonl').open('x',encoding='utf-8') as output:
        for row in outputs: output.write(json.dumps(row,ensure_ascii=False)+'\n')
    (out/'analysis-source.py').write_bytes(Path(__file__).read_bytes())
    lines=['# 目标200/30开发验证v8','','72份新资料按输入桶轮换扩展为每轮230请求；三种规则各3轮。重复出现请求不是独立内容样本。','','|轮次|聊天P95(ms)|后台完成P95(ms)|全程输出token/s|正确完成/计划230|待执行采样峰值|','|---|---:|---:|---:|---:|---:|']
    for r in rounds: lines.append(f"|{r['round']}|{r['classes']['interactive']['total_first_p95_ms']}|{r['classes']['background']['completion_p95_ms']}|{r['output_tps']}|{r['all_plan_correct']}/230|{r['peak_queue_metrics']['deferred_peak']}|")
    (out/'metrics.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    dump(out/'report-sha256.json',{p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file()})
    return {k:result[k] for k in ('status','classification','by_policy','paired_differences')}

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--run-dir',type=Path,required=True); p.add_argument('--out-dir',type=Path,required=True); a=p.parse_args(); print(json.dumps(analyze(a.run_dir,a.out_dir),ensure_ascii=False))
