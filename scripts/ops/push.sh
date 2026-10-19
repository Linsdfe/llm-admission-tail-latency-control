#!/usr/bin/env bash
# 一键推送本地提交到 GitHub。
#
# 背景：本机 Windows 侧 git 直连 GitHub 不通（需代理 + openssl 后端），而 WSL 网络可直连
# GitHub 的 22 端口；GitHub 认证用的私钥在 Windows 侧。因此本脚本在 WSL 内：
#   1) 临时复制 Windows 私钥到 /tmp（chmod 600）
#   2) 用该私钥通过 SSH 推送 /mnt/c 上的主仓库
#   3) 无论成败都删除临时私钥（trap 保证）
#
# 用法：bash scripts/ops/push.sh [分支名]      默认 main
# 可用 WIN_SSH_KEY 环境变量覆盖私钥路径。
set -uo pipefail

KEY_SRC="${WIN_SSH_KEY:-/mnt/c/Users/1/.ssh/id_ed25519}"
BRANCH="${1:-main}"
REPO_REAL="$(readlink -f "$HOME/proj-win" 2>/dev/null || echo "$HOME/proj-win")"

[ -d "$REPO_REAL/.git" ] || { echo "FATAL: 主仓库不存在: $REPO_REAL"; exit 1; }
[ -f "$KEY_SRC" ] || { echo "FATAL: 找不到 Windows SSH 私钥: $KEY_SRC"; exit 1; }

TMPKEY="$(mktemp /tmp/gh_key.XXXXXX)"
cleanup() { rm -f "$TMPKEY"; }
trap cleanup EXIT INT TERM
cp "$KEY_SRC" "$TMPKEY" && chmod 600 "$TMPKEY"

export GIT_SSH_COMMAND="ssh -i $TMPKEY -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30"

# 用数组保存 git 调用参数，避免仓库路径含空格/中文时被拆分
GIT=(git -C "$REPO_REAL" -c safe.directory=*)

echo "--- 本地 HEAD ---"
"${GIT[@]}" rev-parse --short HEAD
echo "--- 待推送（相对 origin/$BRANCH）---"
if "${GIT[@]}" rev-parse --verify --quiet "origin/$BRANCH" >/dev/null 2>&1; then
  "${GIT[@]}" log --oneline "origin/$BRANCH..HEAD"
else
  echo "  （远端尚无该分支，首次推送）"
fi

echo "--- 推送 origin $BRANCH ---"
if timeout 180 "${GIT[@]}" push origin "$BRANCH" 2>&1; then
  echo "--- 校验 ---"
  local_head=$("${GIT[@]}" rev-parse HEAD)
  remote_head=$(timeout 60 git ls-remote origin "refs/heads/$BRANCH" | cut -f1)
  echo "  本地 HEAD : $local_head"
  echo "  远端 HEAD : $remote_head"
  if [ "$local_head" = "$remote_head" ]; then
    echo "  推送完成，本地与远端一致"
  else
    echo "  **警告**：本地与远端不一致，请检查"
    exit 1
  fi
  "${GIT[@]}" status -sb | head -1
else
  echo "  **推送失败**：检查网络 / 私钥权限 / GitHub 授权"
  exit 1
fi
