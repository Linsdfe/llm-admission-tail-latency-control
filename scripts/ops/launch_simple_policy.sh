#!/usr/bin/env bash
# Keep the authorized local collection alive if the chat command is interrupted.
set -euo pipefail
task_root="$(cd "$(dirname "$0")/../.." && pwd)"
task_batch="${1:?new batch directory required}"
task_launch="${2:?new launcher evidence directory required}"
task_client_gate="${3:?client gate directory required}"
task_engine_gate="${4:?engine gate directory required}"
test ! -e "$task_batch"
mkdir "$task_launch"
task_command=(python3 "$task_root/scripts/replay/76_run_simple_policy.py"
  --config "$task_root/configs/prestudy-simple-policy-v1.json"
  --gate-dir "$task_client_gate" --engine-gate-dir "$task_engine_gate"
  --out-dir "$task_batch"
  --engine-bin /home/aiinfra/projects/llm-admission-tail-latency-control/third_party/llama.cpp-simple-policy-v1-20261022/build/bin/llama-server)
printf '%q ' "${task_command[@]}" > "$task_launch/command.txt"
printf '\n' >> "$task_launch/command.txt"
cp "$0" "$task_launch/launcher-source.sh"
sha256sum "$task_launch/launcher-source.sh" > "$task_launch/launcher-source.sha256"
date --iso-8601=seconds > "$task_launch/launched-at.txt"
nohup "${task_command[@]}" > "$task_launch/runner.log" 2>&1 < /dev/null &
task_pid=$!
printf '%s\n' "$task_pid" > "$task_launch/pid.txt"
printf 'Started local collection with PID %s\n' "$task_pid"
