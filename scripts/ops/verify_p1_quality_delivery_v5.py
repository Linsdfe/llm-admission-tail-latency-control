"""Preserve v4 evidence and validate v13 delivery; never approve G1."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
BASE = '9418c94c5dd4f241106c60e00c96b68b5fb32456'
ARCHIVE = 'results/archives/20261026/daily-20261026-161720-ca10e1d1.zip'
ARCHIVE_SHA = 'a8ce7741b0142370ecfe96895bd38f90067adc8675bde2c2988b361239919c7e'


def read(p): return json.loads(p.read_text(encoding='utf-8'))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def git_bytes(name): return subprocess.check_output(['git', 'show', BASE + ':' + name], cwd=ROOT)


def verify(out, revision=None):
    old = json.loads(git_bytes('docs/p1/g1-freeze-candidate-v3.json'))['files']
    old.update(json.loads(git_bytes('docs/p1/g1-freeze-candidate-v3-addendum.json'))['files'])
    previous = json.loads(git_bytes('docs/p1/g1-quality-evidence-v4.json'))
    old.update(previous['files'])
    for name in ['docs/p1/g1-quality-evidence-v4.json', 'docs/p1/g1-freeze-candidate-v3.json', 'docs/p1/g1-freeze-candidate-v3-addendum.json']:
        old[name] = hashlib.sha256(git_bytes(name)).hexdigest()
    for name, digest in old.items():
        assert sha(ROOT / name) == digest, ('previous protected changed', name)
    assert sha(ROOT / ARCHIVE) == ARCHIVE_SHA
    raw_count = 0
    maps = ['results/raw/maintenance/20261026-p1-g1-delivery-v2/new-raw-sha256.json',
            'results/raw/maintenance/20261026-p1-quality-delivery-v4/new-raw-sha256.json']
    with zipfile.ZipFile(ROOT / ARCHIVE) as archive:
        for name in maps:
            raw_map = (ROOT / name).read_bytes()
            if 'quality-delivery-v4' in name:
                assert raw_map == archive.read('20261026/project/' + name)
            for path, digest in json.loads(raw_map).items():
                assert sha(ROOT / path) == digest, ('previous raw changed', path)
                raw_count += 1
    native = ROOT / 'results/raw/development/20261026-1650-p1-lifecycle-v13'
    prep = ROOT / 'results/raw/maintenance/20261026-p1-lifecycle-preparation-v13'
    snapshots = 0
    for manifest in [native / 'source-sha256.json', *prep.rglob('source-sha256.json')]:
        for name, digest in read(manifest).items():
            assert sha(manifest.parent / 'source' / name) == digest, ('execution snapshot changed', manifest, name)
            snapshots += 1
    lifecycle = ROOT / 'docs/experiments/20261026-p1-lifecycle-v13'
    boundary = ROOT / 'docs/experiments/20261026-p1-budget-boundaries-v13'
    audit = read(lifecycle / 'independent-audit.json')
    assert audit['status'] == 'passed' and audit['correctness_requests'] == 18 and audit['actual_sent'] == 15
    assert audit['same_server_recoveries'] == 8 and audit['native_launches'] == audit['native_releases'] == 16
    assert read(lifecycle / 'synthetic-gate.json')['status'] == 'passed'
    assert len(read(lifecycle / 'synthetic-gate.json')['scenarios']) == 15
    assert read(lifecycle / 'legacy-regression.json')['status'] == 'known_problems_reproduced'
    geometry = read(boundary / 'independent-audit.json')
    assert geometry['status'] == 'passed' and geometry['complete_intervals'] == 134 and geometry['all_intervals_miss_locked_budget']
    for folder in [lifecycle, boundary]:
        for name, digest in read(folder / 'report-sha256.json').items():
            assert sha(folder / name) == digest
    assert not [p for p in (ROOT / 'data/test').rglob('*') if p.is_file() and p.name != '.gitkeep']
    names = ['docs/p1/lifecycle-and-boundary-protocol-v13.md', 'scripts/replay/p1_live_control_v13.py',
             'scripts/replay/p1_controlled_replay_v13.py', 'scripts/replay/79q_run_p1_lifecycle_v13.py',
             'tests/integration/verify_p1_lifecycle_v13.py', 'tests/integration/audit_p1_lifecycle_v13.py',
             'tests/integration/audit_p1_budget_boundaries_v13.py', 'scripts/analysis/109_analyze_p1_budget_boundaries_v13.py',
             'scripts/ops/verify_p1_quality_delivery_v5.py', 'docs/reports/20261026/P1异常回收与算法作用边界补强.md']
    for folder in [lifecycle, boundary]:
        names += [p.relative_to(ROOT).as_posix() for p in folder.iterdir() if p.is_file()]
    files = {**old, **{name: sha(ROOT / name) for name in names}}
    tree = None
    if revision:
        tree = subprocess.check_output(['git', 'write-tree'] if revision == 'index' else ['git', 'rev-parse', revision], cwd=ROOT, text=True).strip()
        archive = subprocess.check_output(['git', 'archive', tree], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            for name, digest in files.items():
                assert hashlib.sha256(tar.extractfile(name).read()).hexdigest() == digest, ('Git bytes', name)
    docs = [ROOT / name for name in names if name.endswith('.md')]
    docs += [ROOT / name for name in ['README.md', 'docs/p1/README.md', 'docs/p1/g1-review-checklist-v1.md',
                                      'docs/implementation-plan.md', 'results/README.md', 'daily/README.md']]
    docs += list((ROOT / 'daily/20261026').glob('*.md'))
    links = 0
    for path in docs:
        for target in re.findall(r'\]\(([^\n)]+)\)', path.read_text(encoding='utf-8')):
            target = target.strip('<>')
            if '://' in target or target.startswith('#'):
                continue
            assert (path.parent / target.split('#', 1)[0]).resolve().exists(), (path, target)
            links += 1
    result = {'status': 'quality_improved_not_G1_approval', 'revision': revision, 'git_tree_or_revision': tree,
              'previous_protected_files': len(old), 'previous_raw_files_unchanged': raw_count,
              'new_execution_snapshot_files_checked': snapshots, 'local_links': links,
              'new_correctness_planned': 18, 'new_actual_sent': 15, 'new_model_entered': 14, 'new_warmups': 2,
              'boundary_new_GPU_measurements': 0, 'subjective_fixed_rubric': [16, 16, 18, 19, 19],
              'subjective_quality_score': 88, 'G1_passed': False, 'final_test_accessed': False, 'files': files}
    with out.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return {k: v for k, v in result.items() if k != 'files'}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--revision')
    a = p.parse_args()
    print(json.dumps(verify(a.out, a.revision), ensure_ascii=False))
