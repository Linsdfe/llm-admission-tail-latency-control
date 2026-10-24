#!/usr/bin/env python3
"""Post-hoc paired output-budget sentinel probe; not a quality/performance gate."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/replay"))
from p1_development_replay import validate

spec = importlib.util.spec_from_file_location("p1_budget_frozen_runner", ROOT / "scripts/replay/79_run_p1_development.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

OLD = ROOT / "results/raw/development/20261024-025138-p1-development-validation-v1"
PROTOCOL = "docs/p1/output-budget-probe-protocol-v1.md"
NEW_SOURCES = ["scripts/replay/79c_run_p1_output_budget_probe.py", PROTOCOL,
               "docs/p1/calibration-refinement-design-review-v1.md"]
SELECTION = [
    ("c05", "cal-11001-i002", "interactive", 2, 151, "372db8aa87c97966ee5b8b7752a8a5b5e5e0e3b48968e2123a332bdc53af89e4"),
    ("c11", "cal-11001-i005", "interactive", 2, 153, "d7afdd90d77dfa47599764e1dee7ba7864864549d8ca4366b45c62dae56f5b7c"),
    ("c17", "cal-11001-i008", "interactive", 2, 155, "0a2fa92f4a15773d22633b3ffcbf28a15ce30e5a4e2ff24ed71b450251d1a90f"),
    ("c23", "cal-11001-i011", "interactive", 2, 154, "a1c81a42a02625509aa3d2b57261383a62223f87892095dcb2c5254815f6b6fc"),
    ("c04", "cal-11001-b001", "background", 1, 327, "4d035ba5cf458f7373ab5adfe58b0cb3a08ac354d3070775e4298bf523d13661"),
    ("c08", "cal-11001-b003", "background", 1, 324, "f11b4d9c25fd0970a07dd9992a29ee24a7c7c8506a71daa72fa9e01347113f7f"),
    ("c18", "cal-11001-b008", "background", 1, 331, "8cc32317f6e4719ae04a9359a537fdd90bf2dc3147c278364ea6b4bd6d925047"),
    ("c22", "cal-11001-b010", "background", 1, 335, "018aad3e95f8925b22ee35a8e3e35c5fe1b05d5dd3db1690efcaeb4bacb19be5"),
]
CAPS = {"interactive": {"low": 64, "high": 128}, "background": {"low": 384, "high": 768}}
TIMEOUT_SECONDS = 900
SCOPE = "Post-hoc eight-prompt sentinel diagnostic only; not population quality, load performance, or main calibration/validation. No automatic quality pass and no M-based budget selection."


def text_sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def selected_inputs():
    trace_path = OLD / "prep/calibration-trace.jsonl"
    by_id = {r["req_id"]: r for r in base.rows(trace_path)}
    assert len(by_id) == 24
    selected = []
    input_hashes = {trace_path.relative_to(ROOT).as_posix(): base.prior.sha(trace_path)}
    for case, req_id, category, bucket, length, expected_sha in SELECTION:
        path = OLD / "prep/calibration" / case / "replay/client.jsonl"
        assert base.prior.sha(path) == expected_sha, "Original selected record changed: " + case
        recorded = base.rows(path)
        assert len(recorded) == 1
        old = recorded[0]
        row = by_id[req_id]
        assert all(old[k] == v for k, v in row.items() if k != "arrival_offset_ms"), case
        assert old["arrival_offset_ms"] == 0, case
        assert (row["category"], row["input_token_bucket"], row["input_tokens"]) == (category, bucket, length)
        assert row["max_tokens"] == CAPS[category]["low"]
        assert row["temperature"] == 0 and row["ignore_eos"] is False
        assert row["content_sha256"] == text_sha(row["prompt"])
        assert old["terminal"] == "completed" and old["native_final"]["truncated"] is False
        assert row["input_tokens"] + CAPS[category]["high"] + 1 <= 2048
        input_hashes[path.relative_to(ROOT).as_posix()] = expected_sha
        selected.append((case, dict(row)))
    return selected, input_hashes


def make_plan(selected):
    plan = []
    for index, (case, original) in enumerate(selected, 1):
        levels = ("low", "high") if index % 2 else ("high", "low")
        for level in levels:
            row = dict(original, req_id=f"budget-{index:02d}-{level}", arrival_offset_ms=0,
                       max_tokens=CAPS[original["category"]][level],
                       probe_source_req_id=original["req_id"], probe_source_case=case,
                       probe_case_index=index, probe_level=level, probe_study="p1-output-budget-probe-v1",
                       posthoc_sentinel=True, not_main_calibration_or_validation=True)
            plan.append(row)
    assert len(plan) == len({r["req_id"] for r in plan}) == 16
    validate(plan)
    return plan


def ngram_diagnostics(content):
    # Unicode characters, whitespace removed, punctuation retained; no tokenizer proxy.
    text = "".join(c for c in content if not c.isspace())
    values = []
    for n in (4, 8, 16):
        counts = Counter(text[i:i+n] for i in range(max(0, len(text)-n+1)))
        windows = sum(counts.values())
        gram, maximum = counts.most_common(1)[0] if counts else (None, 0)
        values.append({"n_characters": n, "windows": windows, "unique_ngrams": len(counts),
                       "most_frequent_ngram": gram, "max_occurrences": maximum,
                       "max_frequency_ratio": maximum/windows if windows else None,
                       "duplicate_excess_ratio": (windows-len(counts))/windows if windows else None})
    ratios = [v["max_frequency_ratio"] for v in values if v["max_frequency_ratio"] is not None]
    return {"unit": "Unicode characters excluding whitespace; punctuation retained",
            "character_count_without_whitespace": len(text), "per_n": values,
            "max_ngram_frequency_ratio_across_n": max(ratios) if ratios else None,
            "quality_verdict": None}


def model_free_checks():
    frozen = base.read(OLD / "source-sha256.json")
    assert len(frozen) == 38 and all(base.prior.sha(ROOT / p) == h for p, h in frozen.items())
    selected, input_hashes = selected_inputs()
    plan = make_plan(selected)
    assert all(r["input_tokens"] + r["max_tokens"] + 1 <= 2048 for r in plan)
    assert [(r["probe_case_index"], r["probe_level"]) for r in plan] == [
        (i, level) for i in range(1, 9) for level in (("low", "high") if i % 2 else ("high", "low"))]
    assert len({r["content_sha256"] for r in plan}) == 8
    assert ngram_diagnostics("")["max_ngram_frequency_ratio_across_n"] is None
    repeated = ngram_diagnostics("abcdabcdabcd")["per_n"][0]
    assert repeated["windows"] == 9 and repeated["max_occurrences"] == 3
    assert abs(repeated["duplicate_excess_ratio"] - 5/9) < 1e-12
    return {"status": "passed", "model_started": False, "selected_original_prompts": 8,
            "measured_completions": 16, "warmup_completions": 2, "expected_cap_events": 36,
            "max_input_plus_output_plus_safety": max(r["input_tokens"]+r["max_tokens"]+1 for r in plan),
            "old_frozen_sources_unchanged": 38, "input_source_sha256": input_hashes,
            "scope": SCOPE}, plan


def cap_pairs(log_path, req_ids):
    events = base.gate.cap_events(log_path)
    result = {}
    for req_id in req_ids:
        pair = [e for e in events if e["clientid"] == req_id]
        assert len(pair) == 2 and [e["event"] for e in pair] == ["LAUNCH", "RELEASE"], (req_id, pair)
        assert pair[0]["id"] == pair[1]["id"] and pair[0]["slot"] == pair[1]["slot"]
        result[req_id] = pair
    return result


def diagnostic(row):
    final = row["native_final"]
    return {"req_id": row["req_id"], "original_req_id": row["probe_source_req_id"],
            "source_case": row["probe_source_case"], "category": row["category"],
            "input_token_bucket": row["input_token_bucket"], "input_tokens": row["input_tokens"],
            "level": row["probe_level"], "max_tokens": row["max_tokens"],
            "output_tokens": row["output_tokens"], "terminal": row["terminal"],
            "stop_type": final.get("stop_type"), "eos": final.get("stop_type") == "eos",
            "generation_limit": final.get("stop_type") == "limit",
            "reached_output_budget": row["output_tokens"] == row["max_tokens"],
            "context_truncated": final.get("truncated"), "content": row["content"],
            "content_sha256": text_sha(row["content"]), "repetition": ngram_diagnostics(row["content"]),
            "assistant_qualitative_review": "pending_exploratory_subjective_review_no_automatic_pass"}


async def collect(args):
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    completed = []
    server = log = sampler = sample_log = tokenizer = None
    base.dump(out / "batch-status.json", {"status": "preparing", "started_at": datetime.now().astimezone().isoformat()})
    try:
        preflight, plan = model_free_checks()
        base.dump(out / "model-free-preflight.json", preflight)
        assert shutil.disk_usage(out).free >= 10*1024**3
        base.free_port()
        engine = args.engine_bin.resolve(strict=True)
        model = args.model.resolve(strict=True)
        engine_root = engine.parents[2]
        cfg = base.read(ROOT / "configs/p1-development-validation-v1.json")
        runtime = base.prior.binary_inventory(engine)
        assert runtime == base.read(OLD / "engine-runtime-sha256.json")
        model_sha = await asyncio.to_thread(base.prior.sha, model)
        assert model_sha == cfg["model_sha256"]
        commit = base.prior.git_output(engine_root, "rev-parse", "HEAD").strip()
        assert commit == cfg["engine_commit"]
        gate_dir = args.client_gate_dir.resolve(strict=True)
        client_gate = base.read(gate_dir / "summary.json")
        assert client_gate["status"] == "passed"
        assert all(base.prior.sha(ROOT / p) == h for p, h in client_gate["source_sha256"].items())
        shutil.copytree(gate_dir, out / "client-gate")
        old_hashes = base.read(OLD / "source-sha256.json")
        hashes = {}
        for relative in list(old_hashes) + NEW_SOURCES:
            source = ROOT / relative
            target = out / "source" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            hashes[relative] = base.prior.sha(source)
        assert all(hashes[p] == h for p, h in old_hashes.items())
        for relative, digest in preflight["input_source_sha256"].items():
            source = ROOT / relative
            assert base.prior.sha(source) == digest
            target = out / "input-source" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        engine_hashes = base.read(OLD / "engine-source-sha256.json")
        for relative, digest in engine_hashes.items():
            source = engine_root / relative
            assert base.prior.sha(source) == digest
            target = out / "engine-source" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        base.dump(out / "source-sha256.json", hashes)
        base.dump(out / "input-source-sha256.json", preflight["input_source_sha256"])
        base.dump(out / "engine-source-sha256.json", engine_hashes)
        base.dump(out / "engine-runtime-sha256.json", runtime)
        base.dump(out / "environment.json", {"recorded_at": datetime.now().astimezone().isoformat(),
            "python": sys.version, "kernel": Path("/proc/version").read_text(), "engine_commit": commit,
            "engine_binary": str(engine), "model": str(model), "model_sha256": model_sha,
            "project_commit": base.prior.git_output(ROOT, "rev-parse", "HEAD").strip(),
            "project_working_tree": base.prior.git_output(ROOT, "status", "--short"),
            "scope": SCOPE, "protocol": PROTOCOL, "batch_timeout_seconds": TIMEOUT_SECONDS})
        base.write_rows(out / "probe-plan.jsonl", plan)
        base.capture_gpu(out / "gpu-before.txt")
        sample_log = (out / "gpu-samples.csv").open("xb")
        sampler = await asyncio.create_subprocess_exec("nvidia-smi", "--query-gpu=timestamp,memory.used,utilization.gpu,temperature.gpu,power.draw",
            "--format=csv", "--loop-ms=1000", stdout=sample_log, stderr=asyncio.subprocess.STDOUT)
        service = out / "service"
        service.mkdir()
        server, log = await base.prior.start_server(service, engine, model, "original", 5000)
        props = base.read(service / "props.json")
        assert props["total_slots"] == 2 and props["default_generation_settings"]["n_ctx"] == 2048
        base.dump(out / "service-ownership.json", {"pid": server.pid, "owner": "this_script_created_process",
            "port_was_free_before_start": True, "stop_method": "signal only this subprocess; inherited SIGINT then kill after 10 seconds"})
        await asyncio.wait_for(base.warm(out), timeout=60)
        await base.await_idle(service, "after-warmup")
        warm_rows = base.rows(out / "warmup/client.jsonl")
        assert len(warm_rows) == 2 and all(r["terminal"] == "completed" for r in warm_rows)
        for r in warm_rows:
            assert r["output_tokens"] == r["timings"]["predicted_n"] == r["native_final"]["tokens_predicted"]
            assert r["new_prompt_tokens"] + r["cached_prompt_tokens"] == r["native_final"]["tokens_evaluated"]
            assert r["native_final"]["truncated"] is False
        base.dump(out / "warmup-audit.json", {"completed": 2, "cap_pairs": cap_pairs(service / "server.log", ["warm0", "warm1"]), "idle_verified": True})
        tokenizer = base.Tokenizer(out)
        try:
            for _, original in selected_inputs()[0]:
                tokens = await asyncio.to_thread(tokenizer, original["prompt"])
                assert len(tokens) == original["input_tokens"]
                assert text_sha(json.dumps(tokens, separators=(",", ":"))) == original["token_ids_sha256"]
                assert text_sha(original["prompt"]) == original["content_sha256"]
            base.dump(out / "tokenization-summary.json", {"calls": tokenizer.count, "prompts_checked": 8,
                "actual_token_ids_and_content_hash_match_original": True, "final_test_generated_or_read": False})
        finally:
            tokenizer.close()
            tokenizer = None
        for index, row in enumerate(plan, 1):
            assert time.monotonic() - started < TIMEOUT_SECONDS
            assert shutil.disk_usage(out).free >= 4*1024**3
            assert sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) < 512*1024**2
            assert all(base.prior.sha(ROOT / p) == h for p, h in hashes.items())
            directory = out / "cases" / f"q{index:02d}-{row['req_id']}"
            directory.mkdir(parents=True)
            manifest = {"status": "running", "study": "p1-output-budget-probe-v1", "scope": SCOPE,
                "sequence_index": index, "req_id": row["req_id"], "source_case": row["probe_source_case"],
                "original_req_id": row["probe_source_req_id"], "level": row["probe_level"], "max_tokens": row["max_tokens"]}
            base.dump(directory / "manifest.json", manifest)
            await base.replay([row], directory / "replay", base.prior.URL, .01, 45, 45, 50, 360)
            audit = base.audit_replay(directory / "replay", [row])
            base.dump(directory / "audit.json", audit)
            assert not audit["flags"] and audit["terminal_counts"]["completed"] == 1, "Retain failing case; stop later requests"
            await base.await_idle(directory, "after-request")
            audit.update(idle_verified=True, cap_pairs=cap_pairs(service / "server.log", [row["req_id"]]))
            base.dump(directory / "audit.json", audit)
            observed = base.rows(directory / "replay/client.jsonl")[0]
            result = diagnostic(observed)
            (directory / "output.txt").write_text(observed["content"], encoding="utf-8")
            base.dump(directory / "diagnostic.json", result)
            manifest.update(status="completed", finished_at=datetime.now().astimezone().isoformat(),
                            assistant_qualitative_review="pending", audit=audit)
            base.dump(directory / "manifest.json", manifest)
            completed.append({"directory": directory.relative_to(out).as_posix(), **result})
            base.dump(out / "diagnostics.json", completed)
            base.dump(out / "batch-status.json", {"status": "running", "requests_completed": len(completed),
                "elapsed_seconds": time.monotonic()-started})
            print(f"DONE {index}/16 {row['req_id']} output={result['output_tokens']}/{row['max_tokens']} stop={result['stop_type']}", flush=True)
        await base.await_idle(service, "after-all")
        await base.prior.prior.engine.stop_server(server, log)
        log = None
        base.dump(out / "service-stop.json", {"pid": server.pid, "server_exit_code": server.returncode,
            "stopped_owned_process_only": True, "finished_at": datetime.now().astimezone().isoformat()})
        assert server.returncode == 0
        accounting = base.gate.verify_cap_events(service / "server.log", 0)
        assert accounting["events"] == 36
        expected_ids = {r["req_id"] for r in plan} | {"warm0", "warm1"}
        assert {e["clientid"] for e in base.gate.cap_events(service / "server.log")} == expected_ids
        base.dump(out / "cap-pairs.json", cap_pairs(service / "server.log", sorted(expected_ids)))
        assert base.prior.binary_inventory(engine) == runtime
        assert all(base.prior.sha(ROOT / p) == h for p, h in hashes.items())
        assert all(base.prior.sha(ROOT / p) == h for p, h in preflight["input_source_sha256"].items())
        assert all(base.prior.sha(engine_root / p) == h for p, h in engine_hashes.items())
        assert await asyncio.to_thread(base.prior.sha, model) == model_sha
        base.dump(out / "engine-runtime-sha256-after.json", base.prior.binary_inventory(engine))
        base.dump(out / "model-sha256-after.json", {"path": str(model), "sha256": model_sha})
        summary = {"status": "completed", "study": "p1-output-budget-probe-v1", "scope": SCOPE,
            "sentinel_prompts": 8, "measured_completions": 16, "warmup_completions": 2, "completion_requests": 18,
            "terminal_counts": {s: sum(r["terminal"] == s for r in completed) for s in base.STATES},
            "accounting": accounting, "server_exit_code": server.returncode,
            "source_runtime_model_inputs_unchanged": True, "final_test_generated_or_read": False,
            "assistant_qualitative_review": "pending_exploratory_subjective_review_no_automatic_pass",
            "finished_at": datetime.now().astimezone().isoformat(), "elapsed_seconds": time.monotonic()-started}
        base.dump(out / "summary.json", summary)
        base.dump(out / "batch-status.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    except BaseException as exc:
        base.dump(out / "batch-status.json", {"status": "failed", "requests_completed": len(completed),
            "error": repr(exc), "finished_at": datetime.now().astimezone().isoformat(),
            "elapsed_seconds": time.monotonic()-started})
        raise
    finally:
        if tokenizer is not None:
            tokenizer.close()
        if server is not None and log is not None:
            await base.prior.prior.engine.stop_server(server, log)
            base.dump(out / "service-stop.json", {"pid": server.pid, "server_exit_code": server.returncode,
                "stopped_owned_process_only": True, "cleanup_after_interrupted_or_failed_batch": True})
        if sampler is not None and sampler.returncode is None:
            sampler.terminate()
            await sampler.wait()
        if sample_log is not None:
            sample_log.close()
        if sample_log is not None:
            base.capture_gpu(out / "gpu-after.txt")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--model-free-check", action="store_true")
    parser.add_argument("--engine-bin", type=Path)
    parser.add_argument("--model", type=Path, default=base.prior.DEFAULT_MODEL)
    parser.add_argument("--client-gate-dir", type=Path, default=ROOT / "results/raw/client-verification/20261024-p1-development-client-v2")
    args = parser.parse_args()
    if args.model_free_check:
        result, _ = model_free_checks()
        result["source_sha256"] = {p: base.prior.sha(ROOT / p) for p in NEW_SOURCES}
        if args.out_dir is not None:
            args.out_dir.resolve().mkdir(parents=True, exist_ok=False)
            base.dump(args.out_dir.resolve() / "summary.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.engine_bin is None or args.out_dir is None:
        parser.error("--engine-bin and --out-dir required for real-model collection")
    asyncio.run(asyncio.wait_for(collect(args), timeout=TIMEOUT_SECONDS))


if __name__ == "__main__":
    main()
