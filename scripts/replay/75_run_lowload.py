#!/usr/bin/env python3
"""Versioned six-run supplement. Never overwrite previous evidence."""
import argparse
import asyncio
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

from open_loop import dump, replay

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/analysis"))
from lowload_evidence import audit_run, read, jsonlines

spec = importlib.util.spec_from_file_location("original_prestudy", ROOT / "scripts/replay/70_run_prestudy.py")
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def traces_from_source(cfg):
    source = ROOT / cfg["source_batch"]
    control = jsonlines(source / "r1-E0/replay/trace.jsonl")
    mixed = jsonlines(source / "r1-E1/replay/trace.jsonl")
    assert [r for r in mixed if r["category"] == "interactive"] == control
    background = [r for r in mixed if r["req_id"] in cfg["background_ids"]]
    assert [r["req_id"] for r in background] == cfg["background_ids"]
    assert [r["arrival_offset_ms"] for r in background] == [250 + 10000 * i for i in range(10)]
    assert len(control) == 200 and len(background) == 10
    return {"C": control, "L": sorted(control + background, key=lambda r: (r["arrival_offset_ms"], r["req_id"]))}


def capture_environment(out):
    for name, cmd in {
        "gpu.txt": ["nvidia-smi"],
        "gpu-processes.txt": ["nvidia-smi", "--query-compute-apps=pid,process_name,used_gpu_memory", "--format=csv"],
        "processes.txt": ["ps", "-eo", "pid,comm,pcpu,pmem", "--sort=-pcpu"],
        "memory.txt": ["free", "-b"],
        "power.txt": ["powershell.exe", "-NoProfile", "-Command", "powercfg /getactivescheme"],
        "host-processes.txt": ["powershell.exe", "-NoProfile", "-Command", "Get-Process | Sort-Object CPU -Descending | Select-Object -First 25 ProcessName,Id,CPU,WorkingSet | ConvertTo-Json -Compress"],
    }.items():
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
        (out / name).write_bytes(r.stdout)
        if r.returncode:
            raise RuntimeError(f"Environment capture failed: {name}")
    processes = subprocess.check_output(["nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader"], text=True).strip()
    if processes:
        raise RuntimeError("Another GPU compute process is present; retain evidence and pause")


