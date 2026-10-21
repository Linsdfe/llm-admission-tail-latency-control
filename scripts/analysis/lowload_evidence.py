"""Shared read-only audit and prespecified diagnostics for low-load prestudy."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/replay"))
from open_loop import percentile, STATES


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def jsonlines(path):
    return [json.loads(s) for s in Path(path).read_text(encoding="utf-8").splitlines() if s.strip()]


def active_before(rows, t):
    # Left limit: an end at exactly t has not occurred at t-minus.
    return sum(r["actual_send_offset_ms"] is not None and r["actual_send_offset_ms"] < t
               and r["end_offset_ms"] >= t for r in rows)


def diagnostics(rows, cfg):
    inter = [r for r in rows if r["category"] == "interactive"]
    backgrounds = sorted((r for r in rows if r["category"] == "background"), key=lambda r: r["arrival_offset_ms"])
    checks = [{"offset_ms": r["arrival_offset_ms"], "active_before": active_before(rows, r["arrival_offset_ms"])} for r in backgrounds[1:]]
    quarters = []
    for k in range(4):
        group = [r for r in inter if k * 25000 <= r["arrival_offset_ms"] < (k + 1) * 25000]
        values = [r["ttft_ms"] for r in group if r["terminal"] == "completed" and r["ttft_ms"] is not None]
        quarters.append({"start_ms": k * 25000, "planned": len(group), "samples": len(values),
                         "p50_ms": percentile(values, 50), "p95_ms": percentile(values, 95)})
    complete = all(r["terminal"] == "completed" for r in rows)
    increase = None if quarters[0]["p50_ms"] is None or quarters[3]["p50_ms"] is None else quarters[3]["p50_ms"] - quarters[0]["p50_ms"]
    drain = max(0, max(r["end_offset_ms"] for r in rows) / 1000 - cfg["measurement_seconds"])
    return {"background_arrival_checks": checks, "quarters": quarters, "last_minus_first_quarter_p50_ms": increase,
            "drain_seconds": drain,
            "no_obvious_persistent_backlog": bool(backgrounds) and len(checks) == 9 and all(c["active_before"] == 0 for c in checks)
               and complete and increase is not None and increase <= cfg["max_quarter_median_increase_ms"]
               and drain <= cfg["max_drain_for_no_persistent_backlog_seconds"]}


def audit_run(directory, expected, cfg):
    directory = Path(directory)
    rows = jsonlines(directory / "replay/client.jsonl")
    trace = jsonlines(directory / "replay/trace.jsonl")
    summary = read(directory / "replay/summary.json")
    assert trace == expected, "trace differs from frozen source"
    assert len(rows) == len(trace) == summary["planned"]
    assert len({r["req_id"] for r in rows}) == len(trace)
    assert {r["req_id"] for r in rows} == {r["req_id"] for r in trace}
    assert len(list((directory / "replay/requests").glob("*.json"))) == len(trace)
    assert sum(summary["terminal_counts"].values()) == len(trace)
    source = {r["req_id"]: r for r in trace}
    flags = []
    for r in rows:
        assert read(directory / "replay/requests" / (r["req_id"] + ".json")) == r
        assert r["terminal"] in STATES
        assert all(r[k] == v for k, v in source[r["req_id"]].items())
        assert r["end_offset_ms"] >= 0
        if r["actual_send_offset_ms"] is not None:
            assert r["actual_send_offset_ms"] >= r["arrival_offset_ms"]
            assert r["end_offset_ms"] >= r["actual_send_offset_ms"]
            assert abs(r["send_lag_ms"] - r["actual_send_offset_ms"] + r["arrival_offset_ms"]) < .001
        if r["first_content_offset_ms"] is not None:
            assert r["actual_send_offset_ms"] <= r["first_content_offset_ms"] <= r["end_offset_ms"]
            assert abs(r["ttft_ms"] - r["first_content_offset_ms"] + r["actual_send_offset_ms"]) < .001
        if r["terminal"] == "completed":
            assert r["http_status"] == 200 and r["stream_done"] and r["finish_reason"] in ("stop", "length")
            assert r["error"] is None and r["first_content_offset_ms"] is not None
            assert sum(c["chars"] for c in r["chunk_series"]) == len(r["content"])
            assert r["chunk_series"][0]["offset_ms"] == r["first_content_offset_ms"]
            assert [c["offset_ms"] for c in r["chunk_series"]] == sorted(c["offset_ms"] for c in r["chunk_series"])
            timing = r.get("timings") or {}
            if not all(timing.get(k) is not None for k in ("prompt_n", "cache_n", "predicted_n")):
                flags.append(f"{r['req_id']}: missing length accounting")
            else:
                assert 0 <= timing["predicted_n"] <= r["max_tokens"]
                assert timing["prompt_n"] + timing["cache_n"] + r["max_tokens"] <= 2048
    for state in STATES:
        assert sum(r["terminal"] == state for r in rows) == summary["terminal_counts"][state]
    completed = [r for r in rows if r["terminal"] == "completed" and r["category"] == "interactive"]
    p95 = percentile([r["ttft_ms"] for r in completed], 95)
    assert p95 == summary["classes"]["interactive"]["ttft_p95_ms"]
    if summary["late_send_count"] or summary["sent"] != len(trace) or summary["actual_send_window_denominator"] != len(trace):
        flags.append("send timing/window mismatch")
    assert summary["late_send_count"] == sum(r["send_lag_ms"] is not None and r["send_lag_ms"] > cfg["send_lag_limit_ms"] for r in rows)
    log = (directory / "server.log").read_text(encoding="utf-8")
    if "n_slots = 2, n_ctx_slot = 2048" not in log:
        flags.append("slot capacity not confirmed")
    for error in ("Context size has been exceeded", "failed to allocate buffer", "truncated = 1"):
        if error in log:
            flags.append(error)
    return {"flags": flags, "summary": summary, "diagnostics": diagnostics(rows, cfg)}, rows
