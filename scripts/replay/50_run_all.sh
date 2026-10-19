#!/usr/bin/env bash
# P0 冒烟 v2 编排：同步脚本/文档 → 起服务 → 预热+测量 → GPU 采样 → 停服务
# 用法：wsl.exe -d Ubuntu-24.04 -e bash <本文件绝对路径>
set -uo pipefail

WIN_REPO="$HOME/proj-win"
REPO="$HOME/projects/llm-admission-tail-latency-control"
RUN="$WIN_REPO/results/raw/prestudy/2026-10-19-smoke-v2"
MODEL="$REPO/models/qwen2.5-1.5b-instruct-q4_k_m/qwen2.5-1.5b-instruct-q4_k_m.gguf"
BIN="$REPO/third_party/llama.cpp/build/bin/llama-server"
PORT=8080

mkdir -p "$RUN/requests"
cd "$REPO/third_party/llama.cpp" || { echo "FATAL: 引擎目录不存在"; exit 1; }
for f in "$MODEL" "$BIN"; do
  [ -f "$f" ] || { echo "FATAL: 缺少文件 $f"; exit 1; }
done

echo "===== 开始 $(date -Is) ====="

# 0) 以 Windows 工作区为准，同步文档与脚本到 WSL 副本（统一走 ops/sync.sh，避免两处维护同一逻辑）
bash "$WIN_REPO/scripts/ops/sync.sh" | tail -2

echo "[0] 同步完成，开始启动服务"

# 1) 启动服务
: > "$RUN/server.log"
nohup "$BIN" \
  -m "$MODEL" \
  --host 127.0.0.1 --port "$PORT" \
  -ngl 99 -c 4096 -np 2 -b 2048 -ub 512 \
  --metrics \
  >> "$RUN/server.log" 2>&1 &
SERVER_PID=$!
# PID 记录在 run-meta.txt（不再单独产生 server.pid 临时文件）
echo "[1] llama-server 已启动 PID=$SERVER_PID"

# 2) 等待健康检查
READY=0
for i in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then READY=$i; break; fi
  sleep 1
done
if [ "$READY" -eq 0 ]; then
  echo "[2] FATAL: 服务 120s 内未就绪"
  tail -40 "$RUN/server.log"
  kill -9 "$SERVER_PID" 2>/dev/null
  exit 1
fi
echo "[2] 服务就绪（约 ${READY}s）"
curl -fsS "http://127.0.0.1:$PORT/health" | tee "$RUN/health.json"; echo
curl -fsS "http://127.0.0.1:$PORT/props" > "$RUN/props.json" 2>/dev/null && echo "[2] /props 已保存"

# 3) 运行前 GPU 快照 + 采样器
nvidia-smi --query-gpu=timestamp,memory.used,memory.total,utilization.gpu --format=csv,noheader \
  | tee "$RUN/gpu-before.txt"
( while true; do
    nvidia-smi --query-gpu=timestamp,memory.used,memory.total,utilization.gpu --format=csv,noheader
    sleep 0.2
  done ) > "$RUN/gpu-samples.csv" 2>/dev/null &
SAMPLER_PID=$!
sleep 1
echo "[3] GPU 采样器 PID=$SAMPLER_PID"

# 4) 预热 + 三条测量请求（逐块时间戳）
export PYTHONDONTWRITEBYTECODE=1
START_MS=$(date +%s%3N)
python3 "$REPO/scripts/replay/20_run_smoke.py" \
  --base-url "http://127.0.0.1:$PORT" \
  --out-dir "$RUN" 2>&1 | tee "$RUN/client-console.txt"
RC=${PIPESTATUS[0]}
curl -fsS "http://127.0.0.1:$PORT/metrics" > "$RUN/metrics-after.txt" 2>/dev/null && echo "[4] /metrics 已保存"
END_MS=$(date +%s%3N)
echo "[4] 请求阶段结束 rc=$RC 耗时 $((END_MS - START_MS)) ms"

# 5) 停采样器 + 运行后快照
kill "$SAMPLER_PID" 2>/dev/null
wait "$SAMPLER_PID" 2>/dev/null
nvidia-smi > "$RUN/gpu-after-run.txt" 2>&1
echo "[5] GPU 采样结束（$(wc -l < "$RUN/gpu-samples.csv") 行）"

# 6) 停服务
kill -INT "$SERVER_PID" 2>/dev/null
for i in 1 2 3 4 5; do kill -0 "$SERVER_PID" 2>/dev/null || break; sleep 1; done
kill -9 "$SERVER_PID" 2>/dev/null
sleep 1
echo "[6] 服务已停止；残留进程检查：$(pgrep -c llama-server || true)"

# 7) 收尾记录
{
  echo "run_id=2026-10-19-smoke-v2"
  echo "started_at=$START_MS"
  echo "finished_at=$END_MS"
  echo "runs_ms=$((END_MS - START_MS))"
  echo "server_pid=$SERVER_PID"
  echo "rc=$RC"
  echo "date=$(date -Is)"
  echo "uptime=$(uptime)"
} > "$RUN/run-meta.txt"
echo "===== 结束 $(date -Is) ====="
