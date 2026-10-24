#!/usr/bin/env python3
"""Opt-in real-model G0 input-context/recovery probe; not a performance run.

Prepared separately from frozen performance sources. This file does nothing until
explicitly executed. It reuses the fixed start/stop and event-audit helpers without
editing them. Never claims a full input+output KV reservation ledger.
"""
import argparse
import asyncio
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/replay"))
spec = importlib.util.spec_from_file_location("g0_existing_engine_gate", ROOT / "tests/integration/verify_background_cap_engine.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
runner = gate.runner
dump = runner.dump


def http(path, body=None):
    request = urllib.request.Request(
        runner.URL + path,
        data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    started_ns = time.monotonic_ns()
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            code, raw, headers = response.status, response.read(), dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        code, raw, headers = exc.code, exc.read(), dict(exc.headers.items())
    ended_ns = time.monotonic_ns()
    data = raw.decode("utf-8")
    try:
        value = json.loads(data)
    except json.JSONDecodeError:
        value = None
    return {"http_status": code, "headers": headers, "body": data, "json": value,
            "client_start_monotonic_ns": started_ns, "client_end_monotonic_ns": ended_ns,
            "client_elapsed_ms": (ended_ns - started_ns) / 1e6}


async def record(out, name, endpoint, body=None):
    dump(out / (name + "-request.json"), {"endpoint": endpoint, "body": body})
    result = await asyncio.to_thread(http, endpoint, body)
    dump(out / (name + "-response.json"), result)
    (out / (name + "-response-body.txt")).write_bytes(result["body"].encode("utf-8"))
    return result


def capture_gpu(out, name):
    with (out / name).open("xb") as output:
        result = subprocess.run(["nvidia-smi"], stdout=output, stderr=subprocess.STDOUT, timeout=15)
    assert result.returncode == 0, "nvidia-smi failed"


async def idle(out, name):
    for _ in range(20):
        await asyncio.sleep(.1)
        await asyncio.to_thread(runner.fetch, "/metrics", out / (name + "-metrics.txt"))
        if all(runner.metric_zero(out / (name + "-metrics.txt")).values()):
            break
    assert all(runner.metric_zero(out / (name + "-metrics.txt")).values())
    slots = await record(out, name + "-slots", "/slots")
    assert slots["http_status"] == 200 and len(slots["json"]) == 2
    assert all(s["n_ctx"] == 2048 and not s["is_processing"] for s in slots["json"])


async def short_request(out, req_id, category):
    body = {"prompt": "Once upon a time, there was", "n_predict": 8, "temperature": 0,
            "stream": False, "cache_prompt": False, "request_class": category,
            "client_req_id": req_id, "ignore_eos": True}
    result = await record(out, req_id, "/completion", body)
    assert result["http_status"] == 200 and result["json"]["stop"] is True, result
    assert result["json"].get("tokens_evaluated", 0) > 0, result
    assert result["json"].get("tokens_predicted", 0) > 0, result
    assert result["json"].get("truncated") is False, result
    return {"id": req_id, "category": category, "http_status": result["http_status"],
            "tokens_evaluated": result["json"]["tokens_evaluated"],
            "tokens_predicted": result["json"]["tokens_predicted"],
            "truncated": result["json"]["truncated"], "client_elapsed_ms": result["client_elapsed_ms"]}


async def verify(args):
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = datetime.now().astimezone().isoformat()
    dump(out / "status.json", {"status": "preparing", "started_at": started})
    server = log = None
    try:
        free_bytes = shutil.disk_usage(out).free
        dump(out / "disk-preflight.json", {"free_bytes": free_bytes, "minimum_free_bytes": 4 * 1024 ** 3})
        assert free_bytes >= 4 * 1024 ** 3, "Less than 4 GiB free; refusing to start real-model probe"
        with socket.socket() as sock:
            assert sock.connect_ex(("127.0.0.1", 8080)) != 0, "8080 already occupied; refusing to reuse/kill another server"
        engine = args.engine_bin.resolve(strict=True)
        model = args.model.resolve(strict=True)
        protocol = args.protocol.resolve(strict=True)
        engine_root = engine.parents[2]
        inventory = runner.binary_inventory(engine)
        model_sha = runner.sha(model)
        commit = runner.git_output(engine_root, "rev-parse", "HEAD").strip()
        expected = runner.read(ROOT / "configs/prestudy-background-cap-v1.json")
        assert commit == expected["engine_commit"] and model_sha == expected["model_sha256"]
        old_gate = runner.read(ROOT / "results/raw/development/20261023-1515-background-cap-engine-v3/summary.json")
        assert inventory == runner.read(ROOT / "results/raw/development/20261023-1515-background-cap-engine-v3/engine-binary-inventory.json")
        assert old_gate["status"] == "passed" and old_gate["model_sha256"] == model_sha
        source_hashes = {}
        for rel in expected["source_files"]:
            source = ROOT / rel
            target = out / "source" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            source_hashes[rel] = runner.sha(source)
        script_rel = Path(__file__).relative_to(ROOT).as_posix()
        protocol_rel = protocol.relative_to(ROOT).as_posix()
        for rel, path in ((script_rel, Path(__file__)), (protocol_rel, protocol)):
            source_hashes[rel] = runner.sha(path)
            target = out / "source" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
        engine_hashes = {}
        for rel in ("tools/server/server-task.h", "tools/server/server-queue.h", "tools/server/server-queue.cpp", "tools/server/server-context.cpp", "tools/server/server-common.cpp", "tools/server/server-task.cpp", "tools/server/README.md", "common/common.h"):
            source = engine_root / rel
            target = out / "engine-source" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            engine_hashes[rel] = runner.sha(source)
            reviewed = ROOT / "patches/background-cap-v1" / rel
            if reviewed.is_file():
                assert source.read_bytes() == reviewed.read_bytes(), rel
        dump(out / "source-sha256.json", source_hashes)
        dump(out / "engine-source-sha256.json", engine_hashes)
        dump(out / "engine-binary-inventory.json", inventory)
        dump(out / "environment.json", {"started_at": started, "engine_binary": str(engine), "engine_commit": commit,
             "model": str(model), "model_sha256": model_sha, "python": sys.version,
             "protocol": protocol_rel, "protocol_sha256": runner.sha(protocol),
             "kernel": Path("/proc/version").read_text(encoding="utf-8"),
             "scope": "Fresh process with an already built fixed binary, not a fresh-from-source rebuild; G0 input-context observability/recovery only."})
        capture_gpu(out, "gpu-before.txt")
        server, log = await runner.start_server(out, engine, model, "capped", 5000)
        props = runner.read(out / "props.json")
        assert props["total_slots"] == 2
        n_ctx = props["default_generation_settings"]["n_ctx"]
        assert n_ctx == 2048, props
        slots = await record(out, "slots-before", "/slots")
        assert slots["http_status"] == 200 and len(slots["json"]) == 2
        assert all(s["n_ctx"] == n_ctx and not s["is_processing"] for s in slots["json"])
        warmups = [await short_request(out, "warm-" + str(i), "interactive") for i in range(2)]
        await idle(out, "after-warmup")
        tokenized = await record(out, "tokenize", "/tokenize", {"content": " a", "add_special": False})
        assert tokenized["http_status"] == 200 and tokenized["json"]["tokens"]
        token = tokenized["json"]["tokens"][0]
        assert type(token) is int
        legal_body = {"prompt": [token] * (n_ctx - 1), "n_predict": 1, "temperature": 0,
                      "stream": False, "cache_prompt": False, "request_class": "background",
                      "client_req_id": "context-legal-2047", "ignore_eos": True}
        legal = await record(out, "context-legal-2047", "/completion", legal_body)
        assert legal["http_status"] == 200 and legal["json"]["stop"] is True, legal
        assert legal["json"]["tokens_evaluated"] == n_ctx - 1, legal
        assert legal["json"]["tokens_predicted"] == 1, legal
        assert type(legal["json"]["truncated"]) is bool, legal
        # Fixed source checks context exhaustion before n_predict, with ctx_shift
        # disabled by default. A 2047-token input may therefore be accepted yet
        # report truncated=true after its first token. Preserve that observation;
        # never silently relabel it as a successful untruncated 2048-token budget.
        legal_observation = {"input_tokens": n_ctx - 1, "n_predict": 1, "http_status": 200,
                             "tokens_evaluated": legal["json"]["tokens_evaluated"],
                             "tokens_predicted": legal["json"]["tokens_predicted"],
                             "truncated": legal["json"]["truncated"],
                             "stop_type": legal["json"].get("stop_type"),
                             "client_elapsed_ms": legal["client_elapsed_ms"]}
        await idle(out, "after-legal")
        cases = []
        for size in (n_ctx, n_ctx + 1):
            bad_id = "context-over-" + str(size)
            body = {"prompt": [token] * size, "n_predict": 1, "temperature": 0, "stream": False,
                    "cache_prompt": False, "request_class": "background", "client_req_id": bad_id}
            result = await record(out, bad_id, "/completion", body)
            assert result["http_status"] == 400, result
            error = result["json"]["error"]
            assert error["type"] == "exceed_context_size_error", result
            assert str(size) in error["message"] and str(n_ctx) in error["message"], result
            assert error["n_prompt_tokens"] == size and error["n_ctx"] == n_ctx, result
            recover_ids = []
            recovery = []
            for category in ("background", "interactive"):
                req_id = "recover-" + str(size) + "-" + category
                recovery.append(await short_request(out, req_id, category))
                recover_ids.append(req_id)
            await idle(out, "after-rejection-" + str(size))
            cases.append({"input_tokens": size, "n_predict": 1, "http_status": 400,
                          "error_type": error["type"], "error": error,
                          "client_elapsed_ms": result["client_elapsed_ms"],
                          "recovery_ids": recover_ids, "recovery": recovery})
        slots = await record(out, "slots-after", "/slots")
        assert slots["http_status"] == 200 and len(slots["json"]) == 2
        assert all(s["n_ctx"] == n_ctx and not s["is_processing"] for s in slots["json"])
        await runner.prior.engine.stop_server(server, log)
        log = None
        assert server.returncode == 0, server.returncode
        accounting = gate.verify_cap_events(out / "server.log", 1)
        assert accounting["events"] == 18 and accounting["peak_running_background"] == 1, accounting
        events = gate.cap_events(out / "server.log")
        ids = ["warm-0", "warm-1", "context-legal-2047"]
        ids += ["context-over-" + str(s) for s in (n_ctx, n_ctx + 1)]
        ids += [i for c in cases for i in c["recovery_ids"]]
        for req_id in ids:
            launches = [e for e in events if e["event"] == "LAUNCH" and e["clientid"] == req_id]
            releases = [e for e in events if e["event"] == "RELEASE" and e["clientid"] == req_id]
            assert len(launches) == len(releases) == 1, {"id": req_id, "launch": launches, "release": releases}
            assert int(releases[0]["event_ms"]) >= int(launches[0]["event_ms"])
            expected_class = "interactive" if req_id.startswith("warm-") or req_id.endswith("-interactive") else "background"
            assert launches[0]["class"] == releases[0]["class"] == expected_class
        # Context validation happens after a task takes a slot. Rejection must
        # release its actual slot exactly once; no 'never launched' assertion.
        for case in cases:
            bad = "context-over-" + str(case["input_tokens"])
            bad_release = next(e for e in events if e["event"] == "RELEASE" and e["clientid"] == bad)
            next_launch = next(e for e in events if e["event"] == "LAUNCH" and e["clientid"] == case["recovery_ids"][0])
            assert int(next_launch["event_ms"]) >= int(bad_release["event_ms"])
        assert runner.binary_inventory(engine) == inventory
        assert runner.sha(model) == model_sha
        for rel, digest in source_hashes.items():
            assert runner.sha(ROOT / rel) == digest, rel
        assert all(runner.sha(engine_root / rel) == digest for rel, digest in engine_hashes.items())
        dump(out / "engine-binary-inventory-after.json", runner.binary_inventory(engine))
        dump(out / "model-sha256-after.json", {"model": str(model), "sha256": runner.sha(model)})
        capture_gpu(out, "gpu-after.txt")
        summary = {"status": "passed", "study": "g0-context-boundary-v1", "started_at": started,
                   "finished_at": datetime.now().astimezone().isoformat(), "slots": 2, "context_per_slot": n_ctx,
                   "completion_requests": 9, "warmup_completed": 2, "warmups": warmups,
                   "legal_boundary_completed": 1, "legal_boundary": legal_observation,
                   "context_rejected": 2, "valid_recovery_completed": 4, "cases": cases, "accounting": accounting,
                   "server_exit_code": server.returncode, "model_sha256": model_sha, "engine_commit": commit,
                   "source_runtime_model_unchanged": True,
                   "scope": "G0 input-context boundary, slot observability, same-service recovery, fresh-process check. Not full KV reservation, input+output hard-budget acceptance, physical memory reservation, fresh build reproducibility, performance or general starvation proof."}
        dump(out / "summary.json", summary)
        dump(out / "status.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    except BaseException as exc:
        dump(out / "status.json", {"status": "failed", "started_at": started,
             "finished_at": datetime.now().astimezone().isoformat(), "error": repr(exc)})
        raise
    finally:
        if server is not None and log is not None:
            await runner.prior.engine.stop_server(server, log)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--engine-bin", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=runner.DEFAULT_MODEL)
    asyncio.run(asyncio.wait_for(verify(parser.parse_args()), timeout=600))
