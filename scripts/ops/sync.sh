#!/usr/bin/env bash
# 单向同步：Windows 主仓库 → WSL 工作副本（文档 / 脚本 / 配置 / 代码骨架 / 根文件）。
# WSL 副本不含 .git（纯构建运行工作副本）；原始证据 results/raw 只存 Windows，不同步。
# 用法：bash scripts/ops/sync.sh
set -uo pipefail

WIN="$HOME/proj-win"
REPO="$HOME/projects/llm-admission-tail-latency-control"

[ -d "$REPO" ] || { echo "FATAL: WSL 工作副本不存在: $REPO"; exit 1; }

# 清理 WSL 副本里的 Python 字节码缓存（由 import 生成，不入库）
find "$REPO/scripts" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$REPO/scripts" -name "*.pyc" -delete 2>/dev/null || true

for d in docs daily configs scripts src include tests data tools patches; do
  if [ -d "$WIN/$d" ]; then
    mkdir -p "$REPO/$d"
    cp -a "$WIN/$d/." "$REPO/$d/" && echo "同步 $d/"
  fi
done

for f in README.md CMakeLists.txt .gitignore .gitattributes; do
  if [ -f "$WIN/$f" ]; then
    cp -a "$WIN/$f" "$REPO/$f" && echo "同步 $f"
  fi
done
for f in "$WIN"/*.md "$WIN"/*.docx; do
  [ -f "$f" ] || continue
  b=$(basename "$f")
  cp -a "$f" "$REPO/$b" && echo "同步 $b"
done

echo "--- 清理 WSL 副本中已从 Windows 删除的文件 ---"
removed=0
for d in docs daily configs scripts src include tests data tools patches; do
  [ -d "$REPO/$d" ] || continue
  while IFS= read -r f; do
    rel="${f#"$REPO/$d/"}"
    if [ ! -e "$WIN/$d/$rel" ]; then
      rm -f "$f" && echo "  移除 $d/$rel" && removed=$((removed + 1))
    fi
  done < <(find "$REPO/$d" -type f 2>/dev/null)
  find "$REPO/$d" -type d -empty -delete 2>/dev/null || true
done
[ "$removed" -eq 0 ] && echo "  无残留文件"

echo "--- 同步后校验 ---"
bad=0
for d in docs daily configs scripts src include tests data tools patches; do
  if ! diff -rq "$WIN/$d" "$REPO/$d" >/dev/null 2>&1; then
    echo "  $d: **不一致**"
    bad=1
  fi
done
if [ "$bad" -eq 0 ]; then
  echo "  10 个受管目录全部一致"
else
  echo "  存在不一致，请运行 scripts/ops/compare.sh 排查"
fi
