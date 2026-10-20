#!/usr/bin/env python3
"""Run step-7 engine lifecycle probes and six frozen, paired E0/E1 repeats."""
import argparse
import asyncio
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
import socket
import subprocess
import sys
import urllib.request

from open_loop import dump, replay

ROOT = Path(__file__).resolve().parents[2]
WSL = Path.home() / "projects/llm-admission-tail-latency-control"
BIN = WSL / "third_party/llama.cpp/build/bin/llama-server"
MODEL = WSL / "models/qwen2.5-1.5b-instruct-q4_k_m/qwen2.5-1.5b-instruct-q4_k_m.gguf"
URL = "http://127.0.0.1:8080"


def fetch(endpoint, target):
    with urllib.request.urlopen(URL + endpoint, timeout=5) as response:
        target.write_bytes(response.read())


async def start_server(out):
    args = [str(BIN), "-m", str(MODEL), "--host", "127.0.0.1", "--port", "8080",
            "-ngl", "99", "-c", "4096", "-np", "2", "-b", "2048", "-ub", "512", "--metrics"]
    dump(out / "server-command.json", args)
    log = (out / "server.log").open("wb")
    proc = await asyncio.create_subprocess_exec(*args, stdout=log, stderr=asyncio.subprocess.STDOUT)
    try:
        for _ in range(120):
            if proc.returncode is not None:
                raise RuntimeError("Server exited during startup")
            try:
                await asyncio.to_thread(fetch, "/health", out / "health.json")
                await asyncio.to_thread(fetch, "/props", out / "props.json")
                return proc, log
            except Exception:
                await asyncio.sleep(1)
        raise RuntimeError("Server not ready within 120 seconds")
    except BaseException:
        await stop_server(proc, log)
        raise


async def stop_server(proc, log):
    if proc.returncode is None:
        proc.send_signal(2)
        try:
            await asyncio.wait_for(proc.wait(), 10)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
    log.close()


def make_traces(cfg):
    rng = random.Random(cfg["seed"])
    topics = ["首 token 延迟", "请求排队", "批处理", "模型推理", "前缀缓存", "吞吐量", "流式响应", "尾延迟"]
    interactive = [{"req_id": f"I{i:03d}", "category": "interactive", "arrival_offset_ms": i * cfg["interactive_interval_ms"],
                    "prompt": f"任务编号 I{i:03d}。请用一句话解释{rng.choice(topics)}。",
                    "max_tokens": cfg["interactive_max_tokens"], "temperature": cfg["temperature"]}
                   for i in range(cfg["interactive_requests"])]
    background = [{"req_id": f"B{i:03d}", "category": "background",
                   "arrival_offset_ms": cfg["background_start_ms"] + i * cfg["background_interval_ms"],
                   "prompt": f"任务编号 B{i:03d}。请分点详细解释大语言模型推理中的{rng.choice(topics)}、输入处理、逐步生成和资源竞争，给出具体例子。",
                   "max_tokens": cfg["background_max_tokens"], "temperature": cfg["temperature"]}
                  for i in range(cfg["background_requests"])]
    return {"E0": interactive, "E1": sorted(interactive + background, key=lambda r: (r["arrival_offset_ms"], r["req_id"]))}


