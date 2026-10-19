#!/usr/bin/env bash
# 只读对比：Windows 主仓库 vs WSL 工作副本的一致性。不修改任何文件。
# 用法：bash scripts/ops/compare.sh
set -uo pipefail

WIN="$HOME/proj-win"
REPO="$HOME/projects/llm-admission-tail-latency-control"
[ -d "$WIN" ] && [ -d "$REPO" ] || { echo "FATAL: 仓库路径不存在"; exit 1; }

echo "===== 仓库一致性对比 $(date -Is) ====="
echo "Windows 主仓库（含 .git）: $WIN"
echo "WSL 工作副本（无 .git）  : $REPO"
echo

echo "--- 1. 受管目录（经 sync.sh 同步，应为完全一致）---"
printf "%-10s %7s %7s %7s  %s\n" "目录" "Win" "WSL" "差异" "状态"
for d in docs daily configs scripts src include tests data tools patches; do
  w=$(find "$WIN/$d" -type f 2>/dev/null | wc -l)
  l=$(find "$REPO/$d" -type f 2>/dev/null | wc -l)
  n=$(diff -rq "$WIN/$d" "$REPO/$d" 2>/dev/null | wc -l)
  [ "$n" -eq 0 ] && st="一致" || st="**不一致**"
  printf "%-10s %7s %7s %7s  %s\n" "$d" "$w" "$l" "$n" "$st"
done

echo
echo "--- 2. 根目录文件 ---"
for f in README.md CMakeLists.txt .gitignore .gitattributes; do
  if [ ! -e "$WIN/$f" ] && [ ! -e "$REPO/$f" ]; then st="两侧都无"
  elif [ ! -e "$WIN/$f" ] || [ ! -e "$REPO/$f" ]; then st="**缺失一方**"
  elif cmp -s "$WIN/$f" "$REPO/$f"; then st="一致"
  else st="**不一致**"; fi
  printf "  %-16s %s\n" "$f" "$st"
done
for f in "$WIN"/*.md "$WIN"/*.docx; do
  [ -e "$f" ] || continue
  b=$(basename "$f")
  if [ ! -e "$REPO/$b" ]; then st="**WSL 缺失**"
  elif cmp -s "$f" "$REPO/$b"; then st="一致"
  else st="**不一致**"; fi
  printf "  %-46s %s\n" "$b" "$st"
done

echo
echo "--- 3. 设计上只存在于一侧的内容 ---"
printf "  %-34s %-10s %-10s\n" "条目" "Windows" "WSL"
for item in ".git" "models/qwen2.5-1.5b-instruct-q4_k_m" "third_party/llama.cpp" "third_party/llama.cpp/build"; do
  a=$([ -e "$WIN/$item" ] && echo "有" || echo "无")
  b=$([ -e "$REPO/$item" ] && echo "有" || echo "无")
  printf "  %-34s %-10s %-10s\n" "$item" "$a" "$b"
done
wa=$(find "$WIN/results/raw" -type f 2>/dev/null | wc -l)
ba=$(find "$REPO/results/raw" -type f 2>/dev/null | wc -l)
printf "  %-34s %-10s %-10s\n" "results/raw（文件数）" "$wa" "$ba"

echo
echo "--- 4. 体积 ---"
du -shL "$WIN" 2>/dev/null | awk '{print "  Windows 主仓库（含引擎检出）: "$1}'
du -sh "$REPO" 2>/dev/null | awk '{print "  WSL 工作副本 : "$1}'
du -sh "$REPO/third_party/llama.cpp/build" 2>/dev/null | awk '{print "    其中构建产物: "$1}'
du -sh "$REPO/models" 2>/dev/null | awk '{print "    其中模型: "$1}'
du -sh "$WIN/results/raw" 2>/dev/null | awk '{print "  Windows 原始证据: "$1}'
