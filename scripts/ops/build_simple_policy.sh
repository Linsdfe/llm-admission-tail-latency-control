#!/usr/bin/env bash
# Build the isolated P0 scheduling prototype; never modify the frozen engine.
set -euo pipefail
task_root="$(cd "$(dirname "$0")/../.." && pwd)"
task_src="/home/aiinfra/projects/llm-admission-tail-latency-control/third_party/llama.cpp-simple-policy-v1-20261022"
task_out="${1:?new build evidence directory required}"
task_mode="${2:-full}"
mkdir "$task_out"
git -C "$task_src" rev-parse HEAD > "$task_out/engine-base.txt"
git -C "$task_src" status --short > "$task_out/working-tree.txt"
date --iso-8601=seconds > "$task_out/started-at.txt"
cmake -S "$task_src" -B "$task_src/build" -DGGML_CUDA=ON -DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc -DCMAKE_CUDA_ARCHITECTURES=86 -DCMAKE_BUILD_TYPE=Release -DCUDAToolkit_ROOT=/usr/local/cuda -DCUDA_CUDART=/usr/local/cuda/lib64/libcudart.so 2>&1 | tee "$task_out/configure.log"
if [[ "$task_mode" == libraries ]]; then
    cmake --build "$task_src/build" -j4 --target ggml-cuda 2>&1 | tee "$task_out/build.log"
else
    cmake --build "$task_src/build" -j4 --target llama-server 2>&1 | tee "$task_out/build.log"
    sha256sum "$task_src/build/bin/llama-server" > "$task_out/server-sha256.txt"
    git -C "$task_src" diff --binary > "$task_out/applied.patch"
    sha256sum "$task_root/patches/simple-priority-aging-v1.patch" > "$task_out/published-patch-sha256.txt"
fi
date --iso-8601=seconds > "$task_out/finished-at.txt"
