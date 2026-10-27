"""Preserve prior evidence and validate the bounded v15 diagnostic delivery."""
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
BASE='34c7f697bcc69e6833116957be641cac39ce553e'
BASE_ARCHIVE='results/archives/20261027/daily-20261027-090350-ffac7cd4.zip'
BASE_SHA='905ff98439920dfd54e01e700d0247091e06bf4a0d079c44c23502d6877d5b53'


def read(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def verify(out,revision=None):
    name='docs/p1/g1-content-profile-evidence-v6.json'
    previous=subprocess.check_output(['git','show',BASE+':'+name],cwd=ROOT)
    assert (ROOT/name).read_bytes()==previous
    old=json.loads(previous)['files'];old[name]=hashlib.sha256(previous).hexdigest()
    for name,digest in old.items():assert sha(ROOT/name)==digest,('old protected',name)
    assert sha(ROOT/BASE_ARCHIVE)==BASE_SHA
    maps=['results/raw/maintenance/20261026-p1-g1-delivery-v2/new-raw-sha256.json',
          'results/raw/maintenance/20261026-p1-quality-delivery-v4/new-raw-sha256.json',
          'results/raw/maintenance/20261026-p1-quality-delivery-v5/new-raw-sha256.json',
          'results/raw/maintenance/20261027-p1-content-delivery-v6/new-raw-sha256.json']
    raw_count=0
    with zipfile.ZipFile(ROOT/BASE_ARCHIVE) as z:
        for name in maps:
            data=(ROOT/name).read_bytes()
            if 'content-delivery-v6/' in name:assert data==z.read('20261027/project/'+name)
            for source,digest in json.loads(data).items():assert sha(ROOT/source)==digest,('old raw',source);raw_count+=1
    run=ROOT/'results/raw/development/20261027-0920-p1-content-factor-v15'
    source=read(run/'source-sha256.json')
    for name,digest in source.items():assert sha(run/'source'/name)==digest,('native source',name)
    prep=ROOT/'results/raw/maintenance/20261027-p1-content-factor-preparation-v15'
    for name,digest in read(prep/'final-gate.json')['source_sha256'].items():assert sha(prep/'source'/name)==digest
    for name,digest in read(run/'reused-source-sha256.json').items():assert sha(ROOT/name)==digest,('reused fixture',name)
    folder=ROOT/'docs/experiments/20261027-p1-content-factor-v15'
    a=read(folder/'independent-audit.json')
    assert a['status']=='passed_diagnostic_evidence_audit' and a['measured_requests']==32 and a['warmups']==2
    assert a['seen_fixtures']==8 and a['distinct_prompts']==16 and a['new_source_facts']==0
    assert [a['cells'][c]['strict_correct'] for c in ('TG','TU','SG','SU')]==[0,0,1,1]
    assert a['TG_exact_content_reproduced']==8 and not a['new_task_acceptance_evaluated']
    assert read(folder/'template-audit.json')['status']=='passed_read_only_template_audit'
    for name,digest in read(folder/'report-sha256.json').items():assert sha(folder/name)==digest
    assert not [p for p in (ROOT/'data/test').rglob('*') if p.is_file() and p.name!='.gitkeep']
    new=['configs/p1-content-factor-diagnostic-v15.json','docs/p1/content-factor-diagnostic-protocol-v15.md',
         'scripts/replay/p1_content_factor_v15.py','scripts/replay/79s_run_p1_content_factor_v15.py',
         'tests/unit/test_p1_content_factor_v15.py','tests/integration/audit_p1_content_factor_v15.py',
         'tests/integration/audit_p1_content_factor_template_v15.py','scripts/analysis/111_summarize_p1_content_factor_v15.py',
         'scripts/ops/verify_p1_content_factor_delivery_v7.py','docs/p1/workload-scope-convergence-after-v15.md',
         'docs/reports/20261027/P1筛选任务四条件诊断与负载收敛.md']
    new += [p.relative_to(ROOT).as_posix() for p in folder.iterdir() if p.is_file()]
    files={**old,**{name:sha(ROOT/name) for name in new}}
    tree=None
    if revision:
        tree=subprocess.check_output(['git','write-tree'] if revision=='index' else ['git','rev-parse',revision],cwd=ROOT,text=True).strip()
        data=subprocess.check_output(['git','archive',tree],cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(data)) as z:
            for name,digest in files.items():assert hashlib.sha256(z.extractfile(name).read()).hexdigest()==digest,('Git bytes',name)
    docs=[ROOT/p for p in new if p.endswith('.md')]+[ROOT/p for p in ['README.md','docs/p1/README.md','docs/p1/g1-review-checklist-v1.md','docs/implementation-plan.md','results/README.md','daily/README.md']]
    docs+=list((ROOT/'daily/20261027').glob('*.md'));links=0
    for p in docs:
        for target in re.findall(r'\]\(([^\n)]+)\)',p.read_text(encoding='utf-8')):
            target=target.strip('<>')
            if '://' in target or target.startswith('#'):continue
            assert (p.parent/target.split('#',1)[0]).resolve().exists(),(p,target)
            links+=1
    result={'status':'diagnostic_complete_task_not_resolved','revision':revision,'git_tree_or_revision':tree,
            'previous_protected_files':len(old),'previous_raw_files_unchanged':raw_count,'native_execution_source_files':len(source),
            'local_links':links,'measured_requests':32,'warmups':2,'seen_fixtures':8,'new_source_facts':0,
            'strict_correct_by_arm':[0,0,1,1],'subjective_quality_score_unchanged':88,
            'new_task_acceptance_evaluated':False,'G1_passed':False,'final_test_accessed':False,'files':files}
    with out.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    return {k:v for k,v in result.items() if k!='files'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--revision')
    a=p.parse_args();print(json.dumps(verify(a.out,a.revision),ensure_ascii=False))
