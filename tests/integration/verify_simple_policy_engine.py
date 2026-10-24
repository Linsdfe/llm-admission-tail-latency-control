#!/usr/bin/env python3
"""Real-engine correctness probes, separate from frozen performance runs."""
import argparse
import asyncio
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/replay"))
from simple_policy_replay import dump, replay

spec = importlib.util.spec_from_file_location("simple_runner", ROOT / "scripts/replay/76_run_simple_policy.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def launches(path):
    result = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "SCHED_LAUNCH" in line:
            result.append(dict(item.split("=", 1) for item in line.split() if "=" in item))
    return result


async def warm(directory):
    rows = [{"req_id": f"warm{i}", "category": "interactive", "arrival_offset_ms": i * 500,
             "prompt": "请回复：准备好了。", "max_tokens": 16} for i in range(2)]
    result = await replay(rows, directory / "warmup", runner.URL, 1, 10)
    assert result["terminal_counts"]["completed"] == 2


async def ordering(out, engine_bin, model, strategy, aging_ms, expected):
    directory = out / f"order-{strategy}-{aging_ms}"
    directory.mkdir()
    server, log = await runner.start_server(directory, engine_bin, model, strategy, aging_ms)
    try:
        await warm(directory)
        original = runner.jsonlines(ROOT / "results/raw/prestudy/20261020-234210-p0-e0e1/r1-E1/replay/trace.jsonl")
        backgrounds = [r for r in original if r["category"] == "background"]
        rows = [dict(backgrounds[k], req_id=f"block{k}", arrival_offset_ms=k * 40) for k in range(2)]
        rows += [dict(backgrounds[2], req_id="queuedB", arrival_offset_ms=80),
                 {"req_id": "queuedI", "category": "interactive", "arrival_offset_ms": 120,
                  "prompt": "请只回复：可以。", "max_tokens": 16, "temperature": 0}]
        result = await replay(rows, directory / "replay", runner.URL, 1, 30, 30)
        assert result["terminal_counts"]["completed"] == 4, result
        await asyncio.sleep(.3)
        await asyncio.to_thread(runner.fetch, "/metrics", directory / "metrics-after.txt")
        assert all(runner.metric_zero(directory / "metrics-after.txt").values())
    finally:
        await runner.prior.engine.stop_server(server, log)
    events = launches(directory / "server.log")
    targets = {r["clientid"]: r for r in events if r.get("clientid") in ("queuedB", "queuedI")}
    blockers = [r for r in events if r.get("clientid") in ("block0", "block1")]
    assert len(blockers) == 2 and len(targets) == 2
    first_target_launch = min(int(e["launch_ms"]) for e in targets.values())
    assert max(int(e["launch_ms"]) for e in blockers) < first_target_launch
    assert max(int(e["enqueue_ms"]) for e in targets.values()) < first_target_launch
    assert int(targets["queuedB"]["enqueue_ms"]) <= int(targets["queuedI"]["enqueue_ms"])
    observed = [r["clientid"] for r in events if r.get("clientid") in ("queuedB", "queuedI")]
    assert observed == expected, {"strategy": strategy, "aging_ms": aging_ms, "observed": observed, "expected": expected}
    for event in events:
        if event.get("clientid") == "queuedB":
            assert event["class"] == "background"
        if event.get("clientid") == "queuedI":
            assert event["class"] == "interactive"
    if strategy == "simple" and aging_ms == 1:
        assert any("clientid=queuedB" in line and "reason=background_aged" in line
                   for line in (directory / "server.log").read_text(encoding="utf-8").splitlines())
    info = {"strategy": strategy, "aging_ms": aging_ms, "expected": expected, "observed": observed,
            "terminal_counts": result["terminal_counts"], "server_exit_code": server.returncode}
    dump(directory / "probe.json", info)
    print(info, flush=True)
    return info


async def lifecycle(out, engine_bin, model):
    directory = out / "lifecycle-simple"
    directory.mkdir()
    server, log = await runner.start_server(directory, engine_bin, model, "simple", 5000)
    try:
        await warm(directory)
        rows = [
            {"req_id": "cancel", "category": "background", "arrival_offset_ms": 0,
             "prompt": "请详细解释大语言模型推理的每一步，给出大量例子。", "max_tokens": 384, "cancel_after_ms": 150},
            {"req_id": "timeout", "category": "background", "arrival_offset_ms": 20,
             "prompt": "请详细解释批处理和推理资源管理。", "max_tokens": 384, "timeout_seconds": .1},
            {"req_id": "cutoff", "category": "background", "arrival_offset_ms": 40,
             "prompt": "请详细解释缓存管理和请求调度。", "max_tokens": 384},
            {"req_id": "notreleased", "category": "interactive", "arrival_offset_ms": 0,
             "release_offset_ms": 2000, "prompt": "你好", "max_tokens": 8}]
        result = await replay(rows, directory / "faults", runner.URL, .3, .2, 30)
        assert result["terminal_counts"] == {"completed": 0, "rejected": 0, "timeout": 1,
                                             "cancelled": 1, "failed": 0, "unfinished": 2}, result
        await asyncio.sleep(1.5)
        recover = [{"req_id": f"recover{i}", "category": "interactive", "arrival_offset_ms": i * 500,
                    "prompt": "请回复：恢复正常。", "max_tokens": 16} for i in range(2)]
        recovery = await replay(recover, directory / "recovery", runner.URL, 1, 15)
        assert recovery["terminal_counts"]["completed"] == 2
        await asyncio.sleep(.3)
        await asyncio.to_thread(runner.fetch, "/metrics", directory / "metrics-after.txt")
        assert all(runner.metric_zero(directory / "metrics-after.txt").values())
    finally:
        await runner.prior.engine.stop_server(server, log)
    launched = [e["clientid"] for e in launches(directory / "server.log")]
    assert "notreleased" not in launched
    info = {"faults": result["terminal_counts"], "recovery_completed": 2, "server_exit_code": server.returncode,
            "scope": "External lifecycle and real service recovery; not formal hard-ledger acceptance"}
    dump(directory / "probe.json", info)
    print(info, flush=True)
    return info


async def verify(args):
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = datetime.now().astimezone().isoformat()
    dump(out / "status.json", {"status": "running", "started_at": started})
    inventory = runner.binary_inventory(args.engine_bin.resolve())
    dump(out / "engine-binary-inventory.json", inventory)
    try:
        cases = [await ordering(out, args.engine_bin, args.model, "original", 5000, ["queuedB", "queuedI"]),
                 await ordering(out, args.engine_bin, args.model, "simple", 3600000, ["queuedI", "queuedB"]),
                 await ordering(out, args.engine_bin, args.model, "simple", 1, ["queuedB", "queuedI"])]
        recovery = await lifecycle(out, args.engine_bin, args.model)
        assert runner.binary_inventory(args.engine_bin.resolve()) == inventory
        summary = {"status": "passed", "started_at": started, "finished_at": datetime.now().astimezone().isoformat(),
                   "ordering_cases": cases, "lifecycle": recovery,
                   "correctness_aging_probe_ms": 1, "correctness_unaged_probe_ms": 3600000, "performance_aging_ms": 5000,
                   "note": "1ms and 3600000ms isolate correctness branches only, not a performance parameter search. Queue unit checks test the 5000ms boundary; main data uses 5000ms. Nonpreemptive queue priority does not guarantee maximum waiting time."}
        dump(out / "summary.json", summary)
        dump(out / "status.json", summary)
    except BaseException as exc:
        dump(out / "status.json", {"status": "failed", "started_at": started,
                                    "finished_at": datetime.now().astimezone().isoformat(), "error": repr(exc)})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--engine-bin", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=runner.DEFAULT_MODEL)
    asyncio.run(verify(parser.parse_args()))
