"""Validate retained v14 failure evidence without approving the task or G1."""
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
BASE='b6d329b87440872b05ddec370f6f380f51285bfb'
BASE_ARCHIVE='results/archives/20261026/daily-20261026-170342-d129d7dc.zip'
BASE_SHA='4e9ee988321923dfa85ab725b5a56a4619c369e9b735dfe26f6cdf3dc2fb09b9'


def read(p): return json.loads(p.read_text(encoding='utf-8'))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def verify(out,revision=None):
    name='docs/p1/g1-quality-evidence-v5.json'
    previous_bytes=subprocess.check_output(['git','show',BASE+':'+name],cwd=ROOT)
    old=read(ROOT/name)['files']
    assert (ROOT/name).read_bytes()==previous_bytes
    old[name]=hashlib.sha256(previous_bytes).hexdigest()
    for path,digest in old.items(): assert sha(ROOT/path)==digest,('previous protected file',path)
    assert sha(ROOT/BASE_ARCHIVE)==BASE_SHA
    raw_count=0
    maps=['results/raw/maintenance/20261026-p1-g1-delivery-v2/new-raw-sha256.json',
          'results/raw/maintenance/20261026-p1-quality-delivery-v4/new-raw-sha256.json',
          'results/raw/maintenance/20261026-p1-quality-delivery-v5/new-raw-sha256.json']
    with zipfile.ZipFile(ROOT/BASE_ARCHIVE) as archive:
        for path in maps:
            data=(ROOT/path).read_bytes()
            if 'delivery-v5/' in path: assert data==archive.read('20261026/project/'+path)
            for source,digest in json.loads(data).items():
                assert sha(ROOT/source)==digest,('previous raw',source)
                raw_count+=1
    run=ROOT/'results/raw/development/20261026-1730-p1-content-profile-v14'
    sources=read(run/'source-sha256.json')
    for path,digest in sources.items(): assert sha(run/'source'/path)==digest,('native snapshot',path)
    prep=ROOT/'results/raw/maintenance/20261026-p1-content-profile-preparation-v14'
    for path,digest in read(prep/'final-gate.json')['source_sha256'].items():
        assert sha(prep/'source'/path)==digest
    folder=ROOT/'docs/experiments/20261026-p1-content-profile-v14'
    audit=read(folder/'independent-audit.json')
    assert audit['status']=='passed_technical_and_evidence_audit' and audit['correct']==2
    assert audit['measured_requests']==48 and audit['warmups']==2
    assert not audit['task_entry_passed'] and not audit['task_quality_gate_passed'] and not audit['natural_length_gate_passed']
    assert not audit['scheduler_comparison_started'] and not audit['G1_passed'] and not audit['final_test_accessed']
    assert read(folder/'provenance-audit.json')['status']=='passed'
    assert read(folder/'error-patterns.json')['subjective_score_unchanged']==88
    for path,digest in read(folder/'report-sha256.json').items(): assert sha(folder/path)==digest
    assert not [p for p in (ROOT/'data/test').rglob('*') if p.is_file() and p.name!='.gitkeep']
    new=['configs/p1-content-selection-profile-v14.json','docs/p1/content-selection-profile-protocol-v14.md',
         'scripts/replay/79r_run_p1_content_profile_v14.py','scripts/replay/p1_content_workload_v14.py',
         'scripts/replay/p1_profile_preflight_v14.py','tests/unit/test_p1_content_profile_v14.py',
         'tests/integration/audit_p1_content_profile_v14.py','tests/integration/audit_p1_content_provenance_v14.py',
         'scripts/analysis/110_summarize_p1_content_failure_v14.py','scripts/ops/verify_p1_content_delivery_v6.py',
         'docs/reports/20261027/P1可变结果任务验证与停止扩大记录.md']
    new += [p.relative_to(ROOT).as_posix() for p in folder.iterdir() if p.is_file()]
    files={**old,**{path:sha(ROOT/path) for path in new}}
    tree=None
    if revision:
        tree=subprocess.check_output(['git','write-tree'] if revision=='index' else ['git','rev-parse',revision],cwd=ROOT,text=True).strip()
        data=subprocess.check_output(['git','archive',tree],cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            for path,digest in files.items(): assert hashlib.sha256(tar.extractfile(path).read()).hexdigest()==digest,('Git bytes',path)
    docs=[ROOT/p for p in new if p.endswith('.md')]
    docs += [ROOT/p for p in ['README.md','docs/p1/README.md','docs/p1/g1-review-checklist-v1.md','docs/implementation-plan.md','results/README.md','daily/README.md']]
    docs += list((ROOT/'daily/20261027').glob('*.md'))
    links=0
    for p in docs:
        for link in re.findall(r'\]\(([^\n)]+)\)',p.read_text(encoding='utf-8')):
            link=link.strip('<>')
            if '://' in link or link.startswith('#'): continue
            assert (p.parent/link.split('#',1)[0]).resolve().exists(),(p,link)
            links+=1
    result={'status':'technical_evidence_valid_task_entry_rejected','revision':revision,'git_tree_or_revision':tree,
            'experiment_date':'2026-10-26','closeout_client_date':'2026-10-27','previous_protected_files':len(old),
            'previous_raw_files_unchanged':raw_count,'native_execution_source_files':len(sources),'local_links':links,
            'measured_requests':48,'warmups':2,'whole_correct':2,'task_entry_passed':False,
            'scheduler_comparison_started':False,'subjective_quality_score_unchanged':88,
            'G1_passed':False,'final_test_accessed':False,'files':files}
    with out.open('x',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2)
    return {k:v for k,v in result.items() if k!='files'}


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--out',type=Path,required=True); p.add_argument('--revision')
    a=p.parse_args(); print(json.dumps(verify(a.out,a.revision),ensure_ascii=False))