async def run(args):
    gate = json.loads((args.step7_dir / "summary.json").read_text(encoding="utf-8"))
    if gate["status"] != "passed":
        raise ValueError("Step-7 controlled lifecycle/calibration gate not passed")
    cfg = json.loads((ROOT / "configs/prestudy-e0e1.json").read_text(encoding="utf-8"))
    traces = make_traces(cfg)
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=False)
    dump(out / "protocol.json", cfg)
    dump(out / "step7-gate.json", gate)
    dump(out / "batch-status.json", {"status": "running", "started_at": datetime.now().astimezone().isoformat()})
    sources = ["scripts/replay/open_loop.py", "scripts/replay/70_run_prestudy.py", "configs/prestudy-e0e1.json", "tests/integration/verify_open_loop.py"]
    hashes = {}
    for rel in sources:
        dest = out / "source" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = (ROOT / rel).read_bytes()
        dest.write_bytes(data)
        hashes[rel] = hashlib.sha256(data).hexdigest()
    dump(out / "code-sha256.json", hashes)
    engine = subprocess.check_output(["git", "-C", str(WSL / "third_party/llama.cpp"), "rev-parse", "HEAD"], text=True).strip()
    model_hash = hashlib.sha256()
    with MODEL.open("rb") as f:
        while chunk := f.read(1024 * 1024): model_hash.update(chunk)
    environment = {"recorded_at": datetime.now().astimezone().isoformat(), "engine_commit": engine,
                   "model_sha256": model_hash.hexdigest(), "python": sys.version,
                   "project_commit": subprocess.check_output(["git", "-C", str(ROOT), "-c", "safe.directory=*", "rev-parse", "HEAD"], text=True).strip(),
                   "working_tree": subprocess.check_output(["git", "-C", str(ROOT), "-c", "safe.directory=*", "status", "--short"], text=True),
                   "hardware": subprocess.check_output(["nvidia-smi"], text=True)}
    dump(out / "environment.json", environment)
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", 8080)) == 0:
            raise RuntimeError("8080 already in use; refusing to reuse another server")
    sample_log = (out / "gpu-samples.csv").open("wb")
    sampler = await asyncio.create_subprocess_exec("nvidia-smi", "--query-gpu=timestamp,memory.used,utilization.gpu,temperature.gpu,power.draw", "--format=csv", "--loop-ms=1000", stdout=sample_log, stderr=asyncio.subprocess.STDOUT)
    completed = []
    try:
        # Real engine cancellation/timeout/cutoff + recovery, without claiming KV-ledger acceptance.
        probe = out / "engine-lifecycle"
        probe.mkdir()
        server, log = await start_server(probe)
        try:
            await asyncio.to_thread(fetch, "/metrics", probe / "metrics-before.txt")
            rows = [
                {"req_id": "cancel", "category": "background", "arrival_offset_ms": 0, "prompt": "详细介绍大语言模型推理的每个步骤。", "max_tokens": 384, "cancel_after_ms": 150},
                {"req_id": "timeout", "category": "background", "arrival_offset_ms": 0, "prompt": "详细介绍批处理和资源管理。", "max_tokens": 384, "timeout_seconds": .1},
                {"req_id": "cutoff", "category": "background", "arrival_offset_ms": 0, "prompt": "详细解释缓存管理和请求调度。", "max_tokens": 384},
                {"req_id": "notreleased", "category": "interactive", "arrival_offset_ms": 0, "release_offset_ms": 2000, "prompt": "你好", "max_tokens": 8}]
            result = await replay(rows, probe / "faults", URL, .3, .2)
            assert result["terminal_counts"] == {"completed": 0, "rejected": 0, "timeout": 1, "cancelled": 1, "failed": 0, "unfinished": 2}, result
            await asyncio.sleep(1)
            recover = [{"req_id": f"recover{i}", "category": "interactive", "arrival_offset_ms": i * 500, "prompt": "请回复：恢复正常。", "max_tokens": 16} for i in range(2)]
            recovery = await replay(recover, probe / "recovery", URL, 1, 10)
            assert recovery["terminal_counts"]["completed"] == 2
            await asyncio.sleep(.2)
            await asyncio.to_thread(fetch, "/metrics", probe / "metrics-after.txt")
            dump(probe / "summary.json", {"status": "passed", "faults": result["terminal_counts"], "recovery_completed": 2,
                                          "scope": "Client sockets stopped and real engine accepted recovery; not formal KV-ledger acceptance"})
            print("STEP7 real-engine cancellation/timeout/cutoff/recovery PASS", flush=True)
        finally:
            await stop_server(server, log)
        for repeat, order in enumerate(cfg["execution_order"], 1):
            for scenario in order:
                run_dir = out / f"r{repeat}-{scenario}"
                run_dir.mkdir()
                manifest = json.loads((ROOT / "configs/run-manifest.template.json").read_text(encoding="utf-8"))
                manifest.update({"status": "running", "run_id": f"{out.name}/r{repeat}-{scenario}", "stage": "prestudy", "scenario": scenario,
                                 "strategy": "original_engine", "recorded_at": datetime.now().astimezone().isoformat(),
                                 "project_commit": environment["project_commit"], "engine_commit": engine, "model_sha256": model_hash.hexdigest(),
                                 "environment_record": "../environment.json", "seed": cfg["seed"], "repeat_index": repeat,
                                 "execution_order": order, "clock_domain": "client time.monotonic", "time_unit": "ms",
                                 "measurement_seconds": cfg["measurement_seconds"], "drain_deadline_seconds": cfg["drain_seconds"],
                                 "sampling_config": {"temperature": 0, "stream": True}, "cache_config": cfg["cache_protocol"],
                                 "workload_path": "replay/trace.jsonl", "client_log": "replay/client.jsonl", "server_log": "server.log",
                                 "denominator_rule": "Actual sends before measurement cutoff; all planned states also reported",
                                 "notes": "Exploratory synthetic prestudy; 2 warmups; fresh server; missing server segment clocks unknown. Drain is 180s after 100s measurement. Code snapshot in parent source/."})
                dump(run_dir / "manifest.json", manifest)
                print(f"START r{repeat}-{scenario}: {len(traces[scenario])} requests", flush=True)
                server, log = await start_server(run_dir)
                try:
                    warmup = [{"req_id": f"warm{i}", "category": "interactive", "arrival_offset_ms": i * 500, "prompt": "请回复：准备好了。", "max_tokens": 16} for i in range(2)]
                    warmed = await replay(warmup, run_dir / "warmup", URL, 1, 10)
                    assert warmed["terminal_counts"]["completed"] == 2
                    await asyncio.to_thread(fetch, "/metrics", run_dir / "metrics-before.txt")
                    summary = await replay(traces[scenario], run_dir / "replay", URL, cfg["measurement_seconds"], cfg["drain_seconds"], cfg["request_timeout_seconds"], cfg["send_lag_limit_ms"])
                    await asyncio.to_thread(fetch, "/metrics", run_dir / "metrics-after.txt")
                    manifest["status"] = "completed"
                    manifest["workload_sha256"] = hashlib.sha256((run_dir / "replay/trace.jsonl").read_bytes()).hexdigest()
                    manifest["server_command"] = json.loads((run_dir / "server-command.json").read_text())
                    manifest["client_command"] = "python3 scripts/replay/70_run_prestudy.py (see frozen protocol.json)"
                    manifest["engine_config"] = {"n_ctx": 4096, "slots": 2, "gpu_layers": 99, "batch": 2048, "ubatch": 512}
                    dump(run_dir / "manifest.json", manifest)
                    completed.append({"repeat": repeat, "scenario": scenario, "summary": summary})
                    print(f"DONE r{repeat}-{scenario}: terminals={summary['terminal_counts']} lag_max={summary['send_lag_max_ms']:.3f}ms interactive_P95={summary['classes']['interactive']['ttft_p95_ms']}", flush=True)
                finally:
                    await stop_server(server, log)
        dump(out / "batch-summary.json", completed)
        dump(out / "batch-status.json", {"status": "completed", "finished_at": datetime.now().astimezone().isoformat(), "runs": len(completed)})
    except BaseException as exc:
        dump(out / "batch-status.json", {"status": "failed", "finished_at": datetime.now().astimezone().isoformat(), "runs_completed": len(completed), "error": repr(exc)})
        raise
    finally:
        if sampler.returncode is None:
            sampler.terminate()
            await sampler.wait()
        sample_log.close()
        (out / "gpu-after.txt").write_text(subprocess.check_output(["nvidia-smi"], text=True), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--step7-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    asyncio.run(run(ap.parse_args()))
