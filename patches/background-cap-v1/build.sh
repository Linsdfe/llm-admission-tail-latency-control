#!/usr/bin/env bash
set -euo pipefail
task_patch_dir="$(cd "$(dirname "$0")" && pwd)"
task_root="$(cd "$task_patch_dir/../.." && pwd)"
task_src="/home/aiinfra/projects/llm-admission-tail-latency-control/third_party/llama.cpp-background-cap-v1-20261023"
task_base="620d2225cc436ab0b001e3200e590e3b286ef9ab"
task_out="${1:?new build evidence directory required}"
mkdir "$task_out"
date --iso-8601=seconds > "$task_out/started-at.txt"
if [[ ! -d "$task_src" ]]; then
    git clone --shared "$task_root/third_party/llama.cpp" "$task_src" 2>&1 | tee "$task_out/clone.log"
    git -C "$task_src" checkout -b codex/background-cap-v1 "$task_base" 2>&1 | tee "$task_out/checkout.log"
    git -C "$task_src" apply --check "$task_root/patches/background-cap-v1.patch"
    git -C "$task_src" apply "$task_root/patches/background-cap-v1.patch"
fi
test "$(git -C "$task_src" rev-parse HEAD)" = "$task_base"
for task_name in server-task.h server-queue.h server-queue.cpp server-context.cpp; do
    cmp "$task_patch_dir/tools/server/$task_name" "$task_src/tools/server/$task_name"
done
git -C "$task_src" rev-parse HEAD > "$task_out/engine-base.txt"
git -C "$task_src" status --short > "$task_out/working-tree.txt"
sha256sum "$task_root/patches/background-cap-v1.patch" > "$task_out/published-patch-sha256.txt"
git -C "$task_src" diff --binary > "$task_out/applied.patch"
cmake -S "$task_src" -B "$task_src/build" -DGGML_CUDA=ON -DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc -DCMAKE_CUDA_ARCHITECTURES=86 -DCMAKE_BUILD_TYPE=Release -DCUDAToolkit_ROOT=/usr/local/cuda -DCUDA_CUDART=/usr/local/cuda/lib64/libcudart.so 2>&1 | tee "$task_out/configure.log"
cmake --build "$task_src/build" -j4 --target llama-server 2>&1 | tee "$task_out/build.log"
sha256sum "$task_src/build/bin/llama-server" > "$task_out/server-sha256.txt"
date --iso-8601=seconds > "$task_out/finished-at.txt"
