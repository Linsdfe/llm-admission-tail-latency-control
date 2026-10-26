"""Check preserved evidence and the new quality-repair delivery, not approve G1."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ['20261026-1500-p1-live-mechanism-v11', '20261026-1515-p1-live-mechanism-v11-r2',
        '20261026-1525-p1-live-mechanism-v11-r3', '20261026-1545-p1-soft-role-v12']
PUBLIC = ['20261026-p1-live-mechanism-v11-r3', '20261026-p1-soft-role-v12', '20261026-p1-quality-repair-v4']


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(out, revision=None):
    old = read(ROOT / 'docs/p1/g1-freeze-candidate-v3.json')['files']
    addendum = read(ROOT / 'docs/p1/g1-freeze-candidate-v3-addendum.json')
    assert sha(ROOT / addendum['base_candidate_manifest']) == addendum['base_manifest_sha256']
    old.update(addendum['files'])
    for name, digest in old.items():
        assert sha(ROOT / name) == digest, ('old protected file changed', name)
    raw = read(ROOT / 'results/raw/maintenance/20261026-p1-g1-delivery-v2/new-raw-sha256.json')
    for name, digest in raw.items():
        assert sha(ROOT / name) == digest, ('old raw changed', name)
    snapshots = 0
    for run_id in RUNS:
        run = ROOT / 'results/raw/development' / run_id
        for name, digest in read(run / 'source-sha256.json').items():
            assert sha(run / 'source' / name) == digest, ('execution snapshot changed', run_id, name)
            snapshots += 1
    for name in PUBLIC[:2]:
        folder = ROOT / 'docs/experiments' / name
        for path, digest in read(folder / 'report-sha256.json').items():
            assert sha(folder / path) == digest
        assert read(folder / 'summary.json')['status'] == 'passed_independent_prototype_audit'
        assert read(folder / 'supplementary-audit.json')['status'] == 'passed'
    summary = read(ROOT / 'docs/experiments' / PUBLIC[2] / 'summary.json')
    assert summary['new_valid_measurements'] == 3134 and summary['new_valid_warmups'] == 28
    assert summary['warm_history_unique'] == 192 and summary['subjective_work_quality_score'] == 84
    assert not summary['G1_passed'] and not summary['final_test_accessed']
    assert not [p for p in (ROOT / 'data/test').rglob('*') if p.is_file() and p.name != '.gitkeep']
    for name, digest in read(ROOT / 'docs/experiments' / PUBLIC[2] / 'evidence-sha256.json').items():
        assert sha(ROOT / name) == digest
    patterns = ['scripts/analysis/10[5-8]*.py', 'scripts/replay/79[m-p]*.py',
                'scripts/replay/p1_*v11.py', 'scripts/replay/p1_*v12.py',
                'tests/unit/test_p1_*v11.py', 'tests/unit/test_p1_*v12.py',
                'tests/integration/*p1_*v11*.py', 'tests/integration/*p1_*v12.py',
                'configs/p1-live-mechanism-v11.json', 'docs/p1/*v11*.md', 'docs/p1/*v12.md',
                'docs/p1/work-quality-scorecard-v1.md', 'docs/p1/quality-improvement-design-note-v4.md',
                'docs/reports/20261026/P1质量补强与同口径复评分.md', 'scripts/ops/verify_p1_quality_delivery_v4.py']
    files = {p.relative_to(ROOT).as_posix(): sha(p) for pattern in patterns for p in ROOT.glob(pattern)}
    for name in PUBLIC:
        files.update({p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT / 'docs/experiments' / name).iterdir() if p.is_file()})
    files.update({'docs/p1/g1-freeze-candidate-v3.json': sha(ROOT / 'docs/p1/g1-freeze-candidate-v3.json'),
                  'docs/p1/g1-freeze-candidate-v3-addendum.json': sha(ROOT / 'docs/p1/g1-freeze-candidate-v3-addendum.json')})
    if revision is not None:
        for name, digest in {**old, **files}.items():
            spec = ':' + name if revision == 'index' else revision + ':' + name
            assert hashlib.sha256(subprocess.check_output(['git', 'show', spec], cwd=ROOT)).hexdigest() == digest, ('Git bytes', spec)
    links = 0
    docs = [ROOT / name for name in files if name.endswith('.md')]
    docs += [ROOT / name for name in ['README.md', 'docs/p1/README.md', 'docs/p1/g1-review-checklist-v1.md',
                                      'docs/implementation-plan.md', 'results/README.md', 'daily/README.md']]
    docs += list((ROOT / 'daily/20261026').glob('*.md'))
    for path in docs:
        for target in re.findall(r'\]\(([^\n)]+)\)', path.read_text(encoding='utf-8')):
            target = target.strip('<>')
            if '://' in target or target.startswith('#'):
                continue
            assert (path.parent / target.split('#', 1)[0]).resolve().exists(), (path, target)
            links += 1
    result = {'status': 'quality_repair_evidence_checked_not_G1_approval', 'revision': revision,
              'previous_protected_files': len(old), 'previous_raw_files_unchanged': len(raw),
              'execution_snapshot_files_checked': snapshots, 'local_links': links,
              'new_valid_measurements': 3134, 'new_valid_warmups': 28, 'subjective_fixed_rubric_score': 84,
              'G1_passed': False, 'final_test_accessed': False, 'files': files}
    with out.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return {k: v for k, v in result.items() if k != 'files'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--revision', help='index, HEAD, or an existing Git revision')
    args = parser.parse_args()
    print(json.dumps(verify(args.out, args.revision), ensure_ascii=False))
