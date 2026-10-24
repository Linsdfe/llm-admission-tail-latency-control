#!/usr/bin/env python3
"""Append-only three-strategy background running-cap experiment.

Run under WSL, after controlled-client and real-engine policy probes. Performance
failures are retained as outcomes; integrity/capacity failures stop later runs.
"""
import argparse
import asyncio
from datetime import datetime
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

from simple_policy_replay_v2 import dump, replay, percentile, STATES

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = Path.home() / "projects/llm-admission-tail-latency-control/models/qwen2.5-1.5b-instruct-q4_k_m/qwen2.5-1.5b-instruct-q4_k_m.gguf"
URL = "http://127.0.0.1:8080"
spec = importlib.util.spec_from_file_location("lowload_runner", ROOT / "scripts/replay/75_run_lowload.py")
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def jsonlines(path):
    return [json.loads(s) for s in Path(path).read_text(encoding="utf-8").splitlines() if s.strip()]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def binary_inventory(engine_bin):
    paths = [engine_bin] + sorted(engine_bin.parent.glob("lib*.so*"))
    return {p.name: {"path": str(p), "realpath": str(p.resolve(strict=True)), "sha256": sha(p)} for p in paths if p.is_file()}


def git_output(directory, *args):
    return subprocess.check_output(["git", "-C", str(directory), "-c", "safe.directory=*", *args], text=True)


