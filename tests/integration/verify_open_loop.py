#!/usr/bin/env python3
"""Exercise terminal states and cutoff against a controllable local HTTP server."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/replay"))
from open_loop import dump, replay


async def verify(out):
    out.mkdir(parents=True, exist_ok=False)
    handlers, writers = set(), set()

    async def serve(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        writers.add(writer)
        try:
            await reader.readline()
            headers = {}
            while (line := await reader.readline()) not in (b"\r\n", b"\n", b""):
                k, v = line.decode().split(":", 1)
                headers[k.lower()] = v.strip()
            body = json.loads(await reader.readexactly(int(headers["content-length"])))
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
        calibration = [{"req_id": f"C{i:03d}", "category": "interactive", "arrival_offset_ms": i * 50,
                        "prompt": "normal", "max_tokens": 10} for i in range(200)]
        calibrated = await replay(calibration, out / "send-calibration", url, 10, 2)
        assert calibrated["terminal_counts"]["completed"] == 200
        assert calibrated["late_send_count"] == 0, calibrated
        closed_port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()
        refused = await replay([{"req_id": "refused", "category": "interactive", "arrival_offset_ms": 0,
                                 "prompt": "normal", "max_tokens": 1}], out / "connection-refused",
                               f"http://127.0.0.1:{closed_port}", .1, .1)
        assert refused["terminal_counts"]["failed"] == 1
        result = {"status": "passed", "terminal_cases": expected, "race_requests": 30,
                  "race_terminal_counts": race_summary["terminal_counts"], "total_timeout_with_continuous_content": True,
                  "calibration_requests": 200, "calibration_rate_rps": 20, "send_lag_limit_ms": 50,
                  "calibration_max_send_lag_ms": calibrated["send_lag_max_ms"],
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
