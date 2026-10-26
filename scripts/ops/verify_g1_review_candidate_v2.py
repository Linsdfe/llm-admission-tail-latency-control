"""Verify review inputs without granting G1 or accessing final requests."""
import argparse,hashlib,json,math,re,subprocess
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def read(p): return json.loads(p.read_text(encoding='utf-8'))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def verify(out,index=False):
 previous=read(ROOT/'results/raw/maintenance/20261026-p1-target-mix-delivery-v8-r2/verification-index.json'); expected=dict(previous['files'])
 for name,h in previous['raw_files'].items(): assert sha(ROOT/'results/raw/development/20261026-0845-p1-target-mix-v8-r2'/name)==h,'v8 raw changed '+name
 refs=[]
 for rid,supervisor in [('20261026-0930-p1-format-calibration-v9-r2','20261026-p1-format-calibration-supervisor-v9-r2'),('20261026-1035-p1-grammar-upper-v10-r2','20261026-p1-grammar-upper-supervisor-v10-r2')]:
  run=ROOT/'results/raw/development'/rid; report=ROOT/'docs/experiments'/rid
  assert read(run/'summary.json')['status']=='completed' and read(report/'native-audit.json')['status']=='passed'
  assert read(ROOT/'results/raw/maintenance'/supervisor/'exit.json')['exit_code']==0
  expected.update(read(run/'source-sha256.json'))
  for p in report.iterdir():
   if p.is_file(): expected[p.relative_to(ROOT).as_posix()]=sha(p)
  for manifest in ['report-sha256.json','supplement-sha256.json']:
   for name,h in read(report/manifest).items(): assert sha(report/name)==h,'report changed '+name
  refs += [json.loads(x) for x in (run/'evaluation-plan.jsonl').read_text(encoding='utf-8').splitlines()]
 overlap=read(ROOT/'docs/experiments/20261026-1035-p1-grammar-upper-v10-r2/overlap-audit.json')
 assert len(refs)==144 and len({r['content_sha256'] for r in refs})==142
 assert len(overlap['overlap_with_v9'])==2 and overlap['previously_unseen']==overlap['unseen_upper_coverage']==46
 assert all(r['split']!='final' for r in refs)
 cfg=read(ROOT/'configs/p1-g1-review-candidate-v2.json'); plan=read(ROOT/'docs/p1/g1-plan-model-free-check-v2.json')
 assert cfg['status']=='candidate_for_G1_review_not_approved' and not cfg['formal_data']['final_generation_enabled'] and not cfg['formal_data']['final_accessed']
 groups=[set(cfg['formal_data'][n]) for n in ['calibration_seeds','development_seeds','final_main_seeds','final_boundary_seeds']]
 assert all(not groups[i]&groups[j] for i in range(4) for j in range(i+1,4)); assert not {r['seed'] for r in refs}&(groups[2]|groups[3])
 assert not [p for p in (ROOT/'data/test').rglob('*') if p.is_file() and p.name!='.gitkeep']
 assert cfg['formal']['blocks']==20 and plan['count']==sum(len(x['execution_order']) for x in plan['reserved_schedule'])==320
 coverage=1-2*sum(math.comb(20,k) for k in range(4))/2**20; assert coverage==plan['interval_coverage'] and coverage>=.995
 assert 1-10*(1-coverage)==plan['joint_coverage_lower_bound']
 for row in plan['reserved_schedule']:
  policies=cfg['formal']['main_policies' if row['scene']=='main' else 'boundary_policies']; assert len(row['execution_order'])==len(set(row['execution_order'])) and set(row['execution_order'])==set(policies)
 budget=read(ROOT/'docs/p1/g1-resource-budget-v2.json'); assert budget['recommended_disk_reservation_GiB']*1024**3>=budget['raw_plus_zip_and_metadata_budget_bytes']
 for failed,sup in [('20261026-0920-p1-format-calibration-v9','20261026-p1-format-calibration-supervisor-v9'),('20261026-1025-p1-grammar-upper-v10','20261026-p1-grammar-upper-supervisor-v10')]:
  run=ROOT/'results/raw/development'/failed; assert read(ROOT/'results/raw/maintenance'/sup/'exit.json')['exit_code']!=0
  assert not list((run/'cases').glob('*/replay/client.jsonl'))
  for name,h in read(run/'source-sha256.json').items(): assert sha(run/'source'/name)==h,'failed snapshot lost '+name
 extra=['scripts/ops/check_corpus_overlap.py','docs/p1/g1-history-corpus-registry-v2.json','docs/p1/g1-overlap-gate-check-v2.json','configs/p1-g1-review-candidate-v2.json','docs/p1/g1-review-candidate-v2.md','docs/p1/g1-plan-model-free-check-v2.json','docs/p1/g1-resource-budget-v2.json','docs/reports/20261026/G1评审材料_白话版.md','tests/integration/audit_p1_g1_native_records_v2.py','scripts/ops/verify_g1_review_candidate_v2.py','docs/p1/grammar-upper-protocol-v10.md']
 expected.update({p:sha(ROOT/p) for p in extra})
 for name,h in expected.items():
  assert sha(ROOT/name)==h,'protected file changed '+name
  if index: assert hashlib.sha256(subprocess.check_output(['git','show',':'+name],cwd=ROOT)).hexdigest()==h,'index bytes changed '+name
 documents=[ROOT/p for p in extra if p.endswith('.md')]+[ROOT/'README.md',ROOT/'docs/p1/README.md',ROOT/'docs/p1/g1-review-checklist-v1.md',ROOT/'docs/implementation-plan.md',ROOT/'results/README.md']+list((ROOT/'daily/20261026').glob('*.md')); links=0
 for path in documents:
  for target in re.findall(r'\]\(([^\n)]+)\)',path.read_text(encoding='utf-8')):
   target=target.strip('<>')
   if '://' in target or target.startswith('#'): continue
   assert (path.parent/target.split('#',1)[0]).resolve().exists(),(path,target)
   links+=1
 result={'status':'ready_for_human_G1_review','checked_at':datetime.now().astimezone().isoformat(),'index_bytes_checked':index,'protected_source_report_files':len(expected),'v8_raw_files_unchanged':len(previous['raw_files']),'new_measured':144,'new_warmups':4,'native_cap_events':296,'failed_preparations_preserved':2,'local_links':links,'final_test_accessed':False,'G1_passed':False,'files':expected}
 with out.open('x',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2)
 return {k:v for k,v in result.items() if k!='files'}
if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--out',type=Path,required=True); p.add_argument('--index',action='store_true'); a=p.parse_args(); print(json.dumps(verify(a.out,a.index),ensure_ascii=False))
