#!/usr/bin/env bash
# P0 冒烟重跑（v2）：环境基线检查。所有输出写入 Windows 仓库 results/raw/prestudy/2026-10-19-smoke-v2/
set -u

REPO_WSL="$HOME/projects/llm-admission-tail-latency-control"
RUN_DIR="$HOME/proj-win/results/raw/prestudy/2026-10-19-smoke-v2"
mkdir -p "$RUN_DIR/requests"

{
  echo "===== 采集时间 ====="; date -Is
  echo "===== whoami / home ====="; whoami; echo "$HOME"
  echo "===== 仓库路径 ====="; ls -d "$REPO_WSL"
  echo "===== proj-win 软链接 ====="; ls -ld "$HOME/proj-win"; readlink -f "$HOME/proj-win"
  echo "===== 引擎二进制 ====="
  ls -lh "$REPO_WSL/third_party/llama.cpp/build/bin/llama-server" \
         "$REPO_WSL/third_party/llama.cpp/build/bin/llama-cli" \
         "$REPO_WSL/third_party/llama.cpp/build/bin/llama-bench"
  echo "===== 引擎版本 ====="
  git -C "$REPO_WSL/third_party/llama.cpp" rev-parse HEAD
  git -C "$REPO_WSL/third_party/llama.cpp" describe --tags
  echo "===== 模型文件 ====="
  ls -lh "$REPO_WSL/models/qwen2.5-1.5b-instruct-q4_k_m/qwen2.5-1.5b-instruct-q4_k_m.gguf"
  sha256sum "$REPO_WSL/models/qwen2.5-1.5b-instruct-q4_k_m/qwen2.5-1.5b-instruct-q4_k_m.gguf"
  echo "===== Python ====="; python3 --version; command -v python3
  echo "===== 内存 / 磁盘 ====="; free -h; df -h "$HOME" | tail -1
  echo "===== 残留 llama-server 进程 ====="; pgrep -a llama-server || echo "无残留进程"
  echo "===== nvidia-smi ====="; nvidia-smi
  echo "===== 结束 ====="
} 2>&1 | tee "$RUN_DIR/00-env-check.txt"

echo "OK: $RUN_DIR/00-env-check.txt"
