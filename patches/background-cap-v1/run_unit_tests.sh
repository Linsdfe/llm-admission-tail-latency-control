#!/usr/bin/env bash
set -euo pipefail
task_patch_dir="$(cd "$(dirname "$0")" && pwd)"
task_root="$(cd "$task_patch_dir/../.." && pwd)"
task_src="${1:?patched engine source directory required}"
task_out="${2:?new validation evidence directory required}"
mkdir "$task_out"
date --iso-8601=seconds > "$task_out/started-at.txt"
task_include_args=(-I"$task_src/tools/server" -I"$task_src/common" -I"$task_src/vendor" -I"$task_src/include" -I"$task_src/ggml/include" -I"$task_src/tools/mtmd")
for task_test in test_simple_scheduler test_background_cap; do
    cp "$task_root/tests/unit/$task_test.cpp" "$task_out/$task_test.cpp"
    sha256sum "$task_out/$task_test.cpp" > "$task_out/$task_test-source-sha256.txt"
    c++ -std=c++17 -O2 -pthread "${task_include_args[@]}" \
        "$task_out/$task_test.cpp" "$task_src/build/tools/server/libserver-context.a" \
        -L"$task_src/build/bin" -Wl,-rpath,"$task_src/build/bin" \
        -lllama-common "$task_src/build/common/libllama-common-base.a" \
        -lmtmd -lllama -lggml -lggml-cpu -lggml-cuda -lggml-base \
        -o "$task_out/$task_test" 2>&1 | tee "$task_out/$task_test-build.log"
    "$task_out/$task_test" 2>&1 | tee "$task_out/$task_test.log"
    sha256sum "$task_out/$task_test" > "$task_out/$task_test-binary-sha256.txt"
done
python3 - "$task_root" "$task_src" "$task_out" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

root, source, out = map(Path, sys.argv[1:])
entries = {}
for name in ('server-task.h', 'server-queue.h', 'server-queue.cpp', 'server-context.cpp'):
    actual = source / 'tools/server' / name
    reviewed = root / 'patches/background-cap-v1/tools/server' / name
    assert actual.read_bytes() == reviewed.read_bytes(), name
    entries[str(actual)] = hashlib.sha256(actual.read_bytes()).hexdigest()
for item in [root / 'patches/background-cap-v1.patch', source / 'build/bin/llama-server',
             source / 'build/bin/libllama-server-impl.so', source / 'build/tools/server/libserver-context.a',
             out / 'test_simple_scheduler', out / 'test_background_cap']:
    entries[str(item)] = hashlib.sha256(item.read_bytes()).hexdigest()
counts = {name: sum(line.startswith('PASS ') for line in (out / (name + '.log')).read_text().splitlines())
          for name in ('test_simple_scheduler', 'test_background_cap')}
assert counts == {'test_simple_scheduler': 9, 'test_background_cap': 19}, counts
(out / 'summary.json').write_text(json.dumps({'status': 'passed', 'model_loaded': False,
    'passed_assertions': counts, 'source_matches_reviewed_copies': True,
    'sha256': entries}, indent=2) + '\n')
PY
date --iso-8601=seconds > "$task_out/finished-at.txt"
