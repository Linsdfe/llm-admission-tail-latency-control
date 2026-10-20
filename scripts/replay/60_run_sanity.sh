#!/usr/bin/env bash
# Run the existing synthetic trace in a fresh evidence directory; verify results.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
export PYTHONIOENCODING=utf-8
export LC_ALL=C.UTF-8

WIN="$HOME/proj-win"
REPO="$HOME/projects/llm-admission-tail-latency-control"
BIN="$REPO/third_party/llama.cpp/build/bin/llama-server"
MODEL="$REPO/models/qwen2.5-1.5b-instruct-q4_k_m/qwen2.5-1.5b-instruct-q4_k_m.gguf"
RUN="$WIN/results/raw/prestudy/$(date +%Y%m%d-%H%M%S)-sanity-rerun-$$"
test -x "$BIN"
test -f "$MODEL"
mkdir "$RUN"
mkdir "$RUN/http"
printf 'RUN=%s\n' "$RUN"

cleanup() {
  rc=$?
  trap - EXIT
  if [ -n "${SERVER_PID:-}" ]; then
    kill -INT "$SERVER_PID" 2>/dev/null || true
    for i in $(seq 1 10); do
      if ! kill -0 "$SERVER_PID" 2>/dev/null; then break; fi
      sleep 1
    done
    if kill -0 "$SERVER_PID" 2>/dev/null; then
      kill -KILL "$SERVER_PID" 2>/dev/null || true
    fi
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  nvidia-smi > "$RUN/gpu-after.txt" 2>&1 || true
  printf 'exit_code=%s\nfinished_at=%s\n' "$rc" "$(date -Is)" > "$RUN/run-status.txt"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$WIN"
python3 - <<'PY'
import socket
with socket.socket() as s:
    s.settimeout(1)
    if s.connect_ex(("127.0.0.1", 8080)) == 0:
        raise SystemExit("Port 8080 is occupied; refusing to reuse an unknown server")
PY
bash scripts/ops/sync.sh > "$RUN/sync.txt"
timeout 30s python3 "$REPO/scripts/replay/30_replay_dry_run.py" \
  --trace "$REPO/data/workloads/prestudy-sanity-e1.jsonl" \
  --out-dir "$RUN/dry-run" --response-ms 2000 \
  | tee "$RUN/dry-run-console.txt"

{
  date -Is
  python3 --version
  git -C "$REPO/third_party/llama.cpp" rev-parse HEAD
  sha256sum "$MODEL"
  git -C "$WIN" -c safe.directory='*' rev-parse HEAD
  git -C "$WIN" -c safe.directory='*' status --short
} > "$RUN/environment.txt"
sha256sum "$REPO/scripts/replay/"{10_make_trace,30_replay_dry_run,40_replay_sanity,stream_client}.py \
  > "$RUN/code-sha256.txt"
nvidia-smi > "$RUN/gpu-before.txt"
ARGS=("$BIN" -m "$MODEL" --host 127.0.0.1 --port 8080 -ngl 99 -c 4096 -np 2 -b 2048 -ub 512 --metrics)
printf '%q ' "${ARGS[@]}" > "$RUN/http/server-command.txt"
printf '\n' >> "$RUN/http/server-command.txt"
"${ARGS[@]}" > "$RUN/http/server.log" 2>&1 &
SERVER_PID=$!
ready=0
for i in $(seq 1 120); do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    tail -40 "$RUN/http/server.log"
    exit 1
  fi
  if curl -fsS http://127.0.0.1:8080/health > "$RUN/http/health.json" 2>/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then echo 'Server startup timed out'; exit 1; fi
curl -fsS http://127.0.0.1:8080/props > "$RUN/http/props.json"
cp "$REPO/data/workloads/prestudy-sanity-e0.jsonl" "$RUN/paired-e0-trace.jsonl"
timeout 180s python3 "$REPO/scripts/replay/40_replay_sanity.py" \
  --trace "$REPO/data/workloads/prestudy-sanity-e1.jsonl" --out-dir "$RUN/http" \
  2>&1 | tee "$RUN/http/client-console.txt"
curl -fsS http://127.0.0.1:8080/metrics > "$RUN/http/metrics-after.txt"
python3 "$WIN/scripts/analysis/10_check_sanity.py" --run-dir "$RUN"
