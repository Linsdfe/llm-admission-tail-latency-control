#!/usr/bin/env python3
"""Append-only P1 input/measurement/load development validation on one GPU."""
import argparse
import asyncio
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

from p1_development_replay import replay, dump, STATES
from p1_workload import build_candidates, materialize_trace, make_scenario

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("p1_prior_runner", ROOT / "scripts/replay/78_run_background_cap.py")
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
gspec = importlib.util.spec_from_file_location("p1_engine_gate", ROOT / "tests/integration/verify_background_cap_engine.py")
gate = importlib.util.module_from_spec(gspec)
gspec.loader.exec_module(gate)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def write_rows(path, values):
    with Path(path).open("x", encoding="utf-8") as output:
        for row in values:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")


def free_port():
    with socket.socket() as sock:
        assert sock.connect_ex(("127.0.0.1", 8080)) != 0, "8080 occupied; do not reuse/kill another process"


def capture_gpu(path):
    with path.open("xb") as output:
        subprocess.run(["nvidia-smi"], stdout=output, stderr=subprocess.STDOUT, timeout=15, check=True)


class Tokenizer:
    def __init__(self, directory):
        self.output = (directory / "tokenization.jsonl").open("x", encoding="utf-8")
        self.count = 0

    def __call__(self, prompt):
        body = {"content": prompt, "add_special": True, "parse_special": True}
        req = urllib.request.Request(prior.URL + "/tokenize", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        start = time.monotonic_ns()
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
            code = response.status
        value = json.loads(raw)
        self.output.write(json.dumps({"index": self.count, "request": body, "http_status": code,
            "response_body": raw, "elapsed_ms": (time.monotonic_ns()-start)/1e6}, ensure_ascii=False) + "\n")
        self.output.flush()
        self.count += 1
        assert code == 200 and all(type(i) is int for i in value["tokens"])
        return value["tokens"]

    def close(self):
        self.output.close()


async def await_idle(directory, stem):
    for _ in range(120):
        await asyncio.to_thread(prior.fetch, "/metrics", directory / (stem + "-metrics.txt"))
        if all(prior.metric_zero(directory / (stem + "-metrics.txt")).values()):
            break
        await asyncio.sleep(.25)
    zero = prior.metric_zero(directory / (stem + "-metrics.txt"))
    await asyncio.to_thread(prior.fetch, "/slots", directory / (stem + "-slots.json"))
    slots = read(directory / (stem + "-slots.json"))
    assert all(zero.values()) and len(slots) == 2 and all(not s["is_processing"] for s in slots), "service not idle after recovery grace"


async def warm(directory):
    prompt = "<|im_start|>system\n你是简洁的助手。<|im_end|>\n<|im_start|>user\n请说准备好了。<|im_end|>\n<|im_start|>assistant\n"
    trace = [{"req_id": f"warm{i}", "category": "interactive", "arrival_offset_ms": i*500,
        "prompt": prompt, "max_tokens": 8} for i in range(2)]
    summary = await replay(trace, directory / "warmup", prior.URL, 1, 15, 10, 50, 360)
    assert summary["terminal_counts"]["completed"] == 2


def audit_replay(directory, expected):
    recorded = rows(directory / "client.jsonl")
    summary = read(directory / "summary.json")
    assert len(recorded) == len(expected) == summary["planned"]
    assert {r["req_id"] for r in recorded} == {r["req_id"] for r in expected}
    assert len({r["req_id"] for r in recorded}) == len(recorded)
    assert len(list((directory / "requests").glob("*.json"))) == len(recorded)
    flags = []
    by_id = {r["req_id"]:r for r in expected}
    for row in recorded:
        assert all(row[k] == value for k,value in by_id[row["req_id"]].items())
        assert read(directory / "requests" / (row["req_id"] + ".json")) == row
        assert row["terminal"] in STATES
        if row["actual_send_offset_ms"] is not None:
            assert row["end_offset_ms"] >= row["actual_send_offset_ms"] >= row["arrival_offset_ms"]
            if row["send_lag_ms"] > 50:
                flags.append("send_lag_exceeded_50ms")
        if row["first_content_offset_ms"] is not None:
            assert row["actual_send_offset_ms"] <= row["first_content_offset_ms"] <= row["end_offset_ms"]
            assert abs(row["total_first_ms"] - row["first_content_offset_ms"] + row["arrival_offset_ms"]) < .001
        assert row["input_tokens"] + row["max_tokens"] + 1 <= 2048
        if row["terminal"] == "completed":
            final = row["native_final"]
            assert final["tokens_evaluated"] == row["input_tokens"]
            assert row["new_prompt_tokens"] + row["cached_prompt_tokens"] == row["input_tokens"]
            assert row["output_tokens_known"] and row["output_tokens"] == row["timings"]["predicted_n"] == final["tokens_predicted"]
            assert 0 < row["output_tokens"] <= row["max_tokens"]
            if final.get("truncated") is not False:
                flags.append("unexpected_context_truncation")
    assert sum(summary["terminal_counts"].values()) == len(recorded)
    assert all(sum(r["terminal"] == s for r in recorded) == summary["terminal_counts"][s] for s in STATES)
    return {"status": "passed" if not flags else "needs_review", "flags": sorted(set(flags)),
            "planned":len(expected), "terminal_counts":summary["terminal_counts"],
            "send_lag_max_ms":summary["send_lag_max_ms"],
            "input_length_checked_completed":sum(r["terminal"] == "completed" for r in recorded)}


async def run(args):
    cfg = read(args.config)
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    completed = []
    sampler = sample_log = server = log = None
    dump(out / "batch-status.json", {"status":"preparing", "started_at":datetime.now().astimezone().isoformat()})
    try:
        assert cfg["study"] == "p1-development-validation-v1" and len(cfg["execution_order"]) == 9
        assert cfg["final_test_access"] == "forbidden" and cfg["strategy_mode"] == "original"
        assert shutil.disk_usage(out).free >= cfg["min_free_bytes_before_batch"]
        free_port()
        engine = args.engine_bin.resolve(strict=True)
        model = args.model.resolve(strict=True)
        engine_root = engine.parents[2]
        runtime = prior.binary_inventory(engine)
        assert runtime == read(ROOT / "results/raw/development/20261023-1515-background-cap-engine-v3/engine-binary-inventory.json")
        model_sha = await asyncio.to_thread(prior.sha, model)
        assert model_sha == cfg["model_sha256"]
        commit = prior.git_output(engine_root, "rev-parse", "HEAD").strip()
        assert commit == cfg["engine_commit"]
        client_gate = read(args.client_gate_dir / "summary.json")
        assert client_gate["status"] == "passed", "new native client gate must pass before model collection"
        assert all(prior.sha(ROOT / rel) == digest for rel,digest in client_gate["source_sha256"].items()), "client source differs from passed gate"
        shutil.copytree(args.client_gate_dir, out / "client-gate")
        source_paths = list(read(ROOT / "configs/prestudy-background-cap-v1.json")["source_files"])
        source_paths += ["scripts/replay/79_run_p1_development.py", "scripts/replay/p1_development_replay.py",
            "scripts/replay/p1_workload.py", "configs/p1-development-workload-v1.json",
            "configs/p1-development-validation-v1.json", cfg["protocol_path"],
            "tests/integration/verify_p1_development_replay.py", "tests/unit/test_p1_workload.py"]
        hashes = {}
        for rel in source_paths:
            source = ROOT / rel
            dest = out / "source" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(source.read_bytes())
            hashes[rel] = prior.sha(source)
        engine_hashes = read(ROOT / "results/raw/development/20261023-2256-g0-context-boundary-v1/engine-source-sha256.json")
        for rel,digest in engine_hashes.items():
            source = engine_root / rel
            assert prior.sha(source) == digest
            dest = out / "engine-source" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(source.read_bytes())
        dump(out / "source-sha256.json", hashes)
        dump(out / "engine-source-sha256.json", engine_hashes)
        dump(out / "engine-runtime-sha256.json", runtime)
        dump(out / "environment.json", {"recorded_at":datetime.now().astimezone().isoformat(),
            "python":sys.version, "kernel":Path("/proc/version").read_text(), "engine_commit":commit,
            "model":str(model), "model_sha256":model_sha, "engine_binary":str(engine),
            "project_commit":prior.git_output(ROOT,"rev-parse","HEAD").strip(),
            "project_working_tree":prior.git_output(ROOT,"status","--short"),
            "scope":"Single repaired original-order prototype, not formal B0/B1, no M/B3 or hard ledger.",
            "client_gate":str(args.client_gate_dir), "config":cfg})
        capture_gpu(out / "gpu-before.txt")
        sample_log = (out / "gpu-samples.csv").open("xb")
        sampler = await asyncio.create_subprocess_exec("nvidia-smi", "--query-gpu=timestamp,memory.used,utilization.gpu,temperature.gpu,power.draw",
            "--format=csv", "--loop-ms=1000", stdout=sample_log, stderr=asyncio.subprocess.STDOUT)
        prep = out / "prep"
        prep.mkdir()
        server, log = await prior.start_server(prep, engine, model, "original", 5000)
        props = read(prep / "props.json")
        assert props["total_slots"] == 2 and props["default_generation_settings"]["n_ctx"] == 2048
        await asyncio.wait_for(warm(prep),60)
        tokenizer = Tokenizer(prep)
        traces = {}
        try:
            cal = await asyncio.to_thread(materialize_trace, build_candidates("calibration",cfg["calibration_seed"]), tokenizer)
            write_rows(prep / "calibration-trace.jsonl",cal)
            for repeat,seed in enumerate(cfg["development_seeds"],1):
                print(f"TOKENIZE development repeat={repeat} seed={seed}",flush=True)
                base = await asyncio.to_thread(materialize_trace,build_candidates("development",seed),tokenizer)
                for scene in ("main_bursty_mixed","boundary_steady","boundary_sparse"):
                    trace = make_scenario(base,scene)
                    traces[(repeat,scene)] = trace
                    write_rows(out / f"trace-r{repeat}-{scene}.jsonl",trace)
            dump(prep / "tokenization-summary.json", {"calls":tokenizer.count,"final_inputs_generated":False})
        finally:
            tokenizer.close()
        cal_dir = prep / "calibration"
        cal_dir.mkdir()
        cal_audits = []
        for index,row in enumerate(cal,1):
            one = dict(row,arrival_offset_ms=0)
            directory = cal_dir / f"c{index:02}"
            directory.mkdir()
            await replay([one],directory / "replay",prior.URL,.01,45,45,50,360)
            audit = audit_replay(directory / "replay",[one])
            cal_audits.append(audit)
            print(f"CAL {index}/24 class={row['category']} input={row['input_tokens']} states={audit['terminal_counts']}",flush=True)
            assert not audit["flags"], audit
            assert audit["terminal_counts"]["completed"] == 1, "calibration request failed; retain evidence and review before main runs"
        dump(prep / "calibration-audits.json",cal_audits)
        await await_idle(prep,"after-calibration")
        await prior.prior.engine.stop_server(server,log)
        log = None
        assert server.returncode == 0
        prep_account = gate.verify_cap_events(prep / "server.log",0)
        dump(prep / "accounting.json",prep_account)
        assert prep_account["events"] == 52
        dump(out / "batch-status.json",{"status":"running","calibration_requests":24,"runs_completed":0})
        for item in cfg["execution_order"]:
            assert time.monotonic()-started < cfg["batch_timeout_seconds"]
            assert shutil.disk_usage(out).free >= cfg["min_free_bytes_before_run"]
            assert sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) < cfg["max_batch_bytes"]
            assert prior.binary_inventory(engine) == runtime
            assert all(prior.sha(ROOT/rel) == digest for rel,digest in hashes.items())
            free_port()
            repeat,scene = item["repeat"],item["scenario"]
            directory = out / f"r{repeat}-{scene}-prototype"
            directory.mkdir()
            manifest = {"status":"starting", "stage":"development", "run_id":out.name+"/"+directory.name,
                **item,"strategy":"prototype_original_order","started_at":datetime.now().astimezone().isoformat(),
                "source_snapshot":"../source","not_formal_B0_B1":True,"model_sha256":model_sha}
            dump(directory / "manifest.json",manifest)
            await asyncio.sleep(cfg["idle_seconds"])
            capture_gpu(directory / "gpu-after-idle.txt")
            server,log = await prior.start_server(directory,engine,model,"original",5000)
            try:
                await asyncio.wait_for(warm(directory),60)
                await await_idle(directory,"before-measurement")
                print(f"START {directory.name}: {len(traces[(repeat,scene)])} requests",flush=True)
                summary = await replay(traces[(repeat,scene)],directory / "replay",prior.URL,
                    cfg["measurement_seconds"],cfg["drain_seconds"],cfg["request_timeout_seconds"],
                    cfg["send_lag_limit_ms"],cfg["transport_timeout_seconds"])
                await await_idle(directory,"after-measurement")
            finally:
                await prior.prior.engine.stop_server(server,log)
                log = None
                manifest["server_exit_code"] = server.returncode
                manifest["finished_at"] = datetime.now().astimezone().isoformat()
                dump(directory / "manifest.json",manifest)
            audit = audit_replay(directory / "replay",traces[(repeat,scene)])
            audit["accounting"] = gate.verify_cap_events(directory / "server.log",0)
            assert server.returncode == 0
            expected_events = 2*(2+summary["terminal_counts"]["completed"])
            if summary["terminal_counts"]["completed"] == summary["planned"]:
                assert audit["accounting"]["events"] == expected_events
            manifest.update(status="completed" if not audit["flags"] else "needs_review", audit=audit,
                trace_sha256=prior.sha(directory / "replay/trace.jsonl"))
            dump(directory / "audit.json",audit)
            dump(directory / "manifest.json",manifest)
            completed.append({**item,"directory":directory.name,"audit":audit,"summary":summary})
            dump(out / "batch-summary.json",completed)
            dump(out / "batch-status.json",{"status":"running","runs_completed":len(completed),"elapsed_seconds":time.monotonic()-started})
            print(f"DONE {directory.name}: {audit['terminal_counts']} lag={audit['send_lag_max_ms']} flags={audit['flags']}",flush=True)
            assert not audit["flags"], audit
        assert prior.binary_inventory(engine) == runtime
        assert all(prior.sha(ROOT/rel) == digest for rel,digest in hashes.items())
        assert all(prior.sha(engine_root/rel) == digest for rel,digest in engine_hashes.items())
        assert await asyncio.to_thread(prior.sha,model) == model_sha
        dump(out / "engine-runtime-sha256-after.json",prior.binary_inventory(engine))
        dump(out / "model-sha256-after.json",{"path":str(model),"sha256":model_sha})
        dump(out / "batch-status.json",{"status":"completed","runs":len(completed),"calibration_requests":24,
            "measurement_requests":sum(i["summary"]["planned"] for i in completed),"warmup_requests":20,
            "finished_at":datetime.now().astimezone().isoformat(),"elapsed_seconds":time.monotonic()-started,
            "source_runtime_model_unchanged":True,"final_test_generated_or_read":False})
    except BaseException as exc:
        dump(out / "batch-status.json",{"status":"failed","runs_completed":len(completed),"error":repr(exc),
            "finished_at":datetime.now().astimezone().isoformat(),"elapsed_seconds":time.monotonic()-started})
        raise
    finally:
        if server is not None and log is not None:
            await prior.prior.engine.stop_server(server,log)
        if sampler is not None and sampler.returncode is None:
            sampler.terminate()
            await sampler.wait()
        if sample_log is not None:
            sample_log.close()
        capture_gpu(out / "gpu-after.txt")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config",type=Path,default=ROOT / "configs/p1-development-validation-v1.json")
    parser.add_argument("--out-dir",type=Path,required=True)
    parser.add_argument("--engine-bin",type=Path,required=True)
    parser.add_argument("--model",type=Path,default=prior.DEFAULT_MODEL)
    parser.add_argument("--client-gate-dir",type=Path,required=True)
    parsed = parser.parse_args()
    asyncio.run(asyncio.wait_for(run(parsed),timeout=read(parsed.config)["batch_timeout_seconds"]))
