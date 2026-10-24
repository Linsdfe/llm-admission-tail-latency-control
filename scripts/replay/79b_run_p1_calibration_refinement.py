#!/usr/bin/env python3
"""Append-only serial P1 calibration refinement using frozen collection tools."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/replay"))
spec = importlib.util.spec_from_file_location("p1_refinement_frozen_runner", ROOT / "scripts/replay/79_run_p1_development.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
from p1_workload import build_candidates, materialize_trace, render_candidate

CONFIG_PATH = ROOT / "configs/p1-calibration-refinement-v1.json"
NEW_SOURCES = ["scripts/replay/79b_run_p1_calibration_refinement.py",
               "configs/p1-calibration-refinement-v1.json",
               "tests/unit/test_p1_calibration_refinement.py"]


def plans(cfg):
    """Build only predeclared calibration/development descriptors, never final."""
    for split in ("calibration", "development"):
        for seed in cfg[split]["seeds"]:
            yield split, seed, build_candidates(split, seed, config=cfg)


def model_free_checks(cfg):
    old = base.read(ROOT / "configs/p1-development-workload-v1.json")
    for key in ("prompt_format", "tokenizer_request", "input_token_buckets", "max_output_tokens",
                "sampling", "slot_context_tokens", "input_plus_output_safety_tokens"):
        assert cfg[key] == old[key], "Changed frozen workload semantics: " + key
    assert cfg["calibration"]["seeds"] == list(range(11002, 11007))
    assert cfg["development"]["seeds"] == [21004]
    assert cfg["final_test_generation_enabled"] is False
    values = list(plans(cfg))
    by_split = {s: [r for split, _, rs in values if split == s for r in rs]
                for s in ("calibration", "development")}
    assert {s: len(rs) for s, rs in by_split.items()} == {"calibration": 120, "development": 120}
    assert {r["req_id"] for r in by_split["calibration"]}.isdisjoint(r["req_id"] for r in by_split["development"])
    assert {r["template_family"] for r in by_split["calibration"]}.isdisjoint(r["template_family"] for r in by_split["development"])
    assert len({r["req_id"] for rs in by_split.values() for r in rs}) == 240
    for split, rs in by_split.items():
        counts = Counter((r["category"], r["input_token_bucket"]) for r in rs)
        assert len(counts) == 6 and set(counts.values()) == {20}, (split, counts)
        for row in rs:
            assert row["ignore_eos"] is False and row["max_tokens"] == cfg["max_output_tokens"][row["category"]]
    try:
        build_candidates("final", 31001, config=cfg)
    except ValueError:
        pass
    else:
        raise AssertionError("Final-test generation must stay disabled")
    frozen = base.read(ROOT / cfg["old_collection_directory"] / "source-sha256.json")
    assert len(frozen) == 38 and all(base.prior.sha(ROOT / p) == h for p, h in frozen.items())
    return {"status": "passed", "model_started": False, "calibration_descriptors": 120,
            "development_validation_descriptors": 120, "samples_per_class_bucket_per_split": 20,
            "old_frozen_sources_unchanged": 38, "final_generation_disabled": True}


def dump(path, value):
    base.dump(path, value)


def request_manifest(split, seed, index, row, directory, state):
    return {"status": state, "stage": "development", "split": split, "seed": seed,
            "index": index, "req_id": row["req_id"], "run_id": directory.as_posix(),
            "strategy": "prototype_original_order", "planned_arrival_offset_ms": 0,
            "serial_not_load_performance": True}


async def collect(args, cfg):
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    completed = []
    server = log = sampler = sample_log = None
    tokenizer = None
    dump(out / "batch-status.json", {"status": "preparing", "started_at": datetime.now().astimezone().isoformat()})
    try:
        preflight = model_free_checks(cfg)
        dump(out / "model-free-preflight.json", preflight)
        assert shutil.disk_usage(out).free >= cfg["min_free_bytes_before_batch"]
        base.free_port()
        engine = args.engine_bin.resolve(strict=True)
        model = args.model.resolve(strict=True)
        engine_root = engine.parents[2]
        old_dir = ROOT / cfg["old_collection_directory"]
        runtime = base.prior.binary_inventory(engine)
        assert runtime == base.read(old_dir / "engine-runtime-sha256.json")
        model_sha = await asyncio.to_thread(base.prior.sha, model)
        assert model_sha == cfg["model_sha256"]
        commit = base.prior.git_output(engine_root, "rev-parse", "HEAD").strip()
        assert commit == cfg["engine_commit"]
        gate_dir = args.client_gate_dir.resolve(strict=True)
        client_gate = base.read(gate_dir / "summary.json")
        assert client_gate["status"] == "passed"
        assert all(base.prior.sha(ROOT / p) == h for p, h in client_gate["source_sha256"].items())
        shutil.copytree(gate_dir, out / "client-gate")
        old_hashes = base.read(old_dir / "source-sha256.json")
        source_paths = list(old_hashes) + NEW_SOURCES + [cfg["protocol_path"]]
        hashes = {}
        for relative in source_paths:
            path = ROOT / relative
            target = out / "source" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
            hashes[relative] = base.prior.sha(path)
        assert all(hashes[p] == h for p, h in old_hashes.items())
        engine_hashes = base.read(old_dir / "engine-source-sha256.json")
        for relative, digest in engine_hashes.items():
            path = engine_root / relative
            assert base.prior.sha(path) == digest
            target = out / "engine-source" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
        dump(out / "source-sha256.json", hashes)
        dump(out / "engine-source-sha256.json", engine_hashes)
        dump(out / "engine-runtime-sha256.json", runtime)
        dump(out / "environment.json", {"recorded_at": datetime.now().astimezone().isoformat(),
             "engine_commit": commit, "engine_binary": str(engine), "model": str(model),
             "model_sha256": model_sha, "python": sys.version,
             "kernel": Path("/proc/version").read_text(), "config": cfg,
             "project_commit": base.prior.git_output(ROOT, "rev-parse", "HEAD").strip(),
             "project_working_tree": base.prior.git_output(ROOT, "status", "--short"),
             "scope": cfg["scope"]})
        base.capture_gpu(out / "gpu-before.txt")
        sample_log = (out / "gpu-samples.csv").open("xb")
        sampler = await asyncio.create_subprocess_exec("nvidia-smi", "--query-gpu=timestamp,memory.used,utilization.gpu,temperature.gpu,power.draw",
             "--format=csv", "--loop-ms=1000", stdout=sample_log, stderr=asyncio.subprocess.STDOUT)
        server_dir = out / "service"
        server_dir.mkdir()
        server, log = await base.prior.start_server(server_dir, engine, model, "original", 5000)
        props = base.read(server_dir / "props.json")
        assert props["total_slots"] == 2 and props["default_generation_settings"]["n_ctx"] == 2048
        await asyncio.wait_for(base.warm(out), timeout=60)
        await base.await_idle(server_dir, "after-warmup")
        tokenizer = base.Tokenizer(out)
        traces = []
        try:
            for split, seed, descriptors in plans(cfg):
                print(f"TOKENIZE {split} seed={seed} requests={len(descriptors)}", flush=True)
                trace = await asyncio.to_thread(materialize_trace, descriptors, tokenizer, cfg)
                base.write_rows(out / f"trace-{split}-seed{seed}.jsonl", trace)
                traces.append((split, seed, trace))
            dump(out / "tokenization-summary.json", {"calls": tokenizer.count, "requests": 240,
                 "final_test_generated_or_read": False})
        finally:
            tokenizer.close()
            tokenizer = None
        dump(out / "batch-status.json", {"status": "running", "requests_completed": 0})
        for split, seed, trace in traces:
            assert time.monotonic() - started < cfg["batch_timeout_seconds"]
            assert base.prior.binary_inventory(engine) == runtime
            assert all(base.prior.sha(ROOT / p) == h for p, h in hashes.items())
            assert all(base.prior.sha(engine_root / p) == h for p, h in engine_hashes.items())
            assert sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) < cfg["max_batch_bytes"]
            seed_dir = out / split / f"seed{seed}"
            seed_dir.mkdir(parents=True)
            for index, row in enumerate(trace, 1):
                assert shutil.disk_usage(out).free >= cfg["min_free_bytes_before_request"]
                one = dict(row, arrival_offset_ms=0)
                directory = seed_dir / f"c{index:02d}"
                directory.mkdir()
                manifest = request_manifest(split, seed, index, one, directory.relative_to(out), "running")
                dump(directory / "manifest.json", manifest)
                await base.replay([one], directory / "replay", base.prior.URL,
                     cfg["serial_measurement_seconds"], cfg["serial_drain_seconds"],
                     cfg["request_timeout_seconds"], cfg["send_lag_limit_ms"], cfg["transport_timeout_seconds"])
                audit = base.audit_replay(directory / "replay", [one])
                manifest.update(status="completed" if not audit["flags"] and audit["terminal_counts"]["completed"] == 1 else "needs_review",
                                finished_at=datetime.now().astimezone().isoformat(), audit=audit)
                dump(directory / "audit.json", audit)
                dump(directory / "manifest.json", manifest)
                completed.append({"split": split, "seed": seed, "index": index, "directory": directory.relative_to(out).as_posix(),
                                  "req_id": one["req_id"], "audit": audit})
                dump(out / "request-audits.json", completed)
                dump(out / "batch-status.json", {"status": "running", "requests_completed": len(completed),
                      "calibration_completed": sum(r["split"] == "calibration" for r in completed),
                      "development_validation_completed": sum(r["split"] == "development" for r in completed),
                      "elapsed_seconds": time.monotonic() - started})
                print(f"DONE {len(completed)}/240 {split} {one['req_id']} input={one['input_tokens']} states={audit['terminal_counts']}", flush=True)
                assert not audit["flags"] and audit["terminal_counts"]["completed"] == 1, "retain failed serial request and review; do not continue collection"
            await base.await_idle(server_dir, f"after-{split}-{seed}")
        await base.prior.prior.engine.stop_server(server, log)
        log = None
        assert server.returncode == 0
        accounting = base.gate.verify_cap_events(server_dir / "server.log", 0)
        assert accounting["events"] == 484
        events = base.gate.cap_events(server_dir / "server.log")
        expected_ids = {r["req_id"] for r in completed} | {"warm0", "warm1"}
        assert {e["clientid"] for e in events} == expected_ids
        assert all(sum(e["clientid"] == rid and e["event"] == kind for e in events) == 1
                   for rid in expected_ids for kind in ("LAUNCH", "RELEASE"))
        assert base.prior.binary_inventory(engine) == runtime
        assert all(base.prior.sha(ROOT / p) == h for p, h in hashes.items())
        assert all(base.prior.sha(engine_root / p) == h for p, h in engine_hashes.items())
        assert await asyncio.to_thread(base.prior.sha, model) == model_sha
        dump(out / "engine-runtime-sha256-after.json", base.prior.binary_inventory(engine))
        dump(out / "model-sha256-after.json", {"path": str(model), "sha256": model_sha})
        summary = {"status": "completed", "study": cfg["study"], "calibration_requests": 120,
             "development_validation_requests": 120, "measured_requests": 240,
             "warmup_requests": 2, "completion_requests": 242,
             "terminal_counts": {"completed": 240, "rejected": 0, "timeout": 0, "cancelled": 0, "failed": 0, "unfinished": 0},
             "accounting": accounting, "server_exit_code": server.returncode,
             "finished_at": datetime.now().astimezone().isoformat(), "elapsed_seconds": time.monotonic() - started,
             "source_runtime_model_unchanged": True, "final_test_generated_or_read": False,
             "scope": cfg["scope"]}
        dump(out / "summary.json", summary)
        dump(out / "batch-status.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    except BaseException as exc:
        dump(out / "batch-status.json", {"status": "failed", "requests_completed": len(completed),
             "error": repr(exc), "finished_at": datetime.now().astimezone().isoformat(),
             "elapsed_seconds": time.monotonic() - started})
        raise
    finally:
        if tokenizer is not None:
            tokenizer.close()
        if server is not None and log is not None:
            await base.prior.prior.engine.stop_server(server, log)
        if sampler is not None and sampler.returncode is None:
            sampler.terminate()
            await sampler.wait()
        if sample_log is not None:
            sample_log.close()
        base.capture_gpu(out / "gpu-after.txt")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--model-free-check", action="store_true")
    parser.add_argument("--engine-bin", type=Path)
    parser.add_argument("--model", type=Path, default=base.prior.DEFAULT_MODEL)
    parser.add_argument("--client-gate-dir", type=Path, default=ROOT / "results/raw/client-verification/20261024-p1-development-client-v2")
    args = parser.parse_args()
    cfg = base.read(args.config)
    if args.model_free_check:
        out = args.out_dir.resolve()
        out.mkdir(parents=True, exist_ok=False)
        result = model_free_checks(cfg)
        result["source_sha256"] = {p: base.prior.sha(ROOT / p) for p in NEW_SOURCES}
        dump(out / "summary.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.engine_bin is None:
        parser.error("--engine-bin is required for real-model collection")
    asyncio.run(asyncio.wait_for(collect(args, cfg), timeout=cfg["batch_timeout_seconds"]))


if __name__ == "__main__":
    main()
