#!/usr/bin/env bash
set -euo pipefail
task_patch_dir="$(cd "$(dirname "$0")" && pwd)"
task_root="$(cd "$task_patch_dir/../.." && pwd)"
task_src="${1:?patched engine source directory required}"
task_out="${2:?new validation evidence directory required}"
mkdir "$task_out"
date --iso-8601=seconds > "$task_out/started-at.txt"
sha256sum "$task_root/tests/unit/test_simple_scheduler.cpp" > "$task_out/test-source-sha256.txt"
task_include_args=(
    -I"$task_src/tools/server" -I"$task_src/common"
    -I"$task_src/vendor" -I"$task_src/include" -I"$task_src/ggml/include"
    -I"$task_src/tools/mtmd"
)
c++ -std=c++17 -O2 -pthread "${task_include_args[@]}" \
    "$task_root/tests/unit/test_simple_scheduler.cpp" \
    "$task_src/build/tools/server/libserver-context.a" \
    -L"$task_src/build/bin" -Wl,-rpath,"$task_src/build/bin" \
    -lllama-common "$task_src/build/common/libllama-common-base.a" \
    -lmtmd -lllama -lggml -lggml-cpu -lggml-cuda -lggml-base \
    -o "$task_out/test-simple-scheduler" 2>&1 | tee "$task_out/build.log"
"$task_out/test-simple-scheduler" 2>&1 | tee "$task_out/test.log"
sha256sum "$task_out/test-simple-scheduler" > "$task_out/test-binary-sha256.txt"
date --iso-8601=seconds > "$task_out/finished-at.txt"
