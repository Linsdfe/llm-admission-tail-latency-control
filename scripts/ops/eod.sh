#!/usr/bin/env bash
# 每日收尾（End of Day）：
#   1) 清理临时文件与空目录   2) 生成当天产出清单（daily/<日期>/产出清单.md）
#   3) 同步到 WSL             4) 一致性检查   5) 打印人工待办
# 用法：bash scripts/ops/eod.sh [YYYYMMDD]   默认今天
set -uo pipefail

WIN="$HOME/proj-win"
WROOT="$(readlink -f "$HOME/proj-win" 2>/dev/null || echo "$HOME/proj-win")"   # find 不跟随软链接，用真实路径
REPO="$HOME/projects/llm-admission-tail-latency-control"
OPS="$WIN/scripts/ops"
DAY="${1:-$(date +%Y%m%d)}"
REF="${DAY:0:4}-${DAY:4:2}-${DAY:6:2}"
DAILY="$WIN/daily/$DAY"
LIST="$DAILY/产出清单.md"

[ -d "$WIN" ] && [ -d "$REPO" ] || { echo "FATAL: 仓库路径不存在"; exit 1; }
mkdir -p "$DAILY"

echo "===== 每日收尾 $REF（daily/$DAY） ====="

echo "--- 1. 清理临时文件 / 空目录 ---"
n=0
for pat in '*.pid' '*.tmp' '*.bak' '*.orig' '*.rej' 'nohup.out' '*.pyc'; do
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    rm -f "$f" && echo "  删除 ${f#$WIN/}" && n=$((n + 1))
  done < <(find "$WIN" -name "$pat" -not -path "*/third_party/*" -not -path "*/.git/*" 2>/dev/null)
done
while IFS= read -r d; do
  [ -z "$d" ] && continue
  rm -rf "$d" && echo "  删除空目录 ${d#$WIN/}" && n=$((n + 1))
done < <(find "$WIN/results" -type d -empty 2>/dev/null)
[ "$n" -eq 0 ] && echo "  无临时文件 / 空目录（OK）"

echo "--- 2. 生成当天产出清单 ---"
{
  echo "# $REF 当天产出清单（自动生成）"
  echo
  echo "> 由 \`scripts/ops/eod.sh\` 生成，请勿手工编辑；每次收尾会重新生成。"
  echo "> 只列受管文件（排除 \`.git\`、\`third_party/\`、\`models/\` 本地产物与 \`.docx\`）。"
  echo "> 生成时间：$(date -Is)"
  echo
  echo "| 文件 | 字节 | 修改时间 |"
  echo "|---|---:|---|"
  find "$WROOT" -type f -newermt "$REF 00:00" \
    -not -path "*/.git/*" -not -path "*/third_party/*" -not -path "*/models/*" \
    -not -name "*.docx" -not -name "产出清单.md" \
    2>/dev/null | sort | while IFS= read -r f; do
      rel="${f#"$WROOT/"}"
      sz=$(stat -c %s "$f" 2>/dev/null || echo 0)
      mt=$(stat -c %y "$f" 2>/dev/null | cut -d. -f1)
      printf "| \`%s\` | %s | %s |\n" "$rel" "$sz" "$mt"
    done
  total=$(find "$WROOT" -type f -newermt "$REF 00:00" -not -path "*/.git/*" -not -path "*/third_party/*" -not -path "*/models/*" -not -name "*.docx" -not -name "产出清单.md" 2>/dev/null | wc -l)
  echo
  echo "合计 **$total** 个文件。"
} > "$LIST"
echo "  已生成 daily/$DAY/产出清单.md（$(grep -c '^| `' "$LIST" || true) 条记录）"

echo "--- 3. 同步到 WSL 工作副本 ---"
bash "$OPS/sync.sh" | tail -2

echo "--- 4. 一致性检查 ---"
bash "$OPS/compare.sh" 2>/dev/null | sed -n '/受管目录/,/根目录文件/p' | head -14

echo "--- 5. 人工待办（Windows 侧完成）---"
cat <<'TODO'
  [ ] 填写 daily/<日期>/README.md：人时、问题与处置、下一步
  [ ] 在 daily/README.md 索引表追加当天一行
  [ ] git add -A && git commit -m "<当天主题>"
  [ ] 确认无残留 llama-server、显存已释放（宿主运行 nvidia-smi 检查）
TODO
echo "===== 收尾完成 ====="