async def run(args):
    cfg = read(ROOT / "configs/prestudy-lowload-v1.json")
    traces = traces_from_source(cfg)
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    dump(out / "protocol.json", cfg)
    dump(out / "batch-status.json", {"status": "preparing", "started_at": datetime.now().astimezone().isoformat()})
    sources = ["scripts/replay/open_loop.py", "scripts/replay/70_run_prestudy.py", "scripts/replay/75_run_lowload.py",
               "scripts/analysis/lowload_evidence.py", "scripts/analysis/85_analyze_lowload.py", "tests/integration/verify_open_loop.py",
               "tests/unit/test_lowload_evidence.py", "configs/prestudy-lowload-v1.json", "docs/prestudy-lowload-protocol-v1.md"]
    hashes = {}
    for rel in sources:
        data = (ROOT / rel).read_bytes()
        dest = out / "source" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        hashes[rel] = hashlib.sha256(data).hexdigest()
    dump(out / "code-sha256.json", hashes)
    source = ROOT / cfg["source_batch"]
    provenance = {}
    input_budgets = {}
    for scenario, trace in traces.items():
        input_budgets[scenario] = []
        for row in trace:
            original = source / ("r1-E1" if row["category"] == "background" else "r1-E0") / "replay/requests" / (row["req_id"] + ".json")
            old = read(original)
            assert all(old[k] == v for k, v in row.items())
            n = old["timings"]["prompt_n"] + old["timings"]["cache_n"]
            assert n + row["max_tokens"] <= 2048
            input_budgets[scenario].append({"req_id": row["req_id"], "historical_input_tokens": n, "max_tokens": row["max_tokens"]})
    for rel in ("r1-E0/replay/trace.jsonl", "r1-E1/replay/trace.jsonl", "environment.json"):
        original = source / rel
        dest = out / "input-source" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(original.read_bytes())
        provenance[rel] = sha(original)
    dump(out / "input-source-sha256.json", provenance)
    dump(out / "historical-input-budgets.json", {"scope": "Historical matched request counts, not newly tokenized measurements; actual new lengths recorded separately", "scenarios": input_budgets})
    completed = []
    sampler = sample_log = None
    try:
        gate = read(args.gate_dir / "summary.json")
        assert gate["status"] == "passed"
        dump(out / "client-gate.json", {"directory": str(args.gate_dir), "summary": gate})
        actual_engine = subprocess.check_output(["git", "-C", str(engine.WSL / "third_party/llama.cpp"), "rev-parse", "HEAD"], text=True).strip()
        actual_model = await asyncio.to_thread(sha, engine.MODEL)
        assert actual_engine == cfg["engine_commit"] and actual_model == cfg["model_sha256"], "Version mismatch"
        env = {"recorded_at": datetime.now().astimezone().isoformat(), "engine_commit": actual_engine,
               "model_sha256": actual_model, "python": sys.version,
               "project_commit": subprocess.check_output(["git", "-C", str(ROOT), "-c", "safe.directory=*", "rev-parse", "HEAD"], text=True).strip(),
               "working_tree": subprocess.check_output(["git", "-C", str(ROOT), "-c", "safe.directory=*", "status", "--short"], text=True),
               "scope": "Uncommitted execution tree; source/ and code-sha256.json identify executable inputs"}
        dump(out / "environment.json", env)
        for name, trace in traces.items():
            (out / f"trace-{name}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in trace), encoding="utf-8")
        sample_log = (out / "gpu-samples.csv").open("wb")
        sampler = await asyncio.create_subprocess_exec("nvidia-smi", "--query-gpu=timestamp,memory.used,utilization.gpu,temperature.gpu,power.draw", "--format=csv", "--loop-ms=1000", stdout=sample_log, stderr=asyncio.subprocess.STDOUT)
        dump(out / "batch-status.json", {"status": "running", "started_at": env["recorded_at"]})
        for repeat, order in enumerate(cfg["execution_order"], 1):
            for scenario in order:
                if time.monotonic() - started >= cfg["batch_start_limit_seconds"]:
                    raise RuntimeError("60-minute start limit reached")
                if shutil.disk_usage(out).free < cfg["min_free_bytes"] or sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) >= cfg["max_batch_bytes"]:
                    raise RuntimeError("Disk budget reached")
                with socket.socket() as sock:
                    if sock.connect_ex(("127.0.0.1", 8080)) == 0:
                        raise RuntimeError("8080 occupied; refusing to reuse server")
                directory = out / f"r{repeat}-{scenario}"
                directory.mkdir()
                print(f"IDLE r{repeat}-{scenario}: {cfg['idle_seconds']} seconds", flush=True)
                capture_environment(directory)
                await asyncio.sleep(cfg["idle_seconds"])
                subprocess.run(["nvidia-smi"], stdout=(directory / "gpu-after-idle.txt").open("wb"), check=True)
                manifest = {"status": "running", "run_id": f"{out.name}/r{repeat}-{scenario}", "stage": "prestudy", "scenario": scenario,
                            "repeat": repeat, "order": order, "started_at": datetime.now().astimezone().isoformat(),
                            "strategy": "original_engine", "engine_commit": actual_engine, "model_sha256": actual_model,
                            "project_commit": env["project_commit"], "code_snapshot": "../source", "clock": cfg["timing"], "seed": cfg["seed"]}
                dump(directory / "manifest.json", manifest)
                server = log = None
                async def startup_warmup():
                    nonlocal server, log
                    server, log = await engine.start_server(directory)
                    warm = [{"req_id": f"warm{i}", "category": "interactive", "arrival_offset_ms": i * 500,
                             "prompt": "请回复：准备好了。", "max_tokens": 16} for i in range(2)]
                    result = await replay(warm, directory / "warmup", engine.URL, 1, 10)
                    assert result["terminal_counts"]["completed"] == 2
                try:
                    await asyncio.wait_for(startup_warmup(), cfg["startup_warmup_timeout_seconds"])
                    await asyncio.to_thread(engine.fetch, "/metrics", directory / "metrics-before.txt")
                    print(f"START r{repeat}-{scenario}: {len(traces[scenario])} requests", flush=True)
                    summary = await replay(traces[scenario], directory / "replay", engine.URL, cfg["measurement_seconds"], cfg["drain_seconds"], cfg["request_timeout_seconds"], cfg["send_lag_limit_ms"])
                    await asyncio.sleep(.2)
                    await asyncio.to_thread(engine.fetch, "/metrics", directory / "metrics-after.txt")
                finally:
                    if server is not None:
                        await engine.stop_server(server, log)
                        manifest["server_exit_code"] = server.returncode
                audit, _ = audit_run(directory, traces[scenario], cfg)
                dump(directory / "audit.json", audit)
                manifest.update(status="completed" if not audit["flags"] else "needs_review", finished_at=datetime.now().astimezone().isoformat(),
                                workload_sha256=sha(directory / "replay/trace.jsonl"), server_command=read(directory / "server-command.json"))
                dump(directory / "manifest.json", manifest)
                completed.append({"repeat": repeat, "scenario": scenario, "audit": audit})
                dump(out / "batch-summary.json", completed)
                print(f"DONE r{repeat}-{scenario}: states={summary['terminal_counts']} lag={summary['send_lag_max_ms']:.3f}ms P95={summary['classes']['interactive']['ttft_p95_ms']:.3f}ms flags={audit['flags']}", flush=True)
                if audit["flags"]:
                    raise RuntimeError("Integrity gate failed; no subsequent runs started")
        dump(out / "batch-status.json", {"status": "completed", "finished_at": datetime.now().astimezone().isoformat(), "runs": len(completed), "elapsed_seconds": time.monotonic() - started})
    except BaseException as exc:
        dump(out / "batch-status.json", {"status": "failed", "finished_at": datetime.now().astimezone().isoformat(), "runs_completed": len(completed), "error": repr(exc)})
        raise
    finally:
        if sampler is not None and sampler.returncode is None:
            sampler.terminate()
            await sampler.wait()
        if sample_log is not None:
            sample_log.close()
        subprocess.run(["nvidia-smi"], stdout=(out / "gpu-after.txt").open("wb"), check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
