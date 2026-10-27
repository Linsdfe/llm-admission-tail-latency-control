"""Check cross-field counts and scope, without creating any final request."""
import argparse
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
def audit():
    c=json.loads((ROOT/'configs/p1-g1-review-candidate-v4.json').read_text(encoding='utf-8'))
    w=c['workload'];d=c['formal_data'];m=c['mechanism_contract'];f=c['formal'];s=c['search'];checks=0
    def check(x,label):
        nonlocal checks
        assert x,label
        checks+=1
    check(w['task_kinds']==['extract','organize'] and len(m['buckets'])==4,'two tasks/four buckets')
    per_seed=2*len(w['task_kinds'])*w['instances_per_class_task_seed'];check(per_seed==32==d['source_rows_per_seed'],'32sources per seed')
    check(len(d['calibration_seeds'])*per_seed==128==d['calibration_sources']==m['warm_total'],'128 genuine planned warm samples')
    check(len(d['calibration_seeds'])*8==32==m['warm_samples_per_bucket']==m['minimum_history'],'32 per bucket')
    check(len(d['development_seeds'])*per_seed==96==d['development_sources'],'96 development sources')
    check(len(d['final_main_seeds'])*per_seed==640==d['final_unique_sources'],'640 final sources')
    check(d['final_main_seeds']==d['final_boundary_seeds'] and set(d['retired_unused_seeds']).isdisjoint(d['final_main_seeds']),'shared boundary sources; no extra392xx')
    for a,b in [('calibration_seeds','development_seeds'),('calibration_seeds','final_main_seeds'),('development_seeds','final_main_seeds')]:check(set(d[a]).isdisjoint(d[b]),'split seeds disjoint')
    check(not d['final_generation_enabled'] and not d['final_accessed'],'no final execution authorization')
    for category,total in [('interactive',200),('background',30)]:
        counts=w['trace_count_allocation_per_task'][category]
        check(2*sum(counts.values())==total,'230 balanced trace allocation')
        check(set(map(int,counts))==set(w['target_counts'][category]),'same public target-count strata')
        check(len(w['target_counts'][category])==8,'eight per task/seed')
        check(w['input_ranges'][category][1]+w['max_tokens'][category]+w['safety_tokens']<=w['slot_context_tokens'],'worst context safe')
    check(w['input_bucket_quotas'] is None,'old padded buckets removed')
    check(sum(s['development_runs_by_step'].values())==96==s['max_native_development_runs'],'96 development runs')
    check((len(f['main_policies'])+len(f['boundary_policies']))*f['blocks']==320==f['native_runs_before_alias_merge'],'320 formal runs')
    check(s['B3_margin_tokens'][0]==0 and len(s['M_window'])*len(s['M_quantile'])==4,'strong zero margin/four M candidates')
    check(c['predictor_candidate']['fit'].startswith('none') and 'no public grammar floor' in c['predictor_candidate']['effective_length'],'public point/no stale clipping')
    check(c['environment']['context_per_slot']==2048 and c['measurement']['open_seconds']+c['measurement']['drain_seconds']==280,'capacity and deadline')
    check(not c['scope']['api_inference'] and not c['review_recommendation']['G1_passed'],'API off/user review pending')
    expected=128*1.2*60/3600+.25;check(round(expected,2)==c['resource_budget']['calibration_additional_hours'],'calibration budget arithmetic')
    check(round(72.14+expected,2)==c['resource_budget']['planned_machine_hours_upper'],'total budget arithmetic')
    check(not [p for p in (ROOT/'data/test').rglob('*') if p.is_file() and p.name!='.gitkeep'],'final test directory untouched')
    probe=json.loads((ROOT/'docs/experiments/20261027-p1-copy-scope-v16-v17/v17-audit.json').read_text(encoding='utf-8'))
    check(probe['task_acceptance_passed'] and probe['whole_correct']==64,'actual fresh pipeline accepted')
    return dict(status='passed_contract_consistency',checks=checks,formal_calibration_planned=128,formal_calibration_collected=0,
                residual_buckets=4,warm_per_bucket_planned=32,development_runs_upper=96,formal_runs_upper=320,
                final_unique_sources_planned=640,final_generated=False,G1_passed=False)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();r=audit()
    with a.out.open('x',encoding='utf-8') as f:json.dump(r,f,ensure_ascii=False,indent=2)
    print(json.dumps(r,ensure_ascii=False))