def make_traces(cfg):
    source = ROOT / cfg["source_batch"]
    inter = jsonlines(source / "r1-E0/replay/trace.jsonl")
    mixed = jsonlines(source / "r1-E1/replay/trace.jsonl")
    assert len(inter) == 200 and [r for r in mixed if r["category"] == "interactive"] == inter
    backgrounds = sorted((r for r in mixed if r["category"] == "background"), key=lambda r: r["arrival_offset_ms"])
    assert len(backgrounds) == 100
    indices = [math.floor(k * 100 / 30) for k in range(30)]
    traces = {}
    for scenario in ("steady", "burst"):
        selected = []
        for k, idx in enumerate(indices):
            row = dict(backgrounds[idx])
            row["arrival_offset_ms"] = 250 + k * (100000 / 30) if scenario == "steady" else (k // 3) * 10000 + 250 + (k % 3) * 50
            selected.append(row)
        traces[scenario] = sorted(inter + selected, key=lambda r: (r["arrival_offset_ms"], r["req_id"]))
        assert len(traces[scenario]) == 230
    # Apart from declared arrivals, both scenarios contain exactly the same work.
    strip = lambda rows: sorted(({k: v for k, v in r.items() if k != "arrival_offset_ms"} for r in rows), key=lambda r: r["req_id"])
    assert strip(traces["steady"]) == strip(traces["burst"])
    return traces, indices


def fetch(endpoint, target):
    with urllib.request.urlopen(URL + endpoint, timeout=5) as response:
        target.write_bytes(response.read())


def policy_environment(strategy, aging_ms):
    assert strategy in ("original", "simple", "capped"), strategy
    return {"LLAMA_SERVER_SIMPLE_SCHEDULER": "0" if strategy == "original" else "1",
            "LLAMA_SERVER_BACKGROUND_AGING_MS": str(aging_ms),
            "LLAMA_SERVER_MAX_RUNNING_BACKGROUND": "1" if strategy == "capped" else "0"}


async def start_server(directory, engine_bin, model, strategy, aging_ms):
    command = [str(engine_bin), "-m", str(model), "--host", "127.0.0.1", "--port", "8080",
               "-ngl", "99", "-c", "4096", "-np", "2", "-b", "2048", "-ub", "512", "--metrics"]
    policy_env = policy_environment(strategy, aging_ms)
    dump(directory / "server-command.json", command)
    dump(directory / "server-policy-environment.json", policy_env)
    log = (directory / "server.log").open("xb")
    proc = await asyncio.create_subprocess_exec(*command, env=dict(os.environ, **policy_env), stdout=log, stderr=asyncio.subprocess.STDOUT)
    try:
        for _ in range(120):
            if proc.returncode is not None:
                raise RuntimeError("Server exited during startup")
            try:
                await asyncio.to_thread(fetch, "/health", directory / "health.json")
                await asyncio.to_thread(fetch, "/props", directory / "props.json")
                return proc, log
            except Exception:
                await asyncio.sleep(1)
        raise RuntimeError("Server not ready within 120 seconds")
    except BaseException:
        await prior.engine.stop_server(proc, log)
        raise


def metric_zero(path):
    text = path.read_text(encoding="utf-8")
    result = {}
    for name in ("llamacpp:requests_processing", "llamacpp:requests_deferred"):
        values = [float(line.split()[1]) for line in text.splitlines() if line.startswith(name + " ")]
        result[name] = bool(values) and all(v == 0 for v in values)
    return result


def audit_run(directory, expected, cfg):
    """No outcome-based exclusions: only recording and safety checks are gates."""
    trace = jsonlines(directory / "replay/trace.jsonl")
    rows = jsonlines(directory / "replay/client.jsonl")
    summary = read(directory / "replay/summary.json")
    assert summary["schema_version"] == 4 and summary["client_variant"] == "simple_policy_replay_v2"
    assert summary["persistence"]["recorded_requests"] == summary["persistence"]["written_requests"] == summary["planned"]
    assert trace == expected
    assert len(rows) == len(expected) == summary["planned"]
    by_id = {r["req_id"]: r for r in expected}
    assert len({r["req_id"] for r in rows}) == len(expected)
    assert {r["req_id"] for r in rows} == set(by_id)
    assert len(list((directory / "replay/requests").glob("*.json"))) == len(rows)
    flags = []
    for r in rows:
        assert read(directory / "replay/requests" / (r["req_id"] + ".json")) == r
        assert r["terminal"] in STATES
        assert all(r[k] == v for k, v in by_id[r["req_id"]].items())
        assert r["request_class"] == r["category"] and r["client_req_id"] == r["req_id"]
        assert r["end_offset_ms"] >= 0
        assert abs(r["completion_from_planned_ms"] - r["end_offset_ms"] + r["arrival_offset_ms"]) < .001
        sent = r["actual_send_offset_ms"]
        if sent is not None:
            assert r["end_offset_ms"] >= sent >= r["arrival_offset_ms"]
            assert abs(r["send_lag_ms"] - sent + r.get("release_offset_ms", r["arrival_offset_ms"])) < .001
        first = r["first_content_offset_ms"]
        if first is not None:
            assert sent <= first <= r["end_offset_ms"]
            assert abs(r["ttft_ms"] - first + sent) < .001
            assert abs(r["total_first_ms"] - first + r["arrival_offset_ms"]) < .001
        if r["terminal"] == "completed":
            assert r["http_status"] == 200 and r["stream_done"] and r["finish_reason"] in ("stop", "length")
            assert r["error"] is None and first is not None
            assert sum(c["chars"] for c in r["chunk_series"]) == len(r["content"])
            assert r["chunk_series"][0]["offset_ms"] == first
            timing = r.get("timings") or {}
            if not all(timing.get(k) is not None for k in ("prompt_n", "cache_n", "predicted_n")):
                flags.append(f"{r['req_id']}: missing length accounting")
            else:
                assert 0 <= timing["predicted_n"] <= r["max_tokens"]
                assert timing["prompt_n"] + timing["cache_n"] + r["max_tokens"] <= 2048
    for state in STATES:
        assert sum(r["terminal"] == state for r in rows) == summary["terminal_counts"][state]
    assert sum(summary["terminal_counts"].values()) == len(rows)
    for category in ("interactive", "background"):
        complete = [r for r in rows if r["terminal"] == "completed" and r["category"] == category]
        for field, key in (("ttft_ms", "ttft_p95_ms"), ("total_first_ms", "total_first_p95_ms")):
            assert percentile([r[field] for r in complete if r[field] is not None], 95) == summary["classes"][category][key]
    if summary["late_send_count"] or summary["sent"] != len(trace) or summary["actual_send_window_denominator"] != len(trace):
        flags.append("send timing/window mismatch")
    log = (directory / "server.log").read_text(encoding="utf-8", errors="replace")
    strategy = read(directory / "server-policy-environment.json")
    expected_mode = strategy["LLAMA_SERVER_SIMPLE_SCHEDULER"]
    expected_cap = strategy["LLAMA_SERVER_MAX_RUNNING_BACKGROUND"]
    config_lines = [line for line in log.splitlines() if "SCHED_CONFIG" in line]
    if not any(f"mode={expected_mode}" in line and f"max_running_background={expected_cap}" in line for line in config_lines):
        flags.append("server policy configuration not confirmed")
    cap_event_ids = set()
    running_background = 0
    active = {}
    last_event_ms = -1
    for line in log.splitlines():
        if "SCHED_CAP_LAUNCH" not in line and "SCHED_CAP_RELEASE" not in line:
            continue
        fields = dict(v.split("=", 1) for v in line.split() if "=" in v)
        event_ms = int(fields["event_ms"])
        if event_ms < last_event_ms or fields["cap"] != expected_cap:
            flags.append("server cap event timing/configuration mismatch")
        last_event_ms = event_ms
        before, after = (int(fields[k]) for k in ("running_background_before", "running_background_after"))
        slot = int(fields["slot"])
        if before != running_background:
            flags.append("server background accounting discontinuity")
        if "SCHED_CAP_LAUNCH" in line:
            if slot in active:
                flags.append("launch on active slot")
            active[slot] = fields
            cap_event_ids.add(fields["clientid"])
            expected_after = before + (fields["class"] == "background")
        else:
            previous = active.pop(slot, None)
            if previous is None or previous["id"] != fields["id"] or previous["clientid"] != fields["clientid"] or previous["class"] != fields["class"]:
                flags.append("release without matching active task")
            expected_after = before - (fields["class"] == "background")
        if after != expected_after or after < 0:
            flags.append("server background accounting delta mismatch")
        if expected_cap == "1" and max(before, after) > 1:
            flags.append("background running cap violated")
        running_background = after
    if active or running_background != 0:
        flags.append("engine slots not released at recording end")
    if "n_slots = 2, n_ctx_slot = 2048" not in log:
        flags.append("slot capacity not confirmed")
    for error in ("Context size has been exceeded", "failed to allocate buffer", "truncated = 1", "out of memory", "CUDA error"):
        if error.lower() in log.lower():
            flags.append(error)
    metrics = metric_zero(directory / "metrics-after.txt")
    if not all(metrics.values()):
        flags.append("server not empty after client terminal states and recovery grace")
    enqueued = set()
    launched = set()
    for line in log.splitlines():
        if "SCHED_ENQUEUE" not in line and "SCHED_LAUNCH" not in line:
            continue
        fields = dict(v.split("=", 1) for v in line.split() if "=" in v)
        req_id = fields.get("clientid")
        if req_id in by_id:
            if fields.get("class") != by_id[req_id]["category"]:
                flags.append(f"{req_id}: server class mismatch")
            if "SCHED_ENQUEUE" in line:
                enqueued.add(req_id)
            if "SCHED_LAUNCH" in line:
                if req_id in launched:
                    flags.append(f"{req_id}: duplicate server launch")
                launched.add(req_id)
    sent_ids = {r["req_id"] for r in rows if r["actual_send_offset_ms"] is not None and r["http_status"] == 200}
    if not sent_ids.issubset(enqueued):
        flags.append("successful HTTP IDs missing server enqueue evidence")
    complete_ids = {r["req_id"] for r in rows if r["terminal"] == "completed"}
    if not complete_ids.issubset(launched):
        flags.append("completed IDs missing server launch evidence")
    if not complete_ids.issubset(cap_event_ids):
        flags.append("completed IDs missing server cap launch evidence")
    return {"flags": sorted(set(flags)), "terminal_counts": summary["terminal_counts"],
            "performance_noncompleted": len(rows) - summary["terminal_counts"]["completed"],
            "metrics_zero": metrics, "cap_event_measurement_ids": len(cap_event_ids & set(by_id)),
            "final_running_background": running_background, "final_active_slots": len(active), "enqueued_measurement_ids": len(enqueued), "launched_measurement_ids": len(launched),
            "scope": "Recording/capacity checks; performance timeouts retained and do not alone stop later runs"}


async def run(args):
    cfg_path = args.config.resolve()
    cfg = read(cfg_path)
    traces, selected_indices = make_traces(cfg)
    assert len(cfg["execution_order"]) == cfg["max_measurement_runs"] == 18
    assert {(r["repeat"], r["scenario"], r["strategy"]) for r in cfg["execution_order"]} == {
        (repeat, scenario, strategy) for repeat in (1, 2, 3) for scenario in ("steady", "burst") for strategy in ("original", "simple", "capped")}
    assert cfg["measurement_seconds"] == 100 and cfg["idle_seconds"] == 60 and cfg["background_aging_ms"] == 5000
    assert cfg["drain_seconds"] == cfg["request_timeout_seconds"] == 180 and cfg["send_lag_limit_ms"] == 50
    assert cfg["max_running_background"] == 1
    for strategy in ("original", "simple", "capped"):
        assert cfg["strategies"][strategy]["environment"] == policy_environment(strategy, cfg["background_aging_ms"])
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    dump(out / "protocol.json", cfg)
    dump(out / "batch-status.json", {"status": "preparing", "started_at": datetime.now().astimezone().isoformat()})
    completed = []
    sampler = sample_log = None
    try:
        gate = read(args.gate_dir / "summary.json")
        assert gate["status"] == "passed" and gate.get("total_first_includes_before_send_hold") is True
        assert gate.get("metadata_fields_verified") == ["request_class", "client_req_id"]
        assert gate.get("slow_disk_test", {}).get("status") == "passed", "v2 slow-disk client gate required"
        dump(out / "client-gate.json", {"directory": str(args.gate_dir.resolve()), "summary": gate})
        engine_gate = read(args.engine_gate_dir / "summary.json")
        assert engine_gate["status"] == "passed" and engine_gate["study"] == "background-cap-v1"
        dump(out / "engine-gate.json", {"directory": str(args.engine_gate_dir.resolve()), "summary": engine_gate})
        engine_bin = args.engine_bin.resolve(strict=True)
        model = args.model.resolve(strict=True)
        engine_root = args.engine_source_dir.resolve() if args.engine_source_dir else engine_bin.parents[2]
        engine_commit = git_output(engine_root, "rev-parse", "HEAD").strip()
        model_sha = await asyncio.to_thread(sha, model)
        binary_sha = await asyncio.to_thread(sha, engine_bin)
        runtime_files = await asyncio.to_thread(binary_inventory, engine_bin)
        assert read(args.engine_gate_dir / "engine-binary-inventory.json") == runtime_files, "Engine gate binary inventory differs"
        assert engine_gate["model_sha256"] == model_sha, "Engine gate model differs"
        assert engine_commit == cfg["engine_commit"] and model_sha == cfg["model_sha256"], "engine/model version mismatch"
        source_files = set(cfg.get("source_files", [])) | {
            "scripts/replay/78_run_background_cap.py", "scripts/replay/simple_policy_replay_v2.py",
            "scripts/replay/open_loop.py", "scripts/replay/70_run_prestudy.py", "scripts/replay/75_run_lowload.py",
            "scripts/analysis/lowload_evidence.py",
            "tests/integration/verify_simple_policy_client_v2.py", "tests/integration/verify_background_cap_engine.py", cfg["protocol_path"], cfg["engine_patch_path"]}
        cfg_rel = cfg_path.relative_to(ROOT).as_posix()
        source_files.add(cfg_rel)
        hashes = {}
        for rel in sorted(source_files):
            data = (ROOT / rel).read_bytes()
            target = out / "source" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            hashes[rel] = hashlib.sha256(data).hexdigest()
        dump(out / "code-sha256.json", hashes)
        assert all(hashes.get(rel) == digest for rel, digest in engine_gate["source_sha256"].items()), "Engine gate code/patch snapshot differs"
        engine_source_paths = cfg.get("engine_source_files", ["tools/server/server-task.h", "tools/server/server-queue.h", "tools/server/server-queue.cpp", "tools/server/server-context.cpp"])
        engine_sources = {}
        for rel in engine_source_paths:
            data = (engine_root / rel).read_bytes()
            reviewed = ROOT / "patches/background-cap-v1" / rel
            assert reviewed.is_file() and data == reviewed.read_bytes(), f"Applied engine source differs from reviewed patch source: {rel}"
            target = out / "engine-source" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            engine_sources[rel] = hashlib.sha256(data).hexdigest()
        dump(out / "engine-source-sha256.json", engine_sources)
        assert engine_sources == engine_gate["engine_source_sha256"], "Engine gate source differs"
        assert engine_commit == engine_gate["engine_commit"], "Engine gate commit differs"
        dump(out / "engine-runtime-sha256.json", runtime_files)
        source = ROOT / cfg["source_batch"]
        provenance = {}
        for rel in ("r1-E0/replay/trace.jsonl", "r1-E1/replay/trace.jsonl", "environment.json"):
            original = source / rel
            dest = out / "input-source" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(original.read_bytes())
            provenance[rel] = sha(original)
        dump(out / "input-source-sha256.json", provenance)
        budgets = []
        for row in traces["steady"]:
            original = source / ("r1-E1" if row["category"] == "background" else "r1-E0") / "replay/requests" / (row["req_id"] + ".json")
            old = read(original)
            assert all(old[k] == v for k, v in row.items() if k != "arrival_offset_ms")
            n = old["timings"]["prompt_n"] + old["timings"]["cache_n"]
            assert n + row["max_tokens"] <= 2048
            budgets.append({"req_id": row["req_id"], "historical_input_tokens": n, "max_tokens": row["max_tokens"]})
        dump(out / "historical-input-budgets.json", {"scope": "Historical matched requests, actual new lengths audited separately", "requests": budgets})
        dump(out / "selected-background-indices.json", selected_indices)
        for scenario, trace in traces.items():
            (out / f"trace-{scenario}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in trace), encoding="utf-8")
        (out / "engine-working-tree.patch").write_text(git_output(engine_root, "diff", "--binary"), encoding="utf-8")
        version = subprocess.run([str(engine_bin), "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
        (out / "engine-version.txt").write_bytes(version.stdout)
        env = {"recorded_at": datetime.now().astimezone().isoformat(), "python": sys.version,
               "engine_commit": engine_commit, "engine_root": str(engine_root), "engine_working_tree": git_output(engine_root, "status", "--short"),
               "engine_binary": str(engine_bin), "engine_binary_sha256": binary_sha, "model": str(model), "model_sha256": model_sha,
               "engine_runtime_files": runtime_files, "engine_source_sha256": engine_sources,
               "project_commit": git_output(ROOT, "rev-parse", "HEAD").strip(), "project_working_tree": git_output(ROOT, "status", "--short"),
               "policy_environment": {strategy: policy_environment(strategy, cfg["background_aging_ms"])
                                      for strategy in ("original", "simple", "capped")},
               "scope": "Same modified binary for all three strategies; source/ + hashes identify uncommitted execution inputs"}
        dump(out / "environment.json", env)
        sample_log = (out / "gpu-samples.csv").open("xb")
        sampler = await asyncio.create_subprocess_exec("nvidia-smi", "--query-gpu=timestamp,memory.used,utilization.gpu,temperature.gpu,power.draw", "--format=csv", "--loop-ms=1000", stdout=sample_log, stderr=asyncio.subprocess.STDOUT)
        dump(out / "batch-status.json", {"status": "running", "started_at": env["recorded_at"]})
        for item in cfg["execution_order"]:
            if time.monotonic() - started >= cfg["batch_start_limit_seconds"]:
                raise RuntimeError("Batch start time limit reached")
            if shutil.disk_usage(out).free < cfg["min_free_bytes"] or sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) >= cfg["max_batch_bytes"]:
                raise RuntimeError("Disk budget reached")
            if binary_inventory(engine_bin) != runtime_files or sha(ROOT / cfg["engine_patch_path"]) != hashes[cfg["engine_patch_path"]]:
                raise RuntimeError("Executable or patch changed during collection")
            if any(sha(engine_root / rel) != digest for rel, digest in engine_sources.items()):
                raise RuntimeError("Engine source changed during collection")
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", 8080)) == 0:
                    raise RuntimeError("8080 occupied; refusing to reuse another server")
            repeat, scenario, strategy = (item[k] for k in ("repeat", "scenario", "strategy"))
            directory = out / f"r{repeat}-{scenario}-{strategy}"
            directory.mkdir()
            print(f"IDLE {directory.name}: {cfg['idle_seconds']} seconds", flush=True)
            prior.capture_environment(directory)
            await asyncio.sleep(cfg["idle_seconds"])
            with (directory / "gpu-after-idle.txt").open("xb") as f:
                subprocess.run(["nvidia-smi"], stdout=f, check=True)
            manifest = {"status": "running", "run_id": f"{out.name}/{directory.name}", "stage": "prestudy", **item,
                        "started_at": datetime.now().astimezone().isoformat(), "engine_commit": engine_commit,
                        "engine_binary_sha256": binary_sha, "model_sha256": model_sha, "project_commit": env["project_commit"],
                        "engine_runtime_sha256_manifest": "../engine-runtime-sha256.json", "engine_source_sha256_manifest": "../engine-source-sha256.json",
                        "code_snapshot": "../source", "clock": cfg["timing"], "seed": cfg["seed"],
                        "engine_gate_supplied": args.engine_gate_dir is not None,
                        "denominators": "window completed requests / 100s; whole-run known completed output / final terminal seconds"}
            dump(directory / "manifest.json", manifest)
            server = log = None
            async def startup_warmup():
                nonlocal server, log
                server, log = await start_server(directory, engine_bin, model, strategy, cfg["background_aging_ms"])
                warm = [{"req_id": f"warm{i}", "category": "interactive", "arrival_offset_ms": i * 500,
                         "prompt": "请回复：准备好了。", "max_tokens": 16} for i in range(2)]
                warmed = await replay(warm, directory / "warmup", URL, 1, 10)
                assert warmed["terminal_counts"]["completed"] == 2
            try:
                await asyncio.wait_for(startup_warmup(), cfg["startup_warmup_timeout_seconds"])
                await asyncio.to_thread(fetch, "/metrics", directory / "metrics-before.txt")
                print(f"START {directory.name}: {len(traces[scenario])} requests", flush=True)
                summary = await replay(traces[scenario], directory / "replay", URL,
                                       cfg["measurement_seconds"], cfg["drain_seconds"], cfg["request_timeout_seconds"], cfg["send_lag_limit_ms"])
                # A grace period is outside the fixed performance denominator.
                for _ in range(10):
                    await asyncio.sleep(.2)
                    await asyncio.to_thread(fetch, "/metrics", directory / "metrics-after.txt")
                    if all(metric_zero(directory / "metrics-after.txt").values()):
                        break
            except BaseException as exc:
                manifest.update(status="failed", error=repr(exc))
                raise
            finally:
                if server is not None:
                    await prior.engine.stop_server(server, log)
                    manifest["server_exit_code"] = server.returncode
                manifest["finished_at"] = datetime.now().astimezone().isoformat()
                dump(directory / "manifest.json", manifest)
            try:
                audit = audit_run(directory, traces[scenario], cfg)
            except BaseException as exc:
                manifest.update(status="failed", error=f"audit: {exc!r}")
                dump(directory / "manifest.json", manifest)
                raise
            dump(directory / "audit.json", audit)
            manifest.update(status="completed" if not audit["flags"] else "needs_review", workload_sha256=sha(directory / "replay/trace.jsonl"),
                            server_command=read(directory / "server-command.json"), policy_environment=read(directory / "server-policy-environment.json"))
            dump(directory / "manifest.json", manifest)
            completed.append({**item, "directory": directory.name, "audit": audit, "summary": summary})
            dump(out / "batch-summary.json", completed)
            p95 = summary["classes"]["interactive"]["total_first_p95_ms"]
            print(f"DONE {directory.name}: states={summary['terminal_counts']} lag={summary['send_lag_max_ms']}ms total-first P95={p95}ms flags={audit['flags']}", flush=True)
            if audit["flags"]:
                raise RuntimeError("Integrity/capacity gate failed; no subsequent runs started")
        assert binary_inventory(engine_bin) == runtime_files, "Runtime library/binary changed during collection"
        assert all(sha(engine_root / rel) == digest for rel, digest in engine_sources.items()), "Engine source changed during collection"
        dump(out / "engine-runtime-sha256-after.json", binary_inventory(engine_bin))
        model_sha_after = await asyncio.to_thread(sha, model)
        dump(out / "model-sha256-after.json", {"path": str(model), "sha256": model_sha_after})
        assert model_sha_after == model_sha, "Model changed during collection"
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
        with (out / "gpu-after.txt").open("wb") as f:
            subprocess.run(["nvidia-smi"], stdout=f, stderr=subprocess.STDOUT)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=ROOT / "configs/prestudy-background-cap-v1.json")
    p.add_argument("--gate-dir", type=Path, required=True)
    p.add_argument("--engine-gate-dir", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--engine-bin", type=Path, required=True)
    p.add_argument("--engine-source-dir", type=Path)
    p.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    asyncio.run(run(p.parse_args()))
