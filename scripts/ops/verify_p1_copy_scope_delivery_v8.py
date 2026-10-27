"""Preserve prior evidence and verify the fresh probes and unified G1 candidate."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import zipfile

ROOT=Path(__file__).resolve().parents[2]
BASE='f0db50a2c248f202d6b99bb21f8bfcee778b688e'
ARCHIVE='results/archives/20261027/daily-20261027-093425-cb10b3b7.zip'
ARCHIVE_SHA='64739afc956ac216593f0df99459a95d238d76b3d312df458bf9291a25b76897'
FOLDER=ROOT/'docs/experiments/20261027-p1-copy-scope-v16-v17'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def verify(out,revision=None):
    previous='docs/p1/g1-content-factor-evidence-v7.json'
    raw=subprocess.check_output(['git','show',BASE+':'+previous],cwd=ROOT)
    assert (ROOT/previous).read_bytes()==raw
    old=read(ROOT/previous)['files'];old[previous]=hashlib.sha256(raw).hexdigest()
    for p,h in old.items():assert sha(ROOT/p)==h,('old protected',p)
    assert sha(ROOT/ARCHIVE)==ARCHIVE_SHA
    maps=['results/raw/maintenance/20261026-p1-g1-delivery-v2/new-raw-sha256.json',
          'results/raw/maintenance/20261026-p1-quality-delivery-v4/new-raw-sha256.json',
          'results/raw/maintenance/20261026-p1-quality-delivery-v5/new-raw-sha256.json',
          'results/raw/maintenance/20261027-p1-content-delivery-v6/new-raw-sha256.json',
          'results/raw/maintenance/20261027-p1-content-factor-delivery-v7/new-raw-sha256.json']
    old_raw=0
    with zipfile.ZipFile(ROOT/ARCHIVE) as z:
        assert (ROOT/maps[-1]).read_bytes()==z.read('20261027/project/'+maps[-1])
    for manifest in maps:
        for p,h in read(ROOT/manifest).items():assert sha(ROOT/p)==h,('old raw',p);old_raw+=1
    assert old_raw==11702
    for v,runid in [(16,'20261027-1030-p1-copy-profile-v16'),(17,'20261027-1040-p1-copy-profile-v17')]:
        run=ROOT/'results/raw/development'/runid
        for p,h in read(run/'source-sha256.json').items():assert sha(ROOT/p)==sha(run/'source'/p)==h,('execution source',p)
        a=read(FOLDER/f'v{v}-audit.json');assert a['total']==64 and a['rule_exact_actual_length']==64 and a['output_limits']==0
        assert a['whole_correct']==(59 if v==16 else 64) and a['task_acceptance_passed']==(v==17)
        assert read(ROOT/f'results/raw/maintenance/20261027-p1-copy-profile-supervisor-v{v}/exit.json')['exit_code']==0
        assert read(run/'service-stop.json')['exit_code']==0
        prep=ROOT/f'results/raw/maintenance/20261027-p1-copy-profile-preparation-v{v}'
        for p,h in read(prep/'final-gate.json')['source_sha256'].items():assert sha(prep/'source'/p)==h
    assert read(FOLDER/'preprocessing-posthoc-audit.json')['checked_requests']==64
    assert read(ROOT/'docs/p1/g1-contract-audit-v4.json')['status']=='passed_contract_consistency'
    cfg=read(ROOT/'configs/p1-g1-review-candidate-v4.json')
    assert not cfg['review_recommendation']['G1_passed'] and cfg['review_recommendation']['assistant_quality_score']==90
    assert not [p for p in (ROOT/'data/test').rglob('*') if p.is_file() and p.name!='.gitkeep']
    for p,h in read(FOLDER/'report-sha256.json').items():assert sha(FOLDER/p)==h
    new=['configs/p1-copy-profile-v16.json','configs/p1-copy-profile-v17.json','configs/p1-g1-review-candidate-v4.json',
        'scripts/replay/p1_copy_workload_v16.py','scripts/replay/p1_copy_workload_v17.py',
        'scripts/replay/79t_run_p1_copy_profile_v16.py','scripts/replay/79u_run_p1_copy_profile_v17.py',
        'tests/unit/test_p1_copy_workload_v16.py','tests/unit/test_p1_copy_workload_v17.py',
        'tests/integration/audit_p1_copy_profile_v16.py','tests/integration/audit_p1_copy_profile_v17.py',
        'tests/integration/audit_p1_copy_preprocessing_v17.py','tests/integration/audit_p1_g1_contract_v4.py',
        'scripts/analysis/112_summarize_p1_copy_scope_v16_v17.py','scripts/ops/verify_p1_copy_scope_delivery_v8.py',
        'docs/p1/copy-profile-protocol-v16.md','docs/p1/copy-profile-protocol-v17.md',
        'docs/p1/g1-review-candidate-v4.md','docs/p1/g1-contract-audit-v4.json',
        'docs/reports/20261027/P1新资料验证与G1合同补齐.md']
    new += [p.relative_to(ROOT).as_posix() for p in FOLDER.iterdir() if p.is_file()]
    files={**old,**{p:sha(ROOT/p) for p in new}};tree=None
    if revision:
        tree=subprocess.check_output(['git','write-tree'] if revision=='index' else ['git','rev-parse',revision],cwd=ROOT,text=True).strip()
        data=subprocess.check_output(['git','archive',tree],cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(data)) as z:
            for p,h in files.items():assert hashlib.sha256(z.extractfile(p).read()).hexdigest()==h,('Git bytes',p)
    docs=[ROOT/p for p in new if p.endswith('.md')]+[ROOT/p for p in ['README.md','docs/p1/README.md','docs/p1/g1-review-checklist-v1.md','docs/implementation-plan.md','results/README.md','daily/README.md']]+list((ROOT/'daily/20261027').glob('*.md'))
    links=0
    for p in docs:
        for target in re.findall(r'\]\(([^\n)]+)\)',p.read_text(encoding='utf-8')):
            target=target.strip('<>')
            if '://' in target or target.startswith('#'):continue
            assert (p.parent/target.split('#',1)[0]).resolve().exists(),(p,target)
            links+=1
    result={'status':'fresh_pipeline_passed_unified_G1_candidate_ready','revision':revision,'git_tree_or_revision':tree,
        'previous_protected_files':len(old),'previous_raw_files_unchanged':old_raw,'local_links':links,
        'v16_correct':59,'v17_correct':64,'total_measured':128,'warmups':4,'native_lifecycle_pairs':132,
        'formal_calibration_planned':128,'formal_calibration_collected':0,'quality_rubric':[17,17,18,19,19],
        'assistant_score':90,'assistant_recommends_G1_pass':True,'G1_passed':False,'final_test_accessed':False,'files':files}
    with out.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    return {k:v for k,v in result.items() if k!='files'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--revision');a=p.parse_args()
    print(json.dumps(verify(a.out,a.revision),ensure_ascii=False))
