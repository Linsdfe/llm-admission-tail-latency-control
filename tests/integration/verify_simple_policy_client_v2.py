#!/usr/bin/env python3
"""V2 client lifecycle gate, including v1/v2 slow-disk counterexample."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/replay"))
import simple_policy_replay as old_client
import simple_policy_replay_v2 as client
dump, replay = client.dump, client.replay


def verify_persistence(directory, expected_count):
    rows = [json.loads(s) for s in (directory / "client.jsonl").read_text(encoding="utf-8").splitlines() if s.strip()]
    files = list((directory / "requests").glob("*.json"))
    assert len(rows) == len(files) == expected_count
    assert len({r["req_id"] for r in rows}) == expected_count
    for row in rows:
        assert json.loads((directory / "requests" / (row["req_id"] + ".json")).read_text(encoding="utf-8")) == row
    return rows


async def verify(out):
    out.mkdir(parents=True, exist_ok=False)
    handlers, writers = set(), set()
    received = []

    async def serve(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        writers.add(writer)
        try:
            if not await reader.readline():
                return  # Cancellation can close a connected socket before HTTP is sent.
            headers = {}
            while (line := await reader.readline()) not in (b"\r\n", b"\n", b""):
                k, v = line.decode().split(":", 1)
                headers[k.lower()] = v.strip()
            if "content-length" not in headers:
                return  # A cancellation may also interrupt the header block.
            body = json.loads(await reader.readexactly(int(headers["content-length"])))
            assert body["request_class"] in ("interactive", "background")
            assert isinstance(body["client_req_id"], str) and body["client_req_id"]
            received.append({"request_class": body["request_class"], "client_req_id": body["client_req_id"]})
            case = body["messages"][0]["content"]
            if case in ("slow", "timeout", "cancel"):
                await asyncio.sleep(2)
            if case == "race":
                await asyncio.sleep(.01)
            if case.startswith("http"):
                code = int(case[4:])
                writer.write(f"HTTP/1.1 {code} Test\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
                await writer.drain()
                return
            writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Type: text/event-stream\r\n\r\n")
            if case == "trickle":
                # Continuous progress must not defeat the total request deadline.
                for _ in range(100):
                    piece = b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
                    writer.write(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
                    await writer.drain()
                    await asyncio.sleep(.01)
                return
            empty = {"choices": [{"delta": {"content": ""}}]}
            content = {"choices": [{"delta": {"content": "你好"}}]}
            finish = {"choices": [{"delta": {}, "finish_reason": "length" if case == "length" else "stop"}],
                      "timings": {"predicted_n": 2, "prompt_n": 4, "cache_n": 1}}
            data = b": heartbeat\n\ndata: " + json.dumps(empty).encode() + b"\n\n"
            data += b"data: " + json.dumps(content, ensure_ascii=False).encode("utf-8") + b"\n\n"
            if case == "badjson":
                data += b"data: {bad}\n\n"
            elif case != "partial":
                data += b"data: " + json.dumps(finish).encode() + b"\n\n"
            if case not in ("badjson", "partial", "nodone"):
                data += b"data: [DONE]\n\n"
            # Deliberately split SSE lines and UTF-8 sequences across HTTP chunks.
            framed = b""
            for i in range(0, len(data), 7):
                chunk = data[i:i + 7]
                framed += f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n"
            writer.write(framed + b"0\r\n\r\n")
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
    expected = {"normal": "completed", "length": "completed", "http429": "rejected", "http503": "rejected",
                "http400": "failed", "partial": "failed", "nodone": "failed", "badjson": "failed",
                "timeout": "timeout", "cancel": "cancelled", "slow": "unfinished", "notreleased": "unfinished"}
    rows = [{"req_id": name, "category": "interactive", "arrival_offset_ms": 0,
             "prompt": name, "max_tokens": 10} for name in expected]
    for row in rows:
        if row["req_id"] == "timeout":
            row["timeout_seconds"] = .06
            row["prompt"] = "trickle"
        if row["req_id"] == "cancel": row["cancel_after_ms"] = 30
        if row["req_id"] == "notreleased": row["release_offset_ms"] = 1000
    started = time.monotonic()
    try:
        summary = await replay(rows, out / "terminal-cases", url, .05, .15, request_timeout=5)
        records = [json.loads(s) for s in (out / "terminal-cases/client.jsonl").read_text(encoding="utf-8").splitlines()]
        for rec in records:
            assert rec["terminal"] == expected[rec["req_id"]], rec
            if rec["req_id"] == "notreleased": assert rec["actual_send_offset_ms"] is None
            if rec["req_id"] == "timeout": assert rec["chunk_series"], "Timeout test must include ongoing content"
            if rec["terminal"] == "completed":
                assert rec["content"] == "你好" and len(rec["chunk_series"]) == 1
                assert abs(rec["first_content_offset_ms"] - rec["actual_send_offset_ms"] - rec["ttft_ms"]) < .001
                assert abs(rec["first_content_offset_ms"] - rec["arrival_offset_ms"] - rec["total_first_ms"]) < .001
        assert sum(summary["terminal_counts"].values()) == len(expected)
        assert time.monotonic() - started < 2, "Deadline failed to stop local execution"
        race = [{"req_id": f"race{i}", "category": "interactive", "arrival_offset_ms": 0,
                 "prompt": "race", "max_tokens": 10,
                 "cancel_after_ms": 0 if i < 10 else 15 if i < 20 else 200} for i in range(30)]
        race_summary = await replay(race, out / "completion-cancel-race", url, .1, .4)
        assert race_summary["terminal_counts"]["completed"] > 0
        assert race_summary["terminal_counts"]["cancelled"] > 0
        assert race_summary["terminal_counts"]["completed"] + race_summary["terminal_counts"]["cancelled"] == 30
        assert len(list((out / "completion-cancel-race/requests").glob("*.json"))) == 30
        held = [{"req_id": "held-background", "category": "background", "arrival_offset_ms": 0,
                 "release_offset_ms": 100, "prompt": "normal", "max_tokens": 10}]
        held_summary = await replay(held, out / "total-wait-boundary", url, .2, .2)
        held_record = json.loads((out / "total-wait-boundary/requests/held-background.json").read_text(encoding="utf-8"))
        assert held_summary["terminal_counts"]["completed"] == 1
        assert held_record["total_first_ms"] - held_record["ttft_ms"] >= 99
        assert {"request_class": "background", "client_req_id": "held-background"} in received
        calibration = [{"req_id": f"C{i:03d}", "category": "interactive", "arrival_offset_ms": i * 50,
                        "prompt": "normal", "max_tokens": 10} for i in range(200)]
        calibrated = await replay(calibration, out / "send-calibration", url, 10, 2)
        assert calibrated["terminal_counts"]["completed"] == 200
        assert calibrated["late_send_count"] == 0, calibrated
        disk_rows = [{"req_id": f"D{i:03d}", "category": "interactive", "arrival_offset_ms": i * 50,
                      "prompt": "normal", "max_tokens": 10} for i in range(40)]
        original_v1_dump, original_v2_dump = old_client.dump, client.dump
        def delayed_dump(original):
            def write(path, value):
                if path.parent.name == "requests":
                    time.sleep(.08)
                original(path, value)
            return write
        try:
            old_client.dump = delayed_dump(original_v1_dump)
            disk_v1 = await old_client.replay(disk_rows, out / "slow-disk-v1", url, 2, 8, request_timeout=5)
            client.dump = delayed_dump(original_v2_dump)
            disk_v2 = await replay(disk_rows, out / "slow-disk-v2", url, 2, 8, request_timeout=5)
            assert disk_v1["send_lag_max_ms"] > 50 and disk_v1["late_send_count"] > 0, disk_v1
            assert disk_v2["send_lag_max_ms"] <= 50 and disk_v2["late_send_count"] == 0, disk_v2
            assert disk_v1["terminal_counts"]["completed"] == disk_v2["terminal_counts"]["completed"] == 40
            verify_persistence(out / "slow-disk-v1", 40)
            verify_persistence(out / "slow-disk-v2", 40)
            cutoff_rows = [{"req_id": f"K{i}", "category": "background", "arrival_offset_ms": 0,
                            "prompt": "slow", "max_tokens": 10} for i in range(8)]
            cutoff = await replay(cutoff_rows, out / "slow-disk-cutoff-v2", url, .05, .15, request_timeout=5)
            cutoff_records = verify_persistence(out / "slow-disk-cutoff-v2", 8)
            assert cutoff["terminal_counts"]["unfinished"] == 8
            assert max(r["end_offset_ms"] for r in cutoff_records) < 400
            assert cutoff["persistence"]["request_tasks_finished_offset_ms"] < 400
            assert cutoff["persistence"]["recorded_requests"] == cutoff["persistence"]["written_requests"] == 8
            slow_disk_test = {"status": "passed", "delay_ms": 80, "requests": 40, "rate_rps": 20,
                              "v1": {"send_lag_max_ms": disk_v1["send_lag_max_ms"], "late_send_count": disk_v1["late_send_count"], "completed": 40},
                              "v2": {"send_lag_max_ms": disk_v2["send_lag_max_ms"], "late_send_count": disk_v2["late_send_count"], "completed": 40,
                                     "persistence_drain_seconds": disk_v2["persistence"]["drain_seconds"]},
                              "cutoff": {"planned": 8, "unfinished": 8, "last_end_offset_ms": max(r["end_offset_ms"] for r in cutoff_records),
                                         "request_tasks_finished_offset_ms": cutoff["persistence"]["request_tasks_finished_offset_ms"],
                                         "persistence_drain_seconds": cutoff["persistence"]["drain_seconds"]},
                              "scope": "Controlled sync persistence counterexample; does not explain actual model policy performance by itself"}
        finally:
            old_client.dump, client.dump = original_v1_dump, original_v2_dump
        closed_port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()
        refused = await replay([{"req_id": "refused", "category": "interactive", "arrival_offset_ms": 0,
                                 "prompt": "normal", "max_tokens": 1}], out / "connection-refused",
                               f"http://127.0.0.1:{closed_port}", .1, 4, request_timeout=5)
        assert refused["terminal_counts"]["failed"] == 1
        result = {"status": "passed", "terminal_cases": expected, "race_requests": 30,
                  "race_terminal_counts": race_summary["terminal_counts"], "total_timeout_with_continuous_content": True,
                  "calibration_requests": 200, "calibration_rate_rps": 20, "send_lag_limit_ms": 50,
                  "calibration_max_send_lag_ms": calibrated["send_lag_max_ms"],
                  "metadata_fields_verified": ["request_class", "client_req_id"],
                  "total_first_includes_before_send_hold": True,
                  "client_variant": "simple_policy_replay_v2", "slow_disk_test": slow_disk_test,
                  "note": "Controlled HTTP/SSE client lifecycle verification, not engine KV reclamation acceptance"}
        dump(out / "summary.json", result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        server.close()
        await server.wait_closed()
        for writer in list(writers): writer.close()
        for task in list(handlers): task.cancel()
        await asyncio.gather(*list(handlers), return_exceptions=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, required=True)
    asyncio.run(verify(ap.parse_args().out_dir))
