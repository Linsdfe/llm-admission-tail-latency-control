#!/usr/bin/env python3
"""Controlled native-SSE gate for P1 collection; no model/GPU required."""
import argparse
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/replay"))
import p1_development_replay as client


def records(directory, expected_count):
    rows = [json.loads(s) for s in (directory / "client.jsonl").read_text(encoding="utf-8").splitlines()]
    files = list((directory / "requests").glob("*.json"))
    assert len(rows) == len(files) == expected_count
    assert len({r["req_id"] for r in rows}) == expected_count
    for row in rows:
        assert json.loads((directory / "requests" / (row["req_id"] + ".json")).read_text(encoding="utf-8")) == row
    return {r["req_id"]: r for r in rows}


def row(req_id, prompt=None, **kwargs):
    return dict(req_id=req_id, category="interactive", arrival_offset_ms=0,
                prompt=prompt or req_id, max_tokens=10, **kwargs)


async def verify(out):
    out.mkdir(parents=True, exist_ok=False)
    (out / "source").mkdir()
    hashes = {}
    for source in (Path(__file__), Path(client.__file__)):
        shutil.copy2(source, out / "source" / source.name)
        hashes[str(source.relative_to(ROOT))] = hashlib.sha256(source.read_bytes()).hexdigest()
    client.dump(out / "source-sha256.json", hashes)
    handlers, writers = set(), set()
    received = {}

    async def serve(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        writers.add(writer)
        try:
            start = await reader.readline()
            if not start:
                return
            assert start.startswith(b"POST /completion HTTP/1.1"), start
            headers = {}
            while (line := await reader.readline()) not in (b"\r\n", b"\n", b""):
                k, v = line.decode().split(":", 1)
                headers[k.lower()] = v.strip()
            if "content-length" not in headers:
                return
            body = json.loads(await reader.readexactly(int(headers["content-length"])))
            assert "messages" not in body and "max_tokens" not in body
            assert body["n_predict"] == 10 and body["stream"] is True
            assert body["ignore_eos"] is False and body["cache_prompt"] is True
            assert body["request_class"] in ("interactive", "background")
            case = body["prompt"]
            received[body["client_req_id"]] = body
            if case in ("slow", "cancel"):
                await asyncio.sleep(2)
            if case == "race":
                await asyncio.sleep(.012)
            if case in ("capacity400", "ordinary400", "http429", "http503"):
                code = 400 if case.endswith("400") else int(case[-3:])
                error_type = "exceed_context_size_error" if case == "capacity400" else "invalid_request_error"
                payload = json.dumps({"error": {"type": error_type, "message": "exact body\nkept"}}, indent=2).encode()
                writer.write(f"HTTP/1.1 {code} Test\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode() + payload)
                await writer.drain()
                return
            writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Type: text/event-stream\r\n\r\n")

            async def emit(obj):
                event = b"data: " + json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n\n"
                # Split SSE lines and UTF-8 characters across HTTP chunks.
                frames = []
                for i in range(0, len(event), 7):
                    chunk = event[i:i + 7]
                    frames.append(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                writer.write(b"".join(frames))
                await writer.drain()

            await emit({"content": "", "stop": False})
            if case == "trickle":
                for _ in range(100):
                    await emit({"content": "字", "stop": False})
                    await asyncio.sleep(.01)
                return
            await emit({"content": "你好", "stop": False})
            if case == "badjson":
                broken = b"data: {bad}\n\n"
                writer.write(f"{len(broken):x}\r\n".encode() + broken + b"\r\n")
            elif case == "sseerror":
                await emit({"error": {"type": "server_error", "message": "preserved SSE"}})
            elif case != "partial":
                final = {"content": "", "stop": True,
                         "stop_type": "limit" if case == "length" else "eos",
                         "tokens_evaluated": 5, "tokens_cached": 222}
                if case != "missingtimings":
                    final["timings"] = {"predicted_n": 2, "prompt_n": 4, "cache_n": 1}
                await emit(final)
            writer.write(b"0\r\n\r\n")
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            writers.discard(writer)
            handlers.discard(task)

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    expected = {"normal": "completed", "length": "completed", "capacity400": "rejected",
                "ordinary400": "failed", "http429": "rejected", "http503": "rejected",
                "partial": "failed", "badjson": "failed", "sseerror": "failed",
                "missingtimings": "completed", "timeout": "timeout", "cancel": "cancelled",
                "predeadline": "timeout", "slow": "unfinished", "notreleased": "unfinished"}
    rows = [row(name) for name in expected]
    for r in rows:
        if r["req_id"] == "timeout":
            r.update(prompt="trickle", timeout_seconds=.065)
        if r["req_id"] == "cancel":
            r["cancel_after_ms"] = 30
        if r["req_id"] == "predeadline":
            r.update(release_offset_ms=150, timeout_seconds=.055)
        if r["req_id"] == "notreleased":
            r["release_offset_ms"] = 1000
    try:
        started = time.monotonic()
        terminal_summary = await client.replay(rows, out / "terminal-cases", url, .05, .15, request_timeout=5)
        terminal = records(out / "terminal-cases", len(rows))
        for name, state in expected.items():
            assert terminal[name]["terminal"] == state, terminal[name]
        assert sum(terminal_summary["terminal_counts"].values()) == len(rows)
        assert time.monotonic() - started < 2
        for name in ("predeadline", "notreleased"):
            assert terminal[name]["actual_send_offset_ms"] is None and name not in received
        assert terminal["predeadline"]["timeout_boundary"] == "planned_system"
        assert 40 < terminal["predeadline"]["end_offset_ms"] < 100
        for name in ("partial", "timeout", "badjson", "sseerror"):
            rec = terminal[name]
            assert rec["first_content_offset_ms"] is not None and rec["content"]
            assert rec["output_tokens"] is None and rec["output_tokens_known"] is False
        assert terminal["missingtimings"]["terminal"] == "completed"
        assert terminal["missingtimings"]["output_tokens"] is None
        assert terminal_summary["classes"]["interactive"]["output_tokens"] is None
        assert terminal_summary["output_tps_whole_run_including_drain"] is None
        rec = terminal["normal"]
        assert rec["content"] == "你好" and len(rec["chunk_series"]) == 1
        assert rec["output_tokens"] == 2 and rec["output_tokens_known"] is True
        assert rec["tokens_evaluated"] == rec["new_prompt_tokens"] + rec["cached_prompt_tokens"] == 5
        assert rec["cached_prompt_tokens"] != rec["native_final"]["tokens_cached"]
        assert terminal["length"]["finish_reason"] == "length"
        assert terminal["sseerror"]["raw_error"]["json"]["error"]["message"] == "preserved SSE"
        for name in ("capacity400", "ordinary400"):
            err = terminal[name]["raw_error"]
            assert base64.b64decode(err["body_base64"]).decode() == err["body_text"]
            assert json.loads(err["body_text"]) == err["json"]
        transport_summary = await client.replay([row("transport", "trickle", transport_timeout_seconds=.045)],
                                               out / "transport-timeout", url, .3, .1, request_timeout=.3)
        transport = records(out / "transport-timeout", 1)["transport"]
        assert transport_summary["terminal_counts"]["timeout"] == 1 and transport["timeout_boundary"] == "transport"
        assert transport["first_content_offset_ms"] is not None
        assert abs(transport["transport_deadline_offset_ms"] - transport["actual_send_offset_ms"] - 45) < .001
        held_summary = await client.replay([row("held", "normal", release_offset_ms=100)], out / "before-send-hold", url, .2, .2)
        held = records(out / "before-send-hold", 1)["held"]
        assert held_summary["terminal_counts"]["completed"] == 1
        assert held["total_first_ms"] - held["ttft_ms"] >= 99
        race = [row(f"race{i}", "race", cancel_after_ms=0 if i < 10 else 8 if i < 20 else 200) for i in range(30)]
        race_summary = await client.replay(race, out / "completion-cancel-race", url, .1, .4)
        records(out / "completion-cancel-race", 30)
        assert race_summary["terminal_counts"]["completed"] > 0 and race_summary["terminal_counts"]["cancelled"] > 0
        assert race_summary["terminal_counts"]["completed"] + race_summary["terminal_counts"]["cancelled"] == 30
        calibration = [dict(row(f"C{i:03d}", "normal"), arrival_offset_ms=i * 50) for i in range(200)]
        calibrated = await client.replay(calibration, out / "send-calibration", url, 10, 2)
        records(out / "send-calibration", 200)
        assert calibrated["terminal_counts"]["completed"] == 200 and calibrated["late_send_count"] == 0, calibrated
        original_dump = client.dump

        def slow_dump(path, obj):
            if path.parent.name == "requests":
                time.sleep(.08)
            original_dump(path, obj)

        try:
            client.dump = slow_dump
            disk_rows = [dict(row(f"D{i:03d}", "normal"), arrival_offset_ms=i * 50) for i in range(40)]
            disk = await client.replay(disk_rows, out / "slow-disk", url, 2, 8, request_timeout=5)
            records(out / "slow-disk", 40)
            assert disk["terminal_counts"]["completed"] == 40 and disk["late_send_count"] == 0, disk
            cutoff_rows = [row(f"K{i}", "slow") for i in range(8)]
            cutoff = await client.replay(cutoff_rows, out / "slow-disk-cutoff", url, .05, .15, request_timeout=5)
            cutoff_records = records(out / "slow-disk-cutoff", 8)
            assert cutoff["terminal_counts"]["unfinished"] == 8
            assert max(r["end_offset_ms"] for r in cutoff_records.values()) < 400
            assert cutoff["persistence"]["request_tasks_finished_offset_ms"] < 400
            assert cutoff["persistence"]["recorded_requests"] == cutoff["persistence"]["written_requests"] == 8
        finally:
            client.dump = original_dump
        result = {
            "status": "passed", "client_variant": "p1_development_replay", "source_sha256": hashes,
            "terminal_cases": expected, "structured_capacity_400_is_rejected": True,
            "generic_400_is_failed": True, "raw_error_bytes_preserved": True,
            "planned_deadline_covers_before_send_hold": True, "continuous_content_does_not_reset_deadline": True,
            "first_content_retained_after_timeout": True, "unknown_output_tokens_are_null": True,
            "native_final_and_cache_semantics_preserved": True,
            "transport_timeout_from_actual_send": True,
            "race_requests": 30, "race_terminal_counts": race_summary["terminal_counts"],
            "calibration_requests": 200, "calibration_rate_rps": 20,
            "calibration_max_send_lag_ms": calibrated["send_lag_max_ms"], "send_lag_limit_ms": 50,
            "slow_disk": {"requests": 40, "write_delay_ms": 80, "max_send_lag_ms": disk["send_lag_max_ms"],
                          "late_send_count": disk["late_send_count"], "persistence_drain_seconds": disk["persistence"]["drain_seconds"],
                          "cutoff_unfinished": 8, "cutoff_last_end_ms": cutoff["last_end_seconds"] * 1000,
                          "cutoff_persistence_drain_seconds": cutoff["persistence"]["drain_seconds"]},
            "note": "Controlled native HTTP/SSE development collection gate; not engine cancellation/KV reclamation acceptance.",
        }
        client.dump(out / "summary.json", result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        server.close()
        await server.wait_closed()
        for writer in list(writers):
            writer.close()
        for task in list(handlers):
            task.cancel()
        await asyncio.gather(*list(handlers), return_exceptions=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, required=True)
    asyncio.run(verify(ap.parse_args().out_dir))
