#!/usr/bin/env python3
"""Read-only audit and append-only report for the twelve-run policy pilot.

This analyzer uses only Python's standard library, recomputes client statistics,
and treats repeated queue SELECT events separately from unique slot LAUNCHes.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path
import re

STATES = ("completed", "rejected", "timeout", "cancelled", "failed", "unfinished")
CLASSES = ("interactive", "background")
ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def lines(path):
    return [json.loads(s) for s in Path(path).read_text(encoding="utf-8").splitlines() if s.strip()]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * p / 100
    low = int(k)
    return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (k - low)


def close(a, b, tolerance=1e-6):
    assert (a is None) == (b is None), (a, b)
    if a is not None:
        assert math.isfinite(a) and math.isfinite(b) and abs(a - b) <= tolerance * max(1, abs(a), abs(b)), (a, b)


def stats(values, prefix):
    return {prefix + "_samples": len(values), prefix + "_p50_ms": percentile(values, 50),
            prefix + "_p95_ms": percentile(values, 95), prefix + "_max_ms": max(values, default=None)}


def reconstruct_traces(batch, cfg):
    inter = lines(batch / "input-source/r1-E0/replay/trace.jsonl")
    mixed = lines(batch / "input-source/r1-E1/replay/trace.jsonl")
    assert len(inter) == 200 and [r for r in mixed if r["category"] == "interactive"] == inter
    backgrounds = sorted((r for r in mixed if r["category"] == "background"), key=lambda r: r["arrival_offset_ms"])
    assert len(backgrounds) == 100
    indices = [math.floor(k * 100 / 30) for k in range(30)]
    assert cfg["selected_background_indices"] == indices == read(batch / "selected-background-indices.json")
    traces = {}
    for scenario in ("steady", "burst"):
        chosen = []
        for k, index in enumerate(indices):
            row = dict(backgrounds[index])
            row["arrival_offset_ms"] = 250 + k * (100000 / 30) if scenario == "steady" else (k // 3) * 10000 + 250 + (k % 3) * 50
            chosen.append(row)
        traces[scenario] = sorted(inter + chosen, key=lambda r: (r["arrival_offset_ms"], r["req_id"]))
        assert traces[scenario] == lines(batch / f"trace-{scenario}.jsonl")
    return traces


def client_summary(rows, cfg):
    window_ms = cfg["measurement_seconds"] * 1000
    sent = [r for r in rows if r["actual_send_offset_ms"] is not None]
    window_sent = [r for r in sent if r["actual_send_offset_ms"] < window_ms]
    counts = {state: sum(r["terminal"] == state for r in rows) for state in STATES}
    last_end = max(r["end_offset_ms"] for r in rows) / 1000
    result = {"planned": len(rows), "sent": len(sent), "terminal_counts": counts,
              "noncompleted": len(rows) - counts["completed"],
              "actual_send_window_denominator": len(window_sent),
              "window_terminal_counts": {s: sum(r["terminal"] == s for r in window_sent) for s in STATES},
              "last_end_seconds": last_end,
              "send_lag_max_ms": max((r["send_lag_ms"] for r in sent), default=None),
              "send_lag_p95_ms": percentile([r["send_lag_ms"] for r in sent], 95),
              "late_send_count": sum(r["send_lag_ms"] > cfg["send_lag_limit_ms"] for r in sent), "classes": {}}
    for category in CLASSES:
        subset = [r for r in rows if r["category"] == category]
        complete = [r for r in subset if r["terminal"] == "completed"]
        first = [r for r in subset if r["first_content_offset_ms"] is not None]
        values = {"planned": len(subset), "completed": len(complete),
                  "noncompleted": len(subset) - len(complete),
                  "terminal_counts": {s: sum(r["terminal"] == s for r in subset) for s in STATES},
                  "finish_reasons": dict(Counter(str(r.get("finish_reason")) for r in subset)),
                  "observed_first_content_samples": len(first),
                  "first_content_coverage_fraction": len(first) / len(subset)}
        for name, group, field in (("ttft", complete, "ttft_ms"), ("total_first", complete, "total_first_ms"),
                                   ("observed_ttft", first, "ttft_ms"), ("observed_total_first", first, "total_first_ms"),
                                   ("completion_from_planned", complete, "completion_from_planned_ms")):
            values.update(stats([r[field] for r in group if r[field] is not None], name))
        for target, source in (("output_tokens", "predicted_n"), ("new_prompt_tokens", "prompt_n"), ("cached_prompt_tokens", "cache_n")):
            known = [(r.get("timings") or {}).get(source) for r in complete]
            values[target] = sum(v for v in known if v is not None)
            values[target + "_known_samples"] = sum(v is not None for v in known)
        values["output_length_known"] = values["output_tokens_known_samples"]
        result["classes"][category] = values
    completed_in_window = sum(r["terminal"] == "completed" and r["end_offset_ms"] < window_ms for r in rows)
    result.update(window_completed_requests=completed_in_window,
                  window_completion_throughput_rps=completed_in_window / cfg["measurement_seconds"],
                  drain_elapsed_seconds=max(0, last_end - cfg["measurement_seconds"]),
                  output_tps_whole_run_including_drain=sum(v["output_tokens"] for v in result["classes"].values()) / last_end)
    return result


def parse_server_log(path, expected, strategy, cfg):
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    assert "n_slots = 2, n_ctx_slot = 2048" in text, "Slot capacity not confirmed"
    mode = 1 if strategy == "simple" else 0
    assert f"SCHED_CONFIG mode={mode} background_aging_ms={cfg['background_aging_ms']} preemptive=0" in text
    for failure in ("Context size has been exceeded", "failed to allocate buffer", "truncated = 1", "out of memory", "CUDA error"):
        assert failure.lower() not in text.lower(), failure
    by_id = {r["req_id"]: r for r in expected}
    enqueues, selections, launches, latest_select = {}, [], [], {}
    for number, line in enumerate(text.splitlines(), 1):
        marker = next((key for key in ("SCHED_ENQUEUE", "SCHED_SELECT", "SCHED_LAUNCH") if key in line), None)
        if marker is None:
            continue
        fields = dict(re.findall(r"(\w+)=([^\s]+)", line))
        req_id = fields.get("clientid")
        if req_id not in by_id:
            continue  # fixed warmups and unrelated control tasks are not measurement samples
        assert fields["class"] == by_id[req_id]["category"]
        fields["line"] = number
        for key in ("id", "wait_ms", "enqueue_ms", "launch_ms", "mode", "pending", "deferred"):
            if key in fields:
                fields[key] = int(fields[key])
        if marker == "SCHED_ENQUEUE":
            if req_id in enqueues:
                assert enqueues[req_id]["id"] == fields["id"] and enqueues[req_id]["enqueue_ms"] == fields["enqueue_ms"]
            else:
                enqueues[req_id] = fields
            assert fields["mode"] == mode
        elif marker == "SCHED_SELECT":
            assert req_id in enqueues and fields["id"] == enqueues[req_id]["id"] and fields["wait_ms"] >= 0
            selections.append(fields)
            latest_select[(req_id, fields["id"])] = fields
        else:
            assert req_id in enqueues and fields["mode"] == mode and fields["wait_ms"] >= 0
            assert req_id not in {r["clientid"] for r in launches}, "Duplicate actual LAUNCH"
            assert fields["enqueue_ms"] == enqueues[req_id]["enqueue_ms"]
            assert fields["launch_ms"] - fields["enqueue_ms"] == fields["wait_ms"]
            selected = latest_select.get((req_id, fields["id"]))
            assert selected is not None and selected["line"] < number
            launches.append({**fields, "launch_rank": len(launches), "latest_select_reason": selected["reason"],
                             "latest_select_source": selected["source"], "latest_select_wait_ms": selected["wait_ms"],
                             "selection_event_count_before_launch": sum(s["clientid"] == req_id for s in selections)})
    enqueue_order = [r["clientid"] for r in sorted(enqueues.values(), key=lambda r: r["line"])]
    launch_order = [r["clientid"] for r in launches]
    ranks = {req: rank for rank, req in enumerate(enqueue_order)}
    inversions = sum(ranks[launch_order[i]] > ranks[launch_order[j]] for i in range(len(launch_order)) for j in range(i + 1, len(launch_order)))
    aged_selected = {s["clientid"] for s in selections if s.get("reason") == "background_aged"}
    aged_launched = [r["clientid"] for r in launches if r["latest_select_reason"] == "background_aged"]
    waits = {c: [r["wait_ms"] for r in launches if r["class"] == c] for c in CLASSES}
    return {"enqueue_unique_requests": len(enqueues), "selection_events": len(selections),
            "selection_source_counts": dict(Counter(s["source"] for s in selections)),
            "selection_reason_counts": dict(Counter(s["reason"] for s in selections)),
            "selection_unique_requests": len({s["clientid"] for s in selections}),
            "selection_events_with_aging_reason": sum(s.get("reason") == "background_aged" for s in selections),
            "aging_selected_unique_request_ids": sorted(aged_selected),
            "aging_launch_unique_request_ids": aged_launched,
            "launch_unique_requests": len(launches), "launch_order": launch_order,
            "enqueue_order": enqueue_order, "launch_order_inversions_vs_enqueue": inversions,
            "launches": launches,
            "wait_by_class": {c: stats(v, "server_wait") for c, v in waits.items()},
            "interpretation": "SELECT may repeat in main/deferred without new activation; only unique LAUNCH is slot admission. Latest matched SELECT reason is descriptive, not an isolated causal effect."}


def in_flight(rows, at_ms):
    """Client N(t-) before the timestamp; not the server queue length."""
    return sum(r["actual_send_offset_ms"] is not None and r["actual_send_offset_ms"] < at_ms <= r["end_offset_ms"] for r in rows)


def peak_in_flight(rows, start=0, end=float("inf")):
    active = in_flight(rows, start)
    peak = active
    events = []
    for row in rows:
        sent = row["actual_send_offset_ms"]
        if sent is not None:
            if start <= sent < end:
                events.append((sent, 1))
            if start <= row["end_offset_ms"] < end:
                events.append((row["end_offset_ms"], -1))
    for _, delta in sorted(events):
        active += delta
        peak = max(peak, active)
    return peak


def diagnostics(rows, scenario, cfg):
    quarters = []
    window_ms = cfg["measurement_seconds"] * 1000
    for q in range(4):
        start, end = q * window_ms / 4, (q + 1) * window_ms / 4
        inter = [r for r in rows if r["category"] == "interactive" and start <= r["arrival_offset_ms"] < end]
        complete = [r for r in inter if r["terminal"] == "completed"]
        observed = [r for r in inter if r["total_first_ms"] is not None]
        quarters.append({"quarter": q + 1, "start_ms": start, "end_ms": end,
                         "interactive_planned": len(inter), "interactive_completed": len(complete),
                         "interactive_terminal_counts": {s: sum(r["terminal"] == s for r in inter) for s in STATES},
                         "interactive_completed_total_first_p50_ms": percentile([r["total_first_ms"] for r in complete], 50),
                         "interactive_completed_total_first_p95_ms": percentile([r["total_first_ms"] for r in complete], 95),
                         "interactive_observed_first_content": len(observed),
                         "interactive_observed_total_first_p95_ms": percentile([r["total_first_ms"] for r in observed], 95),
                         "in_flight_before_start": in_flight(rows, start),
                         "in_flight_before_end": in_flight(rows, end),
                         "in_flight_peak": peak_in_flight(rows, start, end)})
    background = sorted((r for r in rows if r["category"] == "background"), key=lambda r: r["arrival_offset_ms"])
    checks = [{"req_id": r["req_id"], "planned_ms": r["arrival_offset_ms"],
               "active_before": in_flight(rows, r["arrival_offset_ms"])} for r in background]
    first, last = quarters[0]["interactive_completed_total_first_p50_ms"], quarters[-1]["interactive_completed_total_first_p50_ms"]
    return {"client_in_flight_peak": peak_in_flight(rows), "in_flight_before_window_end": in_flight(rows, window_ms),
            "quarters": quarters, "background_arrival_checks": checks,
            "before_next_background_checks": [{"after_req_id": background[k]["req_id"], **checks[k + 1]} for k in range(len(background) - 1)],
            "burst_cycle_start_checks": checks[::3] if scenario == "burst" else [],
            "last_minus_first_quarter_completed_interactive_p50_ms": None if first is None or last is None else last - first,
            "scope": "100-second client in-flight/quarter diagnostics only; no extrapolation to long-term stability and no reused nine-point lowload criterion."}


def audit_run(directory, expected, cfg, item, env):
    manifest = read(directory / "manifest.json")
    assert manifest["status"] == "completed" and all(manifest[k] == item[k] for k in ("repeat", "scenario", "strategy"))
    assert manifest["engine_commit"] == env["engine_commit"] == cfg["engine_commit"]
    assert manifest["model_sha256"] == env["model_sha256"] == cfg["model_sha256"]
    assert manifest["engine_binary_sha256"] == env["engine_binary_sha256"]
    assert manifest["workload_sha256"] == sha(directory / "replay/trace.jsonl")
    assert manifest.get("engine_gate_supplied") is True, "Real-engine gate required by protocol"
    assert manifest["server_exit_code"] == 0
    assert lines(directory / "replay/trace.jsonl") == expected
    warm = read(directory / "warmup/summary.json")
    assert warm["planned"] == warm["terminal_counts"]["completed"] == 2
    original = read(directory / "replay/summary.json")
    rows = lines(directory / "replay/client.jsonl")
    by_id = {r["req_id"]: r for r in expected}
    assert len(rows) == len(expected) == 230 and len(by_id) == 230
    assert {r["req_id"] for r in rows} == set(by_id) and len({r["req_id"] for r in rows}) == len(rows)
    assert len(list((directory / "replay/requests").glob("*.json"))) == len(rows)
    for r in rows:
        assert read(directory / "replay/requests" / (r["req_id"] + ".json")) == r
        assert all(r[k] == value for k, value in by_id[r["req_id"]].items())
        assert r["terminal"] in STATES and r["request_class"] == r["category"] and r["client_req_id"] == r["req_id"]
        assert r["end_offset_ms"] >= r["arrival_offset_ms"]
        close(r["completion_from_planned_ms"], r["end_offset_ms"] - r["arrival_offset_ms"])
        sent, first, end = r["actual_send_offset_ms"], r["first_content_offset_ms"], r["end_offset_ms"]
        if sent is not None:
            assert r["arrival_offset_ms"] <= sent <= end
            close(r["send_lag_ms"], sent - r.get("release_offset_ms", r["arrival_offset_ms"]))
        if first is not None:
            assert sent is not None and sent <= first <= end
            close(r["ttft_ms"], first - sent)
            close(r["total_first_ms"], first - r["arrival_offset_ms"])
        if r["terminal"] == "completed":
            assert first is not None and r["http_status"] == 200 and r["stream_done"] and r["error"] is None
            assert r["finish_reason"] in ("stop", "length")
            assert sum(c["chars"] for c in r["chunk_series"]) == len(r["content"])
            assert r["chunk_series"][0]["offset_ms"] == first
            timing = r["timings"]
            assert all(timing[k] >= 0 for k in ("prompt_n", "cache_n", "predicted_n"))
            assert timing["predicted_n"] <= r["max_tokens"]
            assert timing["prompt_n"] + timing["cache_n"] + r["max_tokens"] <= 2048
    summary = client_summary(rows, cfg)
    for key in ("planned", "sent", "terminal_counts", "window_terminal_counts", "actual_send_window_denominator", "late_send_count"):
        assert summary[key] == original[key], key
    for key in ("last_end_seconds", "send_lag_max_ms", "send_lag_p95_ms", "window_completion_throughput_rps", "drain_elapsed_seconds", "output_tps_whole_run_including_drain"):
        close(summary[key], original[key])
    for category in CLASSES:
        actual, old = summary["classes"][category], original["classes"][category]
        for key, value in old.items():
            if key == "terminal_counts":
                assert all(actual[key][s] == value.get(s, 0) for s in STATES)
            elif key in actual:
                if isinstance(value, (int, float)) or value is None:
                    close(actual[key], value)
                else:
                    assert actual[key] == value
    assert summary["late_send_count"] == 0 and summary["sent"] == summary["actual_send_window_denominator"] == 230
    recorded_audit = read(directory / "audit.json")
    assert not recorded_audit["flags"] and recorded_audit["terminal_counts"] == summary["terminal_counts"]
    metrics = (directory / "metrics-after.txt").read_text(encoding="utf-8")
    for metric in ("llamacpp:requests_processing", "llamacpp:requests_deferred"):
        values = [float(line.split()[1]) for line in metrics.splitlines() if line.startswith(metric + " ")]
        assert values and all(value == 0 for value in values), metric
    expected_policy = {"LLAMA_SERVER_SIMPLE_SCHEDULER": "1" if item["strategy"] == "simple" else "0",
                       "LLAMA_SERVER_BACKGROUND_AGING_MS": str(cfg["background_aging_ms"])}
    assert read(directory / "server-policy-environment.json") == manifest["policy_environment"] == expected_policy
    command = read(directory / "server-command.json")
    assert manifest["server_command"] == command
    for option, value in (("-ngl", "99"), ("-c", "4096"), ("-np", "2"), ("-b", "2048"), ("-ub", "512")):
        assert command[command.index(option) + 1] == value
    server = parse_server_log(directory / "server.log", expected, item["strategy"], cfg)
    completed_ids = {r["req_id"] for r in rows if r["terminal"] == "completed"}
    assert completed_ids.issubset(server["launch_order"])
    successful_ids = {r["req_id"] for r in rows if r["http_status"] == 200}
    assert successful_ids.issubset(server["enqueue_order"])
    return {**item, "directory": directory.name, "summary": summary, "server": server,
            "diagnostics": diagnostics(rows, item["scenario"], cfg), "audit_status": "passed",
            "background_requests": [{"req_id": r["req_id"], "terminal": r["terminal"], "completion_from_planned_ms": r["completion_from_planned_ms"],
                                     "output_tokens": (r.get("timings") or {}).get("predicted_n"), "finish_reason": r["finish_reason"]}
                                    for r in sorted(rows, key=lambda r: r["arrival_offset_ms"]) if r["category"] == "background"]}, rows


def fraction(numerator, denominator):
    return numerator / denominator if numerator is not None and denominator is not None and denominator > 0 else None


def pair_runs(original, simple, cfg):
    a, b = original["summary"], simple["summary"]
    ai, bi = a["classes"]["interactive"], b["classes"]["interactive"]
    ab, bb = a["classes"]["background"], b["classes"]["background"]
    x, y = ai["total_first_p95_ms"], bi["total_first_p95_ms"]
    improvement = None if x is None or y is None else x - y
    relative = fraction(improvement, x)
    rate_ratio = fraction(b["output_tps_whole_run_including_drain"], a["output_tps_whole_run_including_drain"])
    rate_loss = None if rate_ratio is None else 1 - rate_ratio
    cost = fraction(bb["completion_from_planned_p95_ms"], ab["completion_from_planned_p95_ms"])
    cost = None if cost is None else cost - 1
    ao, bo = original["server"]["launch_order"], simple["server"]["launch_order"]
    shared = set(ao) & set(bo)
    ao, bo = [r for r in ao if r in shared], [r for r in bo if r in shared]
    ar, br = {r: k for k, r in enumerate(ao)}, {r: k for k, r in enumerate(bo)}
    discordant = sum(br[ao[i]] > br[ao[j]] for i in range(len(ao)) for j in range(i + 1, len(ao)))
    limits = cfg["exploratory_lines"]
    return {"repeat": original["repeat"], "scenario": original["scenario"],
            "original_total_first_p95_ms": x, "simple_total_first_p95_ms": y,
            "interactive_p95_improvement_ms": improvement, "interactive_p95_improvement_fraction": relative,
            "original_ttft_p95_ms": ai["ttft_p95_ms"], "simple_ttft_p95_ms": bi["ttft_p95_ms"],
            "original_first_content_coverage": ai["observed_first_content_samples"], "simple_first_content_coverage": bi["observed_first_content_samples"],
            "original_noncompleted": a["noncompleted"], "simple_noncompleted": b["noncompleted"],
            "original_tps_whole_run": a["output_tps_whole_run_including_drain"], "simple_tps_whole_run": b["output_tps_whole_run_including_drain"],
            "tps_loss_fraction": rate_loss, "original_background_completion_p95_ms": ab["completion_from_planned_p95_ms"],
            "simple_background_completion_p95_ms": bb["completion_from_planned_p95_ms"], "background_p95_increase_fraction": cost,
            "interactive_observation_line": None if relative is None else improvement >= limits["interactive_absolute_improvement_ms"] and relative >= limits["interactive_relative_improvement_fraction"],
            "tps_observation_line": None if rate_loss is None else rate_loss <= limits["max_full_run_output_rate_loss_fraction"],
            "background_observation_line": None if cost is None else cost <= limits["max_background_p95_completion_increase_fraction"],
            "same_sample_completion_count": a["terminal_counts"]["completed"] == b["terminal_counts"]["completed"],
            "common_launched_ids": len(shared), "launch_rank_changed_ids": sum(ar[r] != br[r] for r in shared),
            "launch_pair_order_discordances": discordant,
            "original_in_flight_peak": original["diagnostics"]["client_in_flight_peak"],
            "simple_in_flight_peak": simple["diagnostics"]["client_in_flight_peak"],
            "original_interactive_server_wait_p95_ms": original["server"]["wait_by_class"]["interactive"]["server_wait_p95_ms"],
            "simple_interactive_server_wait_p95_ms": simple["server"]["wait_by_class"]["interactive"]["server_wait_p95_ms"],
            "simple_aging_selected_unique_ids": len(simple["server"]["aging_selected_unique_request_ids"]),
            "simple_aging_launched_unique_ids": len(simple["server"]["aging_launch_unique_request_ids"])}


def write_csv(path, rows):
    with Path(path).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def fmt(value, digits=3):
    return "NA" if value is None else f"{value:.{digits}f}"


def analyze(batch, output):
    batch, output = Path(batch).resolve(), Path(output).resolve()
    assert not output.exists(), "Analysis output must be a new directory"
    assert not output.is_relative_to(batch), "Analysis output must not alter the raw evidence tree"
    status = read(batch / "batch-status.json")
    assert status["status"] == "completed" and status["runs"] == 12
    cfg, env = read(batch / "protocol.json"), read(batch / "environment.json")
    assert len(cfg["execution_order"]) == 12 and {(r["repeat"], r["scenario"], r["strategy"]) for r in cfg["execution_order"]} == {(r, s, p) for r in (1, 2, 3) for s in ("steady", "burst") for p in ("original", "simple")}
    assert read(batch / "client-gate.json")["summary"]["status"] == "passed"
    assert read(batch / "engine-gate.json")["summary"]["status"] == "passed"
    for manifest, folder in (("code-sha256.json", "source"), ("input-source-sha256.json", "input-source"), ("engine-source-sha256.json", "engine-source")):
        values = read(batch / manifest)
        assert values, manifest
        for rel, digest in values.items():
            assert sha(batch / folder / rel) == digest, (manifest, rel)
    assert set(cfg["source_files"]).issubset(read(batch / "code-sha256.json"))
    assert set(cfg["engine_source_files"]) == set(read(batch / "engine-source-sha256.json"))
    assert {"r1-E0/replay/trace.jsonl", "r1-E1/replay/trace.jsonl", "environment.json"}.issubset(read(batch / "input-source-sha256.json"))
    assert read(batch / "source/configs/prestudy-simple-policy-v1.json") == cfg
    before, after = read(batch / "engine-runtime-sha256.json"), read(batch / "engine-runtime-sha256-after.json")
    assert before == after == env["engine_runtime_files"] and before
    assert any(v["sha256"] == env["engine_binary_sha256"] for v in before.values())
    assert read(batch / "engine-source-sha256.json") == env["engine_source_sha256"]
    traces = reconstruct_traces(batch, cfg)
    recorded = read(batch / "batch-summary.json")
    assert len(recorded) == 12
    runs, request_table, totals = [], [], {s: 0 for s in STATES}
    for index, item in enumerate(cfg["execution_order"]):
        directory = batch / f"r{item['repeat']}-{item['scenario']}-{item['strategy']}"
        run, rows = audit_run(directory, traces[item["scenario"]], cfg, item, env)
        assert all(recorded[index][key] == item[key] for key in ("repeat", "scenario", "strategy"))
        assert recorded[index]["directory"] == directory.name
        assert recorded[index]["summary"] == read(directory / "replay/summary.json")
        assert recorded[index]["audit"] == read(directory / "audit.json")
        runs.append(run)
        launch = {r["clientid"]: r for r in run["server"]["launches"]}
        for state in STATES:
            totals[state] += run["summary"]["terminal_counts"][state]
        for row in rows:
            token = row.get("timings") or {}
            launched = launch.get(row["req_id"], {})
            request_table.append({"run": directory.name, **item, "req_id": row["req_id"], "category": row["category"],
                                  "planned_ms": row["arrival_offset_ms"], "actual_send_ms": row["actual_send_offset_ms"],
                                  "first_content_ms": row["first_content_offset_ms"], "end_ms": row["end_offset_ms"],
                                  "total_first_ms": row["total_first_ms"], "ttft_ms": row["ttft_ms"],
                                  "send_lag_ms": row["send_lag_ms"], "completion_from_planned_ms": row["completion_from_planned_ms"],
                                  "terminal": row["terminal"], "http_status": row["http_status"], "error": row["error"],
                                  "input_new_tokens": token.get("prompt_n"), "input_cached_tokens": token.get("cache_n"),
                                  "output_tokens": token.get("predicted_n"), "max_tokens": row["max_tokens"], "finish_reason": row["finish_reason"],
                                  "server_launch_rank": launched.get("launch_rank"), "server_wait_ms": launched.get("wait_ms"),
                                  "latest_select_reason": launched.get("latest_select_reason"), "latest_select_source": launched.get("latest_select_source"),
                                  "selection_events_before_launch": launched.get("selection_event_count_before_launch")})
    assert sum(totals.values()) == len(request_table) == 2760
    lookup = {(r["repeat"], r["scenario"], r["strategy"]): r for r in runs}
    pairs = [pair_runs(lookup[(repeat, scenario, "original")], lookup[(repeat, scenario, "simple")], cfg) for scenario in ("steady", "burst") for repeat in (1, 2, 3)]
    drift = []
    for scenario in ("steady", "burst"):
        for strategy in ("original", "simple"):
            first, last = lookup[(1, scenario, strategy)], lookup[(3, scenario, strategy)]
            x, y = first["summary"]["classes"]["interactive"]["total_first_p95_ms"], last["summary"]["classes"]["interactive"]["total_first_p95_ms"]
            drift.append({"scenario": scenario, "strategy": strategy, "r1_p95_ms": x, "r3_p95_ms": y,
                          "r3_minus_r1_ms": None if x is None or y is None else y - x})
    analysis_sha = sha(Path(__file__))
    snapshot_sha = read(batch / "code-sha256.json").get("scripts/analysis/86_analyze_simple_policy.py")
    result = {"batch": batch.name, "audit_status": "passed", "analysis_version": 1,
              "analyzed_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
              "protocol": cfg, "terminal_counts": totals, "total_requests": len(request_table),
              "warmup_requests_excluded": 24, "runs": runs, "pairs": pairs, "r3_vs_r1_diagnostics": drift,
              "analysis_code_sha256": analysis_sha, "execution_snapshot_analysis_sha256": snapshot_sha,
              "source_execution_analysis_matches_current": analysis_sha == snapshot_sha,
              "scope": "Exploratory pilot: three paired runs per condition; observations do not establish significance/equivalence or that untested complex algorithms are useless. Observation lines do not automatically approve G0.",
              "throughput_denominators": {"window_completion": "completed before 100-second cutoff / 100 seconds",
                                           "whole_run_output": "known completed output tokens / last client terminal seconds, including drain; incomplete output not estimated"}}
    # Create a new result directory only after the complete read-only audit passes.
    output.mkdir(parents=True, exist_ok=False)
    dump(output / "summary.json", result)
    write_csv(output / "paired-summary.csv", pairs)
    write_csv(output / "request-metrics.csv", request_table)
    report = ["# P0 简单调度原型配对验证", "", f"批次：`{batch.name}`。12 轮只读审计通过；2760 条测量，24 条预热另计。", "",
              f"六终态：`{totals}`。本报告先报告观察，三对探索不作正式显著性或等效结论。", "",
              "| 条件/对 | 原始路径交互 P95 ms | 简单规则 P95 ms | 省下 ms | 相对改善 % | 吞吐损失 % | 后台 P95 增加 % |", "|---|---:|---:|---:|---:|---:|---:|"]
    for p in pairs:
        report.append(f"| {p['scenario']}/{p['repeat']} | {fmt(p['original_total_first_p95_ms'])} | {fmt(p['simple_total_first_p95_ms'])} | {fmt(p['interactive_p95_improvement_ms'])} | {fmt(None if p['interactive_p95_improvement_fraction'] is None else p['interactive_p95_improvement_fraction']*100)} | {fmt(None if p['tps_loss_fraction'] is None else p['tps_loss_fraction']*100)} | {fmt(None if p['background_p95_increase_fraction'] is None else p['background_p95_increase_fraction']*100)} |")
    report += ["", "主指标从计划到达计至首段非空生成内容，包含客户端发送前等待；发送后 TTFT 和首内容覆盖数另列。只有 completed 样本进入完成者分位数，所有终态及全部已观测首内容分位数同时报告，不能用完成者变快掩盖失败。", "",
               "100 ms 且 15% 交互改善、吞吐损失≤5%、后台 P95 增加≤20%只是逐对探索观察线，不能当正式 SLO、显著性、三对全过要求或 G0 自动通过规则。复杂在线校准未测，不能宣布复杂算法无用。", "",
               "## 完整结果与工作量", "", "| 运行 | 完成/计划 | 首内容覆盖 I/B | TTFT P95 ms | 新输入/缓存 token | 输出 I/B token | 窗口完成 req/s | 全程 token/s | 排空 s | 后台完成 P50/P95/最大 ms |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in runs:
        s, i, b = r["summary"], r["summary"]["classes"]["interactive"], r["summary"]["classes"]["background"]
        report.append(f"| {r['directory']} | {s['terminal_counts']['completed']}/{s['planned']} | {i['observed_first_content_samples']}/{b['observed_first_content_samples']} | {fmt(i['ttft_p95_ms'])} | {i['new_prompt_tokens']+b['new_prompt_tokens']}/{i['cached_prompt_tokens']+b['cached_prompt_tokens']} | {i['output_tokens']}/{b['output_tokens']} | {fmt(s['window_completion_throughput_rps'])} | {fmt(s['output_tps_whole_run_including_drain'])} | {fmt(s['drain_elapsed_seconds'])} | {fmt(b['completion_from_planned_p50_ms'])}/{fmt(b['completion_from_planned_p95_ms'])}/{fmt(b['completion_from_planned_max_ms'])} |")
    report += ["", "全程输出速率分母包括排空，分子仅使用完成请求已知实际输出；非完成输出不猜测。100 秒窗口完成 req/s 是另一个指标，不按 SSE 字符块伪造窗口 token/s。各轮 finish_reason、所有后台逐条代价和六终态明细见 summary/逐请求表。", "",
               "## 机制与在途诊断", "", "| 运行 | SELECT 次数 | 唯一 LAUNCH | 老化选择 ID/实际老化启动 ID | 相对入队的启动逆序对 | 客户端在途峰值 | 100s 前仍在途 | 交互服务端等待 P95 ms |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in runs:
        q, d = r["server"], r["diagnostics"]
        report.append(f"| {r['directory']} | {q['selection_events']} | {q['launch_unique_requests']} | {len(q['aging_selected_unique_request_ids'])}/{len(q['aging_launch_unique_request_ids'])} | {q['launch_order_inversions_vs_enqueue']} | {d['client_in_flight_peak']} | {d['in_flight_before_window_end']} | {fmt(q['wait_by_class']['interactive']['server_wait_p95_ms'])} |")
    report += ["", "main/deferred 的 SELECT 可以反复选择同一请求且没有新增启动，不能把 SELECT 次数当准入次数。LAUNCH 按 request ID 去重，匹配之前最近的同 ID SELECT 解释原因；有老化原因不等于已证明长期无饥饿。对照保留原生 slot 亲和等行为，不能称严格 FIFO。", "",
               "逐对启动次序分歧、相同请求的逆序对、服务端自身时钟的等待、每季度客户端在途/交互等待、30 个后台到达前检查和突发十簇检查保存在 summary.json；客户端在途数不是服务器排队数。所有这些只描述本轮 100 秒，不声明长期稳定。r3 相对 r1 只作时间漂移诊断。", "",
               "## 判读和边界", "", "简单规则若改善且代价能接受，优先作为下一阶段基线；若后台代价大，先研究优先级/老化/配额取舍。未改善时先看是否产生实际启动次序差异，以及等待是否由已运行的两个非抢占任务主导。当前未实现完整硬 KV 账本，也未测试复杂算法的增量收益。", "",
               "单设备、单模型、固定合成内容和默认前缀缓存限制外推；实际长度与缓存变化完整报告，不事后删样凑等工作量。三对运行是重复单位，2760 条共享队列请求不能视为2760次独立实验。", "",
               "## 数据与复核", "", f"原始证据：`{batch}`；本报告未修改该目录。", "",
               "[机器摘要](summary.json) · [配对结果](paired-summary.csv) · [逐请求指标](request-metrics.csv) · [原始证据 SHA256](evidence-sha256.json)", "",
               f"分析代码 SHA256：`{analysis_sha}`；与采前快照一致：`{analysis_sha == snapshot_sha}`。执行源码、输入旧轨迹、引擎源码快照哈希已核对；运行时 binary/共享库的采前采后清单一致。历史复核不强求当前工作目录 binary 仍为旧版本。"]
    (output / "README.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    dump(output / "evidence-sha256.json", {p.relative_to(batch).as_posix(): sha(p) for p in sorted(batch.rglob("*")) if p.is_file()})
    dump(output / "report-sha256.json", {p.name: sha(p) for p in sorted(output.iterdir()) if p.is_file()})
    print(json.dumps({"audit": "passed", "requests": len(request_table), "terminal_counts": totals, "pairs": pairs}, ensure_ascii=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    analyze(args.batch, args.output)
