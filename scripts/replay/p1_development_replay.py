#!/usr/bin/env python3
"""P1 development replay, using the verified v2 serial persistence pattern.

Only HTTP loopback endpoints are supported. Deadlines stop local execution and
close sockets; engine-side cancellation/reclamation must be verified separately.
System timeouts include time held before send; transport timeouts are separate.
Unknown output-token counts remain null, never character/SSE-chunk estimates.
"""
import asyncio
import base64
from collections import Counter
import json
import math
from pathlib import Path
import time
from urllib.parse import urlsplit

STATES = ("completed", "rejected", "timeout", "cancelled", "failed", "unfinished")


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * p / 100
    lo = int(k)
    return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (k - lo)


def validate(rows):
    ids = [r["req_id"] for r in rows]
    if not rows or len(ids) != len(set(ids)):
        raise ValueError("Nonempty trace and unique IDs required")
    for r in rows:
        if not isinstance(r["req_id"], str) or not r["req_id"].isascii() or not r["req_id"].replace("-", "").replace("_", "").isalnum():
            raise ValueError("Unsafe request ID")
        if r["category"] not in ("interactive", "background"):
            raise ValueError("Unknown class")
        for key in ("arrival_offset_ms", "release_offset_ms"):
            value = r.get(key, r["arrival_offset_ms"])
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("Invalid time offset")
        if r.get("release_offset_ms", r["arrival_offset_ms"]) < r["arrival_offset_ms"]:
            raise ValueError("Release before planned arrival")
        if not isinstance(r["prompt"], str) or not r["prompt"] or not isinstance(r["max_tokens"], int) or r["max_tokens"] <= 0:
            raise ValueError("Invalid request")
        for key in ("timeout_seconds", "transport_timeout_seconds"):
            if key in r and (not isinstance(r[key], (int, float)) or not math.isfinite(r[key]) or r[key] <= 0):
                raise ValueError("Invalid request deadline")
        if "cancel_after_ms" in r and (not isinstance(r["cancel_after_ms"], (int, float)) or not math.isfinite(r["cancel_after_ms"]) or r["cancel_after_ms"] < 0):
            raise ValueError("Invalid cancellation offset")


async def body_lines(reader, headers):
    """Frame HTTP bodies before parsing SSE, including split chunk boundaries."""
    buffered = b""
    if "chunked" in headers.get("transfer-encoding", "").lower():
        while True:
            line = await reader.readline()
            if not line:
                raise EOFError("Missing chunk size")
            size = int(line.split(b";", 1)[0].strip(), 16)
            if size == 0:
                while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                    pass
                break
            buffered += await reader.readexactly(size)
            if await reader.readexactly(2) != b"\r\n":
                raise ValueError("Invalid chunk ending")
            while b"\n" in buffered:
                result, buffered = buffered.split(b"\n", 1)
                yield result.rstrip(b"\r")
    else:
        remaining = int(headers["content-length"]) if "content-length" in headers else None
        while remaining is None or remaining > 0:
            chunk = await reader.read(65536 if remaining is None else min(65536, remaining))
            if not chunk:
                if remaining:
                    raise EOFError("Truncated HTTP body")
                break
            if remaining is not None:
                remaining -= len(chunk)
            buffered += chunk
            while b"\n" in buffered:
                result, buffered = buffered.split(b"\n", 1)
                yield result.rstrip(b"\r")
    if buffered:
        yield buffered.rstrip(b"\r")


