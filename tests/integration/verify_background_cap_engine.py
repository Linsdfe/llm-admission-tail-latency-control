#!/usr/bin/env python3
"""Real-engine three-strategy and hard background-running-cap correctness probes."""
import argparse
import asyncio
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/replay"))
from simple_policy_replay_v2 import dump, replay

spec = importlib.util.spec_from_file_location("simple_runner", ROOT / "scripts/replay/78_run_background_cap.py")
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


async def lifecycle(out, engine_bin, model, strategy="simple"):
    directory = out / f"lifecycle-{strategy}"
    directory.mkdir()
    server, log = await runner.start_server(directory, engine_bin, model, strategy, 5000)
    try:
        await warm(directory)
        rows = [
            {"req_id": "cancel", "category": "background", "arrival_offset_ms": 0,
             "prompt": "请详细解释大语言模型推理的每一步，给出大量例子。", "max_tokens": 384, "cancel_after_ms": 150},
            {"req_id": "timeout", "category": "background", "arrival_offset_ms": 20,
             "prompt": "请详细解释批处理和推理资源管理。", "max_tokens": 384, "timeout_seconds": .05 if strategy == "capped" else .1},
            {"req_id": "cutoff", "category": "background", "arrival_offset_ms": 40,
             "prompt": "请详细解释缓存管理和请求调度。", "max_tokens": 384},
            {"req_id": "notreleased", "category": "interactive", "arrival_offset_ms": 0,
             "release_offset_ms": 2000, "prompt": "你好", "max_tokens": 8}]
        result = await replay(rows, directory / "faults", runner.URL, .3, .2, 30)
        assert result["terminal_counts"] == {"completed": 0, "rejected": 0, "timeout": 1,
                                             "cancelled": 1, "failed": 0, "unfinished": 2}, result
        await asyncio.sleep(1.5)
        recover = [{"req_id": f"recover{i}", "category": "background" if i == 0 else "interactive", "arrival_offset_ms": i * 500,
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
    accounting = verify_cap_events(directory / "server.log", 1 if strategy == "capped" else 0)
    timeout_observation = None
    if strategy == "capped":
        assert "cancel" in launched and "cutoff" in launched and "recover0" in launched
        enqueue_events = log_events(directory / "server.log", "SCHED_ENQUEUE")
        assert any(e["clientid"] == "timeout" for e in enqueue_events), "Timeout fixture must reach the engine queue"
        event_rows = cap_events(directory / "server.log")
        event_launch = {e["clientid"]: e for e in event_rows if e["event"] == "LAUNCH"}
        event_release = {e["clientid"]: e for e in event_rows if e["event"] == "RELEASE"}
        queued_timeout = next(e for e in enqueue_events if e["clientid"] == "timeout")
        assert int(event_launch["cancel"]["event_ms"]) <= int(queued_timeout["enqueue_ms"]) < int(event_release["cancel"]["event_ms"]), "Timeout must have waited behind a running background"
        assert int(event_launch["cutoff"]["event_ms"]) >= int(event_release["cancel"]["event_ms"])
        assert int(event_launch["recover0"]["event_ms"]) >= int(event_release["cutoff"]["event_ms"])
        timeout_launched = "timeout" in event_launch
        if timeout_launched:
            assert "timeout" in event_release
            assert int(event_launch["timeout"]["event_ms"]) >= int(event_release["cancel"]["event_ms"])
            assert int(event_launch["cutoff"]["event_ms"]) >= int(event_release["timeout"]["event_ms"])
        timeout_observation = {
            "client_terminal": "timeout", "server_launched": timeout_launched,
            "server_released_if_launched": ("timeout" in event_release) if timeout_launched else None,
            "scope": "Client deadline closes its connection; server may detect that disconnect only after starting the request. Successful service cleanup/cap/recovery is asserted here. No-launch after an already enqueued queue CANCEL is covered by the separate deterministic three-mode barrier regression. Client and server clocks are not subtracted."}
        cap_launches = [e for e in cap_events(directory / "server.log") if e["event"] == "LAUNCH"]
        assert all(int(e["running_background_after"]) <= 1 for e in cap_launches)
    info = {"strategy": strategy, "faults": result["terminal_counts"], "recovery_completed": 2,
            "accounting": accounting, "timeout_service_lifecycle": timeout_observation,
            "server_exit_code": server.returncode,
            "scope": "External lifecycle and real service recovery; not formal hard-ledger acceptance"}
    dump(directory / "probe.json", info)
    print(info, flush=True)
    return info


def log_events(path, marker):
    result = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if marker in line:
            result.append(dict(item.split("=", 1) for item in line.split() if "=" in item))
    return result


def cap_events(path):
    result = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        for event in ("LAUNCH", "RELEASE"):
            if "SCHED_CAP_" + event in line:
                result.append({"event": event, **dict(item.split("=", 1) for item in line.split() if "=" in item)})
    return result


def verify_cap_events(path, cap):
    events = cap_events(path)
    assert events, "No cap accounting events"
    active = {}
    count = peak = 0
    last_ms = -1
    for e in events:
        assert int(e["cap"]) == cap, e
        assert int(e["event_ms"]) >= last_ms, e
        last_ms = int(e["event_ms"])
        before, after = (int(e[k]) for k in ("running_background_before", "running_background_after"))
        assert before == count, e
        slot = int(e["slot"])
        if e["event"] == "LAUNCH":
            assert slot not in active, (active, e)
            active[slot] = e
            assert after == before + (e["class"] == "background"), e
        else:
            previous = active.pop(slot)
            assert previous["id"] == e["id"] and previous["clientid"] == e["clientid"], e
            assert after == before - (e["class"] == "background"), e
        count = after
        peak = max(peak, before, after)
        assert count >= 0 and (cap == 0 or peak <= cap), e
    assert not active and count == 0, active
    return {"cap": cap, "events": len(events), "peak_running_background": peak,
            "final_running_background": count, "final_active_slots": len(active)}


async def cap_ordering(out, engine_bin, model, aging_ms):
    directory = out / f"cap-order-{aging_ms}"
    directory.mkdir()
    server, log = await runner.start_server(directory, engine_bin, model, "capped", aging_ms)
    try:
        await warm(directory)
        original = runner.jsonlines(ROOT / "results/raw/prestudy/20261020-234210-p0-e0e1/r1-E1/replay/trace.jsonl")
        backgrounds = [r for r in original if r["category"] == "background"]
        rows = [dict(backgrounds[k], req_id=f"capB{k}", arrival_offset_ms=k * 40) for k in range(3)]
        rows.append({"req_id": "capI", "category": "interactive", "arrival_offset_ms": 120,
                     "prompt": "请只回复：可以。", "max_tokens": 16, "temperature": 0})
        result = await replay(rows, directory / "replay", runner.URL, 1, 45, 45)
        assert result["terminal_counts"]["completed"] == 4, result
        await asyncio.sleep(.3)
        await asyncio.to_thread(runner.fetch, "/metrics", directory / "metrics-after.txt")
        assert all(runner.metric_zero(directory / "metrics-after.txt").values())
    finally:
        await runner.prior.engine.stop_server(server, log)
    path = directory / "server.log"
    accounting = verify_cap_events(path, 1)
    events = cap_events(path)
    by_id = {e["clientid"]: e for e in events if e["event"] == "LAUNCH"}
    releases = {e["clientid"]: e for e in events if e["event"] == "RELEASE"}
    required = ("capB0", "capB1", "capB2", "capI")
    assert all(k in by_id and k in releases for k in required)
    assert int(by_id["capB0"]["event_ms"]) < int(by_id["capI"]["event_ms"]) < int(releases["capB0"]["event_ms"]), "Interactive must use the other slot while B0 runs"
    assert by_id["capB0"]["slot"] != by_id["capI"]["slot"]
    assert int(by_id["capB1"]["event_ms"]) >= int(releases["capB0"]["event_ms"])
    assert int(by_id["capB2"]["event_ms"]) >= int(releases["capB1"]["event_ms"])
    enqueue = {e["clientid"]: e for e in log_events(path, "SCHED_ENQUEUE")}
    launch = {e["clientid"]: e for e in log_events(path, "SCHED_LAUNCH")}
    for k in ("capB1", "capB2"):
        assert int(enqueue[k]["enqueue_ms"]) < int(releases["capB0"]["event_ms"]), "Queued B must arrive while B0 active"
        assert int(enqueue[k]["enqueue_ms"]) == int(launch[k]["enqueue_ms"]), "Deferral must preserve first enqueue age"
        assert int(launch[k]["wait_ms"]) == int(launch[k]["launch_ms"]) - int(enqueue[k]["enqueue_ms"])
        if aging_ms == 1:
            assert int(launch[k]["wait_ms"]) >= aging_ms
            assert any(e["clientid"] == k and e["reason"] == "background_aged" for e in log_events(path, "SCHED_SELECT")), "Age promotion branch not exercised"
    observed = [e["clientid"] for e in events if e["event"] == "LAUNCH" and e["clientid"] in required]
    assert observed == ["capB0", "capI", "capB1", "capB2"], observed
    info = {"strategy": "capped", "aging_ms": aging_ms, "observed": observed,
            "terminal_counts": result["terminal_counts"], "accounting": accounting,
            "age_preserved": True, "interactive_uses_other_slot": True,
            "aged_cannot_bypass_cap": aging_ms == 1, "server_exit_code": server.returncode}
    dump(directory / "probe.json", info)
    print(info, flush=True)
    return info


async def cap_rejection_recovery(out, engine_bin, model):
    directory = out / "cap-rejection-recovery"
    directory.mkdir()
    server, log = await runner.start_server(directory, engine_bin, model, "capped", 5000)
    body = {"messages": [{"role": "user", "content": "请详细解释大语言模型推理。"}],
            "max_tokens": 16, "temperature": 0, "stream": False, "n": 2,
            "request_class": "background", "client_req_id": "bad-n2"}
    dump(directory / "rejected-request.json", body)
    def send_bad():
        request = urllib.request.Request(runner.URL + "/v1/chat/completions",
                                         data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return {"http_status": response.status, "body": response.read().decode("utf-8")}
        except urllib.error.HTTPError as exc:
            return {"http_status": exc.code, "body": exc.read().decode("utf-8")}
    async def delayed_bad():
        # The frozen replay uses a 250ms clock-start hold. Synchronize this
        # external n=2 probe to a real server launch instead of that client clock.
        for _ in range(1000):
            text = (directory / "server.log").read_text(encoding="utf-8", errors="replace")
            if any("SCHED_CAP_LAUNCH" in line and "clientid=reject-blocker " in line for line in text.splitlines()):
                return await asyncio.to_thread(send_bad)
            await asyncio.sleep(.01)
        raise AssertionError("Background blocker never reached server launch")
    try:
        await warm(directory)
        original = runner.jsonlines(ROOT / "results/raw/prestudy/20261020-234210-p0-e0e1/r1-E1/replay/trace.jsonl")
        backgrounds = [r for r in original if r["category"] == "background"]
        rows = [dict(backgrounds[0], req_id="reject-blocker", arrival_offset_ms=0),
                dict(backgrounds[1], req_id="after-reject", arrival_offset_ms=500)]
        result, rejected = await asyncio.gather(replay(rows, directory / "replay", runner.URL, 1, 45, 45), delayed_bad())
        dump(directory / "rejected-response.json", rejected)
        assert rejected["http_status"] == 400 and "n=1 only" in rejected["body"], rejected
        assert result["terminal_counts"]["completed"] == 2, result
        await asyncio.sleep(.3)
        await asyncio.to_thread(runner.fetch, "/metrics", directory / "metrics-after.txt")
        assert all(runner.metric_zero(directory / "metrics-after.txt").values())
    finally:
        await runner.prior.engine.stop_server(server, log)
    path = directory / "server.log"
    accounting = verify_cap_events(path, 1)
    enqueue = log_events(path, "SCHED_ENQUEUE")
    by_enqueue = {e["clientid"]: e for e in enqueue}
    assert "bad-n2" in by_enqueue and "after-reject" in by_enqueue
    events = cap_events(path)
    launched = {e["clientid"]: e for e in events if e["event"] == "LAUNCH"}
    released = {e["clientid"]: e for e in events if e["event"] == "RELEASE"}
    assert "bad-n2" not in launched, "Rejected background parent must never consume running credit"
    bad_enqueue = int(by_enqueue["bad-n2"]["enqueue_ms"])
    good_enqueue = int(by_enqueue["after-reject"]["enqueue_ms"])
    blocker_release = int(released["reject-blocker"]["event_ms"])
    assert int(launched["reject-blocker"]["event_ms"]) <= bad_enqueue < good_enqueue < blocker_release
    assert int(launched["after-reject"]["event_ms"]) >= blocker_release
    info = {"strategy": "capped", "rejected_http_status": rejected["http_status"],
            "valid_completed": result["terminal_counts"]["completed"], "accounting": accounting,
            "bad_never_launched": True, "valid_request_after_rejection_started": True,
            "server_exit_code": server.returncode,
            "scope": "Correctness regression for a rejected queued background followed by valid queued work; not performance"}
    dump(directory / "probe.json", info)
    print(info, flush=True)
    return info


async def verify(args):
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = datetime.now().astimezone().isoformat()
    dump(out / "status.json", {"status": "running", "started_at": started})
    engine_bin = args.engine_bin.resolve(strict=True)
    model = args.model.resolve(strict=True)
    engine_root = engine_bin.parents[2]
    inventory = runner.binary_inventory(engine_bin)
    dump(out / "engine-binary-inventory.json", inventory)
    source_hashes = {}
    for rel in ("tests/integration/verify_background_cap_engine.py", "scripts/replay/78_run_background_cap.py",
                "scripts/replay/simple_policy_replay_v2.py", "patches/background-cap-v1.patch"):
        data = (ROOT / rel).read_bytes()
        target = out / "source" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        source_hashes[rel] = runner.sha(ROOT / rel)
    dump(out / "code-sha256.json", source_hashes)
    engine_sources = {}
    for name in ("server-task.h", "server-queue.h", "server-queue.cpp", "server-context.cpp"):
        rel = "tools/server/" + name
        data = (engine_root / rel).read_bytes()
        assert data == (ROOT / "patches/background-cap-v1" / rel).read_bytes(), rel
        target = out / "engine-source" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        engine_sources[rel] = runner.sha(engine_root / rel)
    dump(out / "engine-source-sha256.json", engine_sources)
    model_hash = runner.sha(model)
    engine_commit = runner.git_output(engine_root, "rev-parse", "HEAD").strip()
    dump(out / "environment.json", {"engine_root": str(engine_root), "engine_commit": engine_commit,
                                   "model": str(model), "model_sha256": model_hash,
                                   "engine_binary": str(engine_bin), "source_sha256": source_hashes,
                                   "engine_source_sha256": engine_sources, "python": sys.version})
    try:
        cases = [await ordering(out, args.engine_bin, args.model, "original", 5000, ["queuedB", "queuedI"]),
                 await ordering(out, args.engine_bin, args.model, "simple", 3600000, ["queuedI", "queuedB"]),
                 await ordering(out, args.engine_bin, args.model, "simple", 1, ["queuedB", "queuedI"])]
        cap_cases = [await cap_ordering(out, args.engine_bin, args.model, 5000),
                     await cap_ordering(out, args.engine_bin, args.model, 1)]
        recovery = [await lifecycle(out, args.engine_bin, args.model, strategy)
                    for strategy in ("simple", "capped")]
        rejection_recovery = await cap_rejection_recovery(out, args.engine_bin, args.model)
        for info in cases:
            info["accounting"] = verify_cap_events(out / f"order-{info['strategy']}-{info['aging_ms']}" / "server.log", 0)
        assert runner.binary_inventory(args.engine_bin.resolve()) == inventory
        assert all(runner.sha(ROOT / rel) == digest for rel, digest in source_hashes.items()), "Gate source changed during probes"
        assert all(runner.sha(engine_root / rel) == digest for rel, digest in engine_sources.items()), "Engine source changed during probes"
        assert runner.sha(model) == model_hash, "Model changed during probes"
        dump(out / "engine-binary-inventory-after.json", runner.binary_inventory(engine_bin))
        summary = {"status": "passed", "study": "background-cap-v1", "started_at": started, "finished_at": datetime.now().astimezone().isoformat(),
                   "ordering_cases": cases, "cap_cases": cap_cases, "lifecycle": recovery,
                   "rejection_recovery": rejection_recovery,
                   "model_sha256": model_hash, "engine_commit": engine_commit, "engine_inventory_unchanged": True,
                   "source_sha256": source_hashes, "engine_source_sha256": engine_sources,
                   "correctness_aging_probe_ms": 1, "correctness_unaged_probe_ms": 3600000, "performance_aging_ms": 5000,
                   "note": "1ms/3600000ms isolate correctness branches, not performance parameter searches. Main data uses5000ms. The cap limits active background requests even when aged; this does not reserve physical GPU memory or guarantee waiting time."}
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
