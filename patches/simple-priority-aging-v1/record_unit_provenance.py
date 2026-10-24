from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
engine = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()
snapshot = out / 'source-snapshot'
snapshot.mkdir(exist_ok=False)
sources = [
    'tools/server/server-task.h', 'tools/server/server-queue.h',
    'tools/server/server-queue.cpp', 'tools/server/server-context.cpp',
]
hashes = {}
for name in sources:
    source = engine / name
    expected = root / 'patches/simple-priority-aging-v1' / name
    assert source.read_bytes() == expected.read_bytes(), name
    target = snapshot / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    hashes[name] = hashlib.sha256(source.read_bytes()).hexdigest()
for name in ['tests/unit/test_simple_scheduler.cpp',
             'patches/simple-priority-aging-v1/run_unit_tests.sh',
             'patches/simple-priority-aging-v1.patch',
             'patches/simple-priority-aging-v1/record_unit_provenance.py']:
    source = root / name
    target = snapshot / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    hashes[name] = hashlib.sha256(source.read_bytes()).hexdigest()
test_log = (out / 'test.log').read_text()
checks = [line[5:] for line in test_log.splitlines() if line.startswith('PASS ')]
assert len(checks) == 9 and '9 scheduler checks passed' in test_log
archive = engine / 'build/tools/server/libserver-context.a'
summary = {
    'status': 'passed', 'exit_code': 0, 'checks': checks,
    'checks_passed': 9, 'model_used': False,
    'engine_source_dir': str(engine),
    'engine_base': subprocess.check_output(['git', '-C', str(engine), 'rev-parse', 'HEAD'], text=True).strip(),
    'linked_archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
    'source_hashes_sha256': hashes,
    'scope': 'Queue behavior checks, not GPU performance or full hard-ledger acceptance',
}
(out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'status': summary['status'], 'checks_passed': 9, 'out': str(out)}))