async def replay(rows, out, base_url, measurement_seconds, drain_seconds,
                 request_timeout=180.0, send_lag_limit_ms=50.0,
                 transport_timeout=360.0):
    validate(rows)
    if (not all(isinstance(v, (int, float)) and math.isfinite(v)
                for v in (measurement_seconds, drain_seconds, request_timeout, transport_timeout, send_lag_limit_ms))
            or min(measurement_seconds, request_timeout, transport_timeout) <= 0
            or min(drain_seconds, send_lag_limit_ms) < 0):
        raise ValueError("Invalid deadline")
    endpoint = urlsplit(base_url)
    if endpoint.scheme != "http" or endpoint.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Only local HTTP endpoints are supported")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "requests").mkdir()
    with (out / "trace.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    origin = time.monotonic() + .25
    deadline = origin + measurement_seconds + drain_seconds
    records = {}
    handles = []
    background = []
    jsonl = (out / "client.jsonl").open("x", encoding="utf-8")
    persistence_queue = asyncio.Queue()
    written_ids = []
    queue_high_water = 0

    def persist_record(rec):
        # This entire operation runs in a worker thread, including JSON encoding.
        dump(out / "requests" / f"{rec['req_id']}.json", rec)
        jsonl.write(json.dumps(rec, ensure_ascii=False) + "\n")
        jsonl.flush()

    async def persist_loop():
        try:
            while True:
                rec = await persistence_queue.get()
                if rec is None:
                    break
                await asyncio.to_thread(persist_record, rec)
                written_ids.append(rec["req_id"])
        finally:
            await asyncio.to_thread(jsonl.close)

    persistence_task = asyncio.create_task(persist_loop())

    async def execute(row):
        nonlocal queue_high_water
        planned = row["arrival_offset_ms"]
        release = row.get("release_offset_ms", planned)
        rec = dict(row, terminal=None, http_status=None, error=None,
                   actual_send_offset_ms=None, first_content_offset_ms=None,
                   end_offset_ms=None, send_lag_ms=None, actual_send_lag_from_planned_ms=None, ttft_ms=None,
                   finish_reason=None, stream_done=False, server_final_received=False, timings=None,
                   native_final=None, tokens_evaluated=None,
                   usage=None, content="", chunk_series=[], headers_ms=None,
                   total_first_ms=None, completion_from_planned_ms=None,
                   raw_error=None, error_type=None, timeout_boundary=None,
                   system_deadline_offset_ms=planned + row.get("timeout_seconds", request_timeout) * 1000,
                   transport_deadline_offset_ms=None, cache_prompt=row.get("cache_prompt", True),
                   output_tokens=None, output_tokens_known=False, output_tokens_source=None,
                   new_prompt_tokens=None, cached_prompt_tokens=None,
                   output_limit_exceeded=None,
                   request_class=row["category"], client_req_id=row["req_id"],
                   clock="client time.monotonic", time_unit="ms")
        writer = None

        async def stream():
            nonlocal writer
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(endpoint.hostname, endpoint.port or 80),
                    timeout=row.get("transport_timeout_seconds", transport_timeout))
            except asyncio.TimeoutError:
                rec["timeout_boundary"] = "transport_connect"
                raise
            body = json.dumps({"prompt": row["prompt"],
                               "n_predict": row["max_tokens"], "temperature": row.get("temperature", 0.0),
                               "stream": True,
                               "ignore_eos": False,
                               "cache_prompt": row.get("cache_prompt", True),
                               "request_class": row["category"],
                               "client_req_id": row["req_id"]}, ensure_ascii=False).encode("utf-8")
            path = endpoint.path.rstrip("/") + "/completion"
            payload = (f"POST {path} HTTP/1.1\r\nHost: {endpoint.hostname}\r\n"
                       f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body
            sent = time.monotonic()
            rec["actual_send_offset_ms"] = (sent - origin) * 1000
            rec["send_lag_ms"] = rec["actual_send_offset_ms"] - release
            rec["actual_send_lag_from_planned_ms"] = rec["actual_send_offset_ms"] - planned
            rec["transport_deadline_offset_ms"] = rec["actual_send_offset_ms"] + row.get("transport_timeout_seconds", transport_timeout) * 1000
            writer.write(payload)
            async def receive():
                await writer.drain()
                status = await reader.readline()
                rec["http_status"] = int(status.split()[1])
                headers = {}
                while True:
                    line = await reader.readline()
                    if line in (b"\r\n", b"\n"):
                        break
                    if not line:
                        raise EOFError("Incomplete headers")
                    key, value = line.decode("latin-1").split(":", 1)
                    headers[key.lower()] = value.strip()
                rec["headers_ms"] = (time.monotonic() - sent) * 1000
                if rec["http_status"] != 200:
                    raw_body = await read_error_body(reader, headers)
                    rec["raw_error"] = {
                        "http_status": rec["http_status"],
                        "body_text": raw_body.decode("utf-8", errors="replace"),
                        "body_base64": base64.b64encode(raw_body).decode("ascii"),
                        "json": None,
                    }
                    try:
                        rec["raw_error"]["json"] = json.loads(raw_body)
                    except (ValueError, UnicodeDecodeError):
                        pass
                    error_obj = rec["raw_error"]["json"]
                    if isinstance(error_obj, dict):
                        nested = error_obj.get("error", error_obj)
                        if isinstance(nested, dict):
                            rec["error_type"] = nested.get("type")
                    capacity = rec["http_status"] == 400 and rec["error_type"] == "exceed_context_size_error"
                    rec["terminal"] = "rejected" if rec["http_status"] in (429, 503) or capacity else "failed"
                    rec["error"] = f"HTTP {rec['http_status']}"
                    return
                async for raw in body_lines(reader, headers):
                    if not raw.startswith(b"data:"):
                        continue
                    value = raw[5:].strip()
                    if value == b"[DONE]":
                        rec["stream_done"] = True
                        break
                    if not value:
                        continue
                    obj = json.loads(value.decode("utf-8"))
                    if obj.get("error"):
                        rec["raw_error"] = {"sse_text": value.decode("utf-8"), "json": obj}
                        raise ValueError(f"SSE error: {obj['error']}")
                    piece = obj.get("content")
                    now = (time.monotonic() - origin) * 1000
                    if piece:
                        if rec["first_content_offset_ms"] is None:
                            rec["first_content_offset_ms"] = now
                            rec["ttft_ms"] = now - rec["actual_send_offset_ms"]
                            rec["total_first_ms"] = now - planned
                        rec["content"] += piece
                        rec["chunk_series"].append({"offset_ms": now, "chars": len(piece)})
                    if obj.get("stop") is True:
                        rec["server_final_received"] = True
                        rec["native_final"] = obj
                        rec["tokens_evaluated"] = obj.get("tokens_evaluated")
                        rec["finish_reason"] = "length" if obj.get("stopped_limit") or obj.get("stop_type") == "limit" else "stop"
                        rec["stream_done"] = True
                    if obj.get("timings"):
                        rec["timings"] = obj["timings"]
                    if obj.get("usage"):
                        rec["usage"] = obj["usage"]
                    if obj.get("stop") is True:
                        break
                if rec["stream_done"] and rec["finish_reason"] in ("stop", "length"):
                    rec["terminal"] = "completed"
                else:
                    raise EOFError("Stream ended without a native stop=true final event")
            try:
                await asyncio.wait_for(receive(), timeout=max(
                    0, origin + rec["transport_deadline_offset_ms"] / 1000 - time.monotonic()))
            except asyncio.TimeoutError:
                rec["timeout_boundary"] = "transport"
                raise

        async def lifecycle():
            await asyncio.sleep(max(0, origin + release / 1000 - time.monotonic()))
            # The outer planned deadline includes pre-send hold and all I/O.
            await stream()

        try:
            await asyncio.wait_for(lifecycle(), timeout=max(0, origin + rec["system_deadline_offset_ms"] / 1000 - time.monotonic()))
        except asyncio.TimeoutError:
            rec["timeout_boundary"] = rec["timeout_boundary"] or "planned_system"
            rec["terminal"], rec["error"] = "timeout", rec["timeout_boundary"] + " total deadline"
        except asyncio.CancelledError as exc:
            rec["terminal"] = "unfinished" if exc.args and exc.args[0] == "drain_deadline" else "cancelled"
            rec["error"] = str(exc) or "Explicit cancellation"
        except Exception as exc:
            rec["terminal"], rec["error"] = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            rec["end_offset_ms"] = (time.monotonic() - origin) * 1000
            rec["completion_from_planned_ms"] = rec["end_offset_ms"] - planned
            timings = rec["timings"] or {}
            output = timings.get("predicted_n") if rec["server_final_received"] else None
            if isinstance(output, int) and not isinstance(output, bool) and output >= 0:
                rec["output_tokens"] = output
                rec["output_tokens_known"] = True
                rec["output_tokens_source"] = "server.timings.predicted_n"
                rec["output_limit_exceeded"] = output > row["max_tokens"]
            for source, target in (("prompt_n", "new_prompt_tokens"), ("cache_n", "cached_prompt_tokens")):
                value = timings.get(source)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    rec[target] = value
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout=1)
                except (Exception, asyncio.CancelledError):
                    pass
            assert rec["terminal"] in STATES
            assert row["req_id"] not in records
            records[row["req_id"]] = rec
            persistence_queue.put_nowait(rec)
            queue_high_water = max(queue_high_water, persistence_queue.qsize())
        return rec

    async def cancel_at(handle, offset):
        await asyncio.sleep(max(0, origin + offset / 1000 - time.monotonic()))
        if not handle.done():
            handle.cancel("explicit_cancel")

    for row in rows:
        task = asyncio.create_task(execute(row))
        handles.append(task)
        if "cancel_after_ms" in row:
            background.append(asyncio.create_task(cancel_at(task, row["arrival_offset_ms"] + row["cancel_after_ms"])))
    try:
        _, pending = await asyncio.wait(handles, timeout=max(0, deadline - time.monotonic()))
        for task in pending:
            task.cancel("drain_deadline")
        await asyncio.gather(*handles)
    except BaseException:
        for task in handles:
            if not task.done():
                task.cancel("replay_aborted")
        await asyncio.gather(*handles, return_exceptions=True)
        raise
    finally:
        request_tasks_finished_offset_ms = (time.monotonic() - origin) * 1000
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        # Flush after lifecycle processing: deadlines and sent/end timestamps above
        # never wait for this queue to drain.
        persistence_started = time.monotonic()
        persistence_queue.put_nowait(None)
        await persistence_task
        persistence_drain_seconds = time.monotonic() - persistence_started
    assert len(records) == len(rows)
    assert len(written_ids) == len(rows) and len(set(written_ids)) == len(rows)
    assert set(written_ids) == set(records)
    values = list(records.values())
    counts = {state: sum(r["terminal"] == state for r in values) for state in STATES}
    assert sum(counts.values()) == len(rows)
    sent = [r for r in values if r["actual_send_offset_ms"] is not None]
    window_sent = [r for r in sent if r["actual_send_offset_ms"] < measurement_seconds * 1000]
    summary = {"schema_version": 5, "client_variant": "p1_development_replay", "planned": len(rows), "sent": len(sent), "terminal_counts": counts,
               "persistence": {"mode": "one async queue / serial worker-thread writes", "recorded_requests": len(records),
                               "written_requests": len(written_ids), "queue_high_water": queue_high_water,
                               "request_tasks_finished_offset_ms": request_tasks_finished_offset_ms,
                               "drain_seconds": persistence_drain_seconds,
                               "timing": "end timestamps precede persistence; persistence drain excluded from latency/throughput denominator"},
               "actual_send_window_denominator": len(window_sent),
               "window_terminal_counts": {s: sum(r["terminal"] == s for r in window_sent) for s in STATES},
               "measurement_seconds": measurement_seconds, "drain_seconds": drain_seconds,
               "deadlines": {"system_seconds_from_planned": request_timeout,
                             "transport_seconds": transport_timeout,
                             "transport_boundary": "connection establishment and subsequent stream bounded separately; stream deadline from send",
                             "global_cutoff_seconds_from_origin": measurement_seconds + drain_seconds,
                             "note": "All deadlines are total, never reset by incoming chunks; local close is not server reclamation."},
               "last_end_seconds": max(r["end_offset_ms"] for r in values) / 1000,
               "send_lag_limit_ms": send_lag_limit_ms,
               "send_lag_max_ms": max((r["send_lag_ms"] for r in sent), default=None),
               "send_lag_p95_ms": percentile([r["send_lag_ms"] for r in sent], 95),
               "late_send_count": sum(r["send_lag_ms"] > send_lag_limit_ms for r in sent),
               "classes": {}}
    for category in ("interactive", "background"):
        subset = [r for r in values if r["category"] == category]
        complete = [r for r in subset if r["terminal"] == "completed"]
        ttfts = [r["ttft_ms"] for r in complete if r["ttft_ms"] is not None]
        totals = [r["total_first_ms"] for r in complete if r["total_first_ms"] is not None]
        observed_ttfts = [r["ttft_ms"] for r in subset if r["ttft_ms"] is not None]
        observed_totals = [r["total_first_ms"] for r in subset if r["total_first_ms"] is not None]
        lengths = [r["output_tokens"] for r in complete]
        inputs = [(r["timings"] or {}).get("prompt_n") for r in complete]
        cached = [(r["timings"] or {}).get("cache_n") for r in complete]
        ends = [r["end_offset_ms"] - r["arrival_offset_ms"] for r in complete]
        summary["classes"][category] = {
            "planned": len(subset), "completed": len(complete), "ttft_samples": len(ttfts),
            "terminal_counts": dict(Counter(r["terminal"] for r in subset)),
            "ttft_p50_ms": percentile(ttfts, 50), "ttft_p95_ms": percentile(ttfts, 95),
            "total_first_samples": len(totals),
            "total_first_p50_ms": percentile(totals, 50), "total_first_p95_ms": percentile(totals, 95),
            "observed_first_content_samples": len(observed_ttfts),
            "observed_ttft_p50_ms": percentile(observed_ttfts, 50), "observed_ttft_p95_ms": percentile(observed_ttfts, 95),
            "observed_total_first_p50_ms": percentile(observed_totals, 50), "observed_total_first_p95_ms": percentile(observed_totals, 95),
            "completion_from_planned_p50_ms": percentile(ends, 50),
            "completion_from_planned_p95_ms": percentile(ends, 95),
            "completion_from_planned_max_ms": max(ends, default=None),
            "output_tokens": sum(lengths) if all(n is not None for n in lengths) else None,
            "known_completed_output_tokens": sum(n for n in lengths if n is not None),
            "output_tokens_known": all(n is not None for n in lengths),
            "output_length_known": sum(n is not None for n in lengths),
            "output_length_unknown": sum(n is None for n in lengths),
            "sent_noncompleted_output_length_unknown": sum(r["actual_send_offset_ms"] is not None and r["terminal"] != "completed" and not r["output_tokens_known"] for r in subset),
            "output_limit_exceeded_count": sum(r["output_limit_exceeded"] is True for r in subset),
            "new_prompt_tokens": sum(n for n in inputs if n is not None),
            "cached_prompt_tokens": sum(n for n in cached if n is not None)}
    ended_in_window = [r for r in values if r["terminal"] == "completed" and r["end_offset_ms"] < measurement_seconds * 1000]
    summary["window_completion_throughput_rps"] = len(ended_in_window) / measurement_seconds
    summary["drain_elapsed_seconds"] = max(0, summary["last_end_seconds"] - measurement_seconds)
    known_sum = sum(c["known_completed_output_tokens"] for c in summary["classes"].values())
    summary["known_completed_output_tokens"] = known_sum
    summary["known_completed_output_tps_including_drain"] = known_sum / summary["last_end_seconds"]
    output_complete = all(r["output_tokens_known"] for r in values if r["terminal"] == "completed")
    no_missing_partial = all(r["terminal"] == "completed" for r in sent)
    summary["output_tps_whole_run_including_drain"] = known_sum / summary["last_end_seconds"] if output_complete and no_missing_partial else None
    summary["output_tps_whole_run_known"] = output_complete and no_missing_partial
    summary["throughput_denominators"] = {
        "window_completion_throughput_rps": "completed before fixed measurement cutoff / measurement_seconds",
        "output_tps_whole_run_including_drain": "completed output / last_end_seconds only when all sent requests complete and final output counts are known; otherwise null",
        "known_completed_output_tps_including_drain": "known output of completed requests / last_end_seconds; explicitly incomplete accounting, never a whole-system throughput claim",
        "window_token_throughput": "not estimated from character chunks"}
    await asyncio.to_thread(dump, out / "summary.json", summary)
    return summary


async def read_error_body(reader, headers):
    """Preserve exact error-body bytes, with normal HTTP chunk framing removed."""
    if "chunked" in headers.get("transfer-encoding", "").lower():
        chunks = []
        while True:
            size_line = await reader.readline()
            if not size_line:
                raise EOFError("Missing error-body chunk size")
            size = int(size_line.split(b";", 1)[0].strip(), 16)
            if size == 0:
                while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                    pass
                return b"".join(chunks)
            chunks.append(await reader.readexactly(size))
            if await reader.readexactly(2) != b"\r\n":
                raise ValueError("Invalid error-body chunk ending")
    if "content-length" in headers:
        return await reader.readexactly(int(headers["content-length"]))
    return await reader.read()


run_trial = replay
