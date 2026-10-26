"""Check versioned G1 design improvements, without granting the decision gate."""
import argparse,hashlib,json,re,statistics,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def read(p): return json.loads(p.read_text(encoding='utf-8'))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def verify(out,index=False):
 previous=read(ROOT/'docs/p1/g1-freeze-candidate-v2.json'); expected=dict(previous['files'])
 for name,h in expected.items(): assert sha(ROOT/name)==h,'v2 protected bytes changed '+name
 raw=read(ROOT/'results/raw/maintenance/20261026-p1-g1-delivery-v2/new-raw-sha256.json')
 for name,h in raw.items(): assert sha(ROOT/name)==h,'native raw changed '+name
 a=read(ROOT/'docs/experiments/20261026-p1-margin-mechanism-audit-v1/summary.json'); decisions=read(ROOT/'docs/experiments/20261026-p1-margin-mechanism-audit-v1/diagnostic-decisions.json')
 assert a['diagnostic_decisions']==len(decisions)==1840 and a['independent_new_measurements']==0
 assert a['source_dev_actual_within_B3_zero_shared_floor']==48 and a['numeric_source_positive_residuals']==0
 extra=[r for r in decisions if r['M_length']>r['B3_zero_length']]; assert len(extra)==544 and all(r['fallback'] and r['M_margin']==1 for r in extra)
 assert read(ROOT/'docs/experiments/20261026-p1-margin-mechanism-audit-v1/independent-audit.json')['status']=='passed'
 for name,h in read(ROOT/'docs/experiments/20261026-p1-margin-mechanism-audit-v1/evidence-sha256.json').items(): assert sha(ROOT/name)==h,'diagnostic input changed '+name
 b=read(ROOT/'docs/experiments/20261026-p1-budget-reference-audit-v1/summary.json')
 old=[r for r in b['rows'] if r['reference_rule']=='v2_target_weighted_p75']; new=[r for r in b['rows'] if r['reference_rule']=='v3_max_class_p75']; assert all(r['background']['own_cost_exceeds_budget']==24 for r in old)
 assert next(r for r in new if r['multiplier']==1)['background']['own_cost_exceeds_budget']==10
 # Independent cost-coefficient extraction directly from native calibration records.
 pre=[]; decode=[]
 run=ROOT/'results/raw/development/20261026-0930-p1-format-calibration-v9-r2'
 for i in range(1,49):
  row=json.loads((run/'cases'/f'q{i:03d}'/'replay/client.jsonl').read_text(encoding='utf-8').splitlines()[0]); pre.append(row['timings']['prompt_ms']/row['timings']['prompt_n']); decode.append(row['timings']['predicted_ms']/row['output_tokens'])
 assert statistics.median(pre)==a['shared_cost_proxy']['input_ms_per_token'] and statistics.median(decode)==a['shared_cost_proxy']['output_ms_per_token']
 config=read(ROOT/'configs/p1-g1-review-candidate-v3.json'); assert not config['formal_data']['final_generation_enabled'] and not config['review_recommendation']['G1_passed']
 assert config['formal']['blocks']==20 and config['formal']['native_runs_before_alias_merge']==320 and config['search']['max_native_development_runs']==96
 assert config['mechanism_contract']['buckets']=='category x task_kind, six buckets'
 assert not [p for p in (ROOT/'data/test').rglob('*') if p.is_file() and p.name!='.gitkeep']
 for folder in ['20261026-p1-margin-mechanism-audit-v1','20261026-p1-budget-reference-audit-v1']:
  path=ROOT/'docs/experiments'/folder
  for name,h in read(path/'report-sha256.json').items(): assert sha(path/name)==h
  for p in path.iterdir():
   if p.is_file(): expected[p.relative_to(ROOT).as_posix()]=sha(p)
 new_names=['docs/p1/margin-mechanism-audit-protocol-v1.md','docs/p1/negative-result-delivery-plan-v1.md','docs/p1/g1-review-candidate-v3.md','configs/p1-g1-review-candidate-v3.json','docs/reports/20261026/G1补强与复核建议_v3.md','scripts/analysis/103_audit_p1_margin_decision_space.py','scripts/analysis/104_compare_p1_budget_reference.py','tests/unit/test_p1_margin_decision_space.py','tests/integration/audit_p1_margin_decision_space.py','scripts/ops/verify_g1_improvement_v3.py']
 expected.update({name:sha(ROOT/name) for name in new_names})
 if index:
  for name,h in expected.items(): assert hashlib.sha256(subprocess.check_output(['git','show',':'+name],cwd=ROOT)).hexdigest()==h,'index changed '+name
 docs=[ROOT/name for name in new_names if name.endswith('.md')]+[ROOT/'README.md',ROOT/'docs/p1/README.md',ROOT/'docs/p1/g1-review-checklist-v1.md',ROOT/'docs/implementation-plan.md',ROOT/'results/README.md']+list((ROOT/'daily/20261026').glob('*.md')); links=0
 for path in docs:
  for target in re.findall(r'\]\(([^\n)]+)\)',path.read_text(encoding='utf-8')):
   target=target.strip('<>')
   if '://' in target or target.startswith('#'): continue
   assert (path.parent/target.split('#',1)[0]).resolve().exists(),(path,target)
   links+=1
 result={'status':'ready_for_user_G1_review_v3','index_bytes_checked':index,'protected_source_report_files':len(expected),'old_native_raw_files_unchanged':len(raw),'new_GPU_measurements':0,'final_test_accessed':False,'G1_passed':False,'assistant_subjective_readiness_score':90,'local_links':links,'files':expected}
 with out.open('x',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2)
 return {k:v for k,v in result.items() if k!='files'}
if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--out',type=Path,required=True); p.add_argument('--index',action='store_true'); a=p.parse_args(); print(json.dumps(verify(a.out,a.index),ensure_ascii=False))
