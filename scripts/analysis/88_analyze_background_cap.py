#!/usr/bin/env python3
"""Read-only audit and append-only report for the eighteen-run background-cap pilot.

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
    mode = 0 if strategy == "original" else 1
    assert f"SCHED_CONFIG mode={mode} background_aging_ms={cfg['background_aging_ms']} preemptive=0" in text
    cap = int(cfg["strategies"][strategy]["environment"]["LLAMA_SERVER_MAX_RUNNING_BACKGROUND"])
    assert f"max_running_background={cap}" in text
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
            "cap_lifecycle": audit_cap_lifecycle(text, by_id, launches, cap),
            "interpretation": "SELECT may repeat in main/deferred without new activation; only unique LAUNCH is slot admission. Latest matched SELECT reason is descriptive, not an isolated causal effect."}


def audit_cap_lifecycle(text, expected_by_id, base_launches, cap):
    """Reconstruct native slot occupation from server events, never client clocks."""
    active, launches, releases, deferrals = {}, [], [], []
    peak, last_ms = 0, -1
    for number,line in enumerate(text.splitlines(),1):
        marker=next((key for key in ("SCHED_CAP_LAUNCH","SCHED_CAP_RELEASE","SCHED_CAP_DEFER") if key in line),None)
        if marker is None: continue
        f=dict(re.findall(r"(\w+)=([^\s]+)",line))
        f["line"]=number
        for key in ("id","slot","running_background_before","running_background_after","running_background","cap","event_ms"):
            if key in f: f[key]=int(f[key])
        assert f["cap"]==cap and f["event_ms"]>=last_ms,(marker,f)
        last_ms=f["event_ms"]
        count=sum(v["class"]=="background" for v in active.values())
        if marker=="SCHED_CAP_DEFER":
            assert f["class"]=="background" and cap>0 and f["running_background"]==count and count>=cap,f
            deferrals.append(f)
            continue
        assert f["running_background_before"]==count,(marker,f,count)
        slot=f["slot"]
        assert f["class"] in CLASSES and slot in (0,1),f
        if marker=="SCHED_CAP_LAUNCH":
            assert slot not in active,("Occupied slot relaunched",f,active)
            assert all(v["id"]!=f["id"] for v in active.values()),f
            active[slot]=f
            launches.append(f)
        else:
            assert slot in active,("Release without launch",f)
            old=active.pop(slot)
            assert all(f[k]==old[k] for k in ("id","clientid","class","slot")),(f,old)
            releases.append({**f,"launch_event_ms":old["event_ms"],"slot_residence_ms":f["event_ms"]-old["event_ms"]})
        after=sum(v["class"]=="background" for v in active.values())
        assert f["running_background_after"]==after,(marker,f,after)
        if cap>0: assert after<=cap,("Cap exceeded",f)
        peak=max(peak,after)
    assert launches and len(launches)==len(releases) and not active,"CAP lifecycle incomplete"
    measured=[r for r in launches if r["clientid"] in expected_by_id]
    measured_releases=[r for r in releases if r["clientid"] in expected_by_id]
    assert len({r["clientid"] for r in measured})==len(measured),"Duplicate CAP measurement launch"
    assert len({r["clientid"] for r in measured_releases})==len(measured_releases),"Duplicate CAP release"
    assert {r["clientid"] for r in measured}=={r["clientid"] for r in base_launches}
    assert {r["clientid"] for r in measured_releases}=={r["clientid"] for r in measured}
    for row in measured:
        assert row["class"]==expected_by_id[row["clientid"]]["category"]
        old=next(r for r in base_launches if r["clientid"]==row["clientid"])
        assert row["id"]==old["id"]
    return {"audit_status":"passed","configured_cap":cap,"running_background_peak":peak,"launch_events":len(launches),
        "release_events":len(releases),"defer_events":len(deferrals),"final_active_slots":len(active),
        "measurement_launches":measured,"measurement_releases":measured_releases,"deferrals":deferrals,
        "all_launches":launches,"all_releases":releases,"clock":"server native event_ms and logfile order only"}


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
    assert original["schema_version"] == cfg["expected_client_schema_version"] == 4
    assert original["client_variant"] == cfg["expected_client_variant"] == "simple_policy_replay_v2"
    persisted = original["persistence"]
    assert persisted["mode"] == cfg["expected_persistence_mode"]
    assert persisted["recorded_requests"] == persisted["written_requests"] == len(expected)
    assert persisted["queue_high_water"] >= 0 and persisted["drain_seconds"] >= 0
    assert persisted["request_tasks_finished_offset_ms"] >= 0
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
    summary["persistence"] = dict(persisted)
    summary["schema_version"] = original["schema_version"]
    summary["client_variant"] = original["client_variant"]
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
    expected_policy = cfg["strategies"][item["strategy"]]["environment"]
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


def compare_runs(reference, candidate, cfg):
    a, b = reference["summary"], candidate["summary"]
    ai, bi = a["classes"]["interactive"], b["classes"]["interactive"]
    ab, bb = a["classes"]["background"], b["classes"]["background"]
    x, y = ai["total_first_p95_ms"], bi["total_first_p95_ms"]
    improvement = None if x is None or y is None else x - y
    relative = fraction(improvement, x)
    ratio = fraction(b["output_tps_whole_run_including_drain"], a["output_tps_whole_run_including_drain"])
    loss = None if ratio is None else 1-ratio
    cost = fraction(bb["completion_from_planned_p95_ms"], ab["completion_from_planned_p95_ms"])
    cost = None if cost is None else cost-1
    ao, bo = reference["server"]["launch_order"], candidate["server"]["launch_order"]
    shared = set(ao) & set(bo)
    ao, bo = [r for r in ao if r in shared], [r for r in bo if r in shared]
    ar, br = {r:k for k,r in enumerate(ao)}, {r:k for k,r in enumerate(bo)}
    limits=cfg["exploratory_lines"]
    target=limits["interactive_p95_engineering_target_ms"]
    target_with_cost=None if y is None or loss is None or cost is None else y<=target and a["noncompleted"]==b["noncompleted"]==0 and loss<=limits["max_full_run_output_rate_loss_fraction"] and cost<=limits["max_background_p95_completion_increase_fraction"]
    all_lines=None if target_with_cost is None or relative is None else target_with_cost and improvement>=limits["interactive_absolute_improvement_ms"] and relative>=limits["interactive_relative_improvement_fraction"]
    return {"repeat":reference["repeat"],"scenario":reference["scenario"],
        "reference":reference["strategy"],"candidate":candidate["strategy"],
        "reference_total_first_p95_ms":x,"candidate_total_first_p95_ms":y,
        "interactive_p95_improvement_ms":improvement,"interactive_p95_improvement_fraction":relative,
        "reference_total_first_p50_ms":ai["total_first_p50_ms"],"candidate_total_first_p50_ms":bi["total_first_p50_ms"],
        "reference_ttft_p95_ms":ai["ttft_p95_ms"],"candidate_ttft_p95_ms":bi["ttft_p95_ms"],
        "reference_first_content_coverage":ai["observed_first_content_samples"],"candidate_first_content_coverage":bi["observed_first_content_samples"],
        "reference_noncompleted":a["noncompleted"],"candidate_noncompleted":b["noncompleted"],
        "reference_tps_whole_run":a["output_tps_whole_run_including_drain"],"candidate_tps_whole_run":b["output_tps_whole_run_including_drain"],
        "tps_loss_fraction":loss,"reference_background_completion_p95_ms":ab["completion_from_planned_p95_ms"],
        "candidate_background_completion_p95_ms":bb["completion_from_planned_p95_ms"],"background_p95_increase_fraction":cost,
        "interactive_observation_line":None if relative is None else improvement>=limits["interactive_absolute_improvement_ms"] and relative>=limits["interactive_relative_improvement_fraction"],
        "tps_observation_line":None if loss is None else loss<=limits["max_full_run_output_rate_loss_fraction"],
        "background_observation_line":None if cost is None else cost<=limits["max_background_p95_completion_increase_fraction"],
        "reference_engineering_target_p95_ms":target,"reference_engineering_target_met":None if x is None else x<=target and a["noncompleted"]==0,
        "candidate_engineering_target_p95_ms":target,"candidate_engineering_target_met":None if y is None else y<=target and b["noncompleted"]==0,
        "reference_all_planned_completed":a["noncompleted"]==0,"candidate_all_planned_completed":b["noncompleted"]==0,
        "target_with_cost_supported":target_with_cost,"candidate_meets_all_exploratory_lines":all_lines,
        "adding_cap_meets_all_exploratory_lines":all_lines if candidate["strategy"]=="capped" else None,
        "same_sample_completion_count":a["terminal_counts"]["completed"]==b["terminal_counts"]["completed"],
        "common_launched_ids":len(shared),"launch_rank_changed_ids":sum(ar[r]!=br[r] for r in shared),
        "launch_pair_order_discordances":sum(br[ao[i]]>br[ao[j]] for i in range(len(ao)) for j in range(i+1,len(ao))),
        "reference_in_flight_peak":reference["diagnostics"]["client_in_flight_peak"],"candidate_in_flight_peak":candidate["diagnostics"]["client_in_flight_peak"],
        "reference_background_running_peak":reference["server"]["cap_lifecycle"]["running_background_peak"],
        "candidate_background_running_peak":candidate["server"]["cap_lifecycle"]["running_background_peak"],
        "reference_interactive_server_wait_p95_ms":reference["server"]["wait_by_class"]["interactive"]["server_wait_p95_ms"],
        "candidate_interactive_server_wait_p95_ms":candidate["server"]["wait_by_class"]["interactive"]["server_wait_p95_ms"],
        "candidate_aging_selected_unique_ids":len(candidate["server"]["aging_selected_unique_request_ids"]),
        "candidate_aging_launched_unique_ids":len(candidate["server"]["aging_launch_unique_request_ids"])}


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
    assert not output.is_relative_to(batch), "Analysis output must not alter raw evidence"
    status = read(batch / "batch-status.json")
    assert status["status"] == "completed" and status["runs"] == 18
    cfg, env = read(batch / "protocol.json"), read(batch / "environment.json")
    assert cfg["study"] == "background-cap-v1" and cfg["max_measurement_runs"] == 18
    expected_items={(r,s,p) for r in (1,2,3) for s in ("steady","burst") for p in ("original","simple","capped")}
    assert len(cfg["execution_order"]) == 18 and {(r["repeat"],r["scenario"],r["strategy"]) for r in cfg["execution_order"]}==expected_items
    for scenario in ("steady","burst"):
        positions={p:[] for p in ("original","simple","capped")}
        for repeat in (1,2,3):
            block=[r for r in cfg["execution_order"] if r["repeat"]==repeat and r["scenario"]==scenario]
            for position,item in enumerate(block): positions[item["strategy"]].append(position)
        assert all(sorted(v)==[0,1,2] for v in positions.values()), "Unbalanced strategy position"
    client_gate=read(batch / "client-gate.json")["summary"]
    assert client_gate["status"] == "passed"
    slow=client_gate["slow_disk_test"]
    assert slow["status"]=="passed" and slow["delay_ms"]==80 and slow["requests"]==40 and slow["rate_rps"]==20
    assert slow["v1"]["send_lag_max_ms"]>cfg["send_lag_limit_ms"] and slow["v1"]["late_send_count"]>0
    assert slow["v1"]["completed"]==slow["v2"]["completed"]==40
    assert slow["v2"]["send_lag_max_ms"]<=cfg["send_lag_limit_ms"] and slow["v2"]["late_send_count"]==0
    assert slow["cutoff"]["planned"]==slow["cutoff"]["unfinished"]==8
    assert slow["cutoff"]["last_end_offset_ms"]<400 and slow["cutoff"]["request_tasks_finished_offset_ms"]<400
    engine_gate=read(batch / "engine-gate.json")["summary"]
    assert engine_gate["status"]=="passed" and engine_gate["study"]=="background-cap-v1"
    assert engine_gate["engine_commit"]==env["engine_commit"] and engine_gate["model_sha256"]==env["model_sha256"]
    assert engine_gate["engine_inventory_unchanged"] is True
    for manifest,folder in (("code-sha256.json","source"),("input-source-sha256.json","input-source"),("engine-source-sha256.json","engine-source")):
        values=read(batch / manifest)
        assert values,manifest
        for rel,digest in values.items(): assert sha(batch/folder/rel)==digest,(manifest,rel)
    code_hashes=read(batch/"code-sha256.json")
    assert set(cfg["source_files"]).issubset(code_hashes)
    assert all(code_hashes.get(rel)==digest for rel,digest in engine_gate["source_sha256"].items())
    assert set(cfg["engine_source_files"])==set(read(batch/"engine-source-sha256.json"))
    assert {"r1-E0/replay/trace.jsonl","r1-E1/replay/trace.jsonl","environment.json"}.issubset(read(batch/"input-source-sha256.json"))
    assert read(batch/"source/configs/prestudy-background-cap-v1.json")==cfg
    before,after=read(batch/"engine-runtime-sha256.json"),read(batch/"engine-runtime-sha256-after.json")
    assert before==after==env["engine_runtime_files"] and before
    assert any(v["sha256"]==env["engine_binary_sha256"] for v in before.values())
    assert read(batch/"model-sha256-after.json")["sha256"]==env["model_sha256"]==cfg["model_sha256"]
    assert read(batch/"engine-source-sha256.json")==env["engine_source_sha256"]==engine_gate["engine_source_sha256"]
    for rel in cfg["engine_source_files"]:
        assert sha(batch/"source/patches/background-cap-v1"/rel)==sha(batch/"engine-source"/rel)
    traces=reconstruct_traces(batch,cfg)
    recorded=read(batch/"batch-summary.json")
    assert len(recorded)==18
    runs,request_table,totals=[],[],{s:0 for s in STATES}
    for index,item in enumerate(cfg["execution_order"]):
        directory=batch/f"r{item['repeat']}-{item['scenario']}-{item['strategy']}"
        run,rows=audit_run(directory,traces[item["scenario"]],cfg,item,env)
        assert all(recorded[index][key]==item[key] for key in ("repeat","scenario","strategy"))
        assert recorded[index]["directory"]==directory.name
        assert recorded[index]["summary"]==read(directory/"replay/summary.json")
        assert recorded[index]["audit"]==read(directory/"audit.json")
        runs.append(run)
        launch={r["clientid"]:r for r in run["server"]["launches"]}
        cap={r["clientid"]:r for r in run["server"]["cap_lifecycle"]["measurement_launches"]}
        for state in STATES: totals[state]+=run["summary"]["terminal_counts"][state]
        for row in rows:
            token=row.get("timings") or {}
            launched=launch.get(row["req_id"],{})
            admitted=cap.get(row["req_id"],{})
            request_table.append({"run":directory.name,**item,"req_id":row["req_id"],"category":row["category"],
                "planned_ms":row["arrival_offset_ms"],"actual_send_ms":row["actual_send_offset_ms"],"first_content_ms":row["first_content_offset_ms"],"end_ms":row["end_offset_ms"],
                "total_first_ms":row["total_first_ms"],"ttft_ms":row["ttft_ms"],"send_lag_ms":row["send_lag_ms"],"completion_from_planned_ms":row["completion_from_planned_ms"],
                "terminal":row["terminal"],"http_status":row["http_status"],"error":row["error"],"input_new_tokens":token.get("prompt_n"),"input_cached_tokens":token.get("cache_n"),
                "output_tokens":token.get("predicted_n"),"max_tokens":row["max_tokens"],"finish_reason":row["finish_reason"],"server_launch_rank":launched.get("launch_rank"),
                "server_wait_ms":launched.get("wait_ms"),"latest_select_reason":launched.get("latest_select_reason"),"latest_select_source":launched.get("latest_select_source"),
                "selection_events_before_launch":launched.get("selection_event_count_before_launch"),"server_slot":admitted.get("slot"),"running_background_at_launch":admitted.get("running_background_after")})
    assert sum(totals.values())==len(request_table)==4140
    lookup={(r["repeat"],r["scenario"],r["strategy"]):r for r in runs}
    pairs=[compare_runs(lookup[(repeat,scenario,reference)],lookup[(repeat,scenario,candidate)],cfg)
        for scenario in ("steady","burst") for repeat in (1,2,3)
        for reference,candidate in (("original","simple"),("original","capped"),("simple","capped"))]
    drift=[]
    for scenario in ("steady","burst"):
        for strategy in ("original","simple","capped"):
            first,last=lookup[(1,scenario,strategy)],lookup[(3,scenario,strategy)]
            x,y=first["summary"]["classes"]["interactive"]["total_first_p95_ms"],last["summary"]["classes"]["interactive"]["total_first_p95_ms"]
            drift.append({"scenario":scenario,"strategy":strategy,"r1_p95_ms":x,"r3_p95_ms":y,"r3_minus_r1_ms":None if x is None or y is None else y-x})
    analysis_sha=sha(Path(__file__))
    snapshot_sha=read(batch/"code-sha256.json").get("scripts/analysis/88_analyze_background_cap.py")
    result={"batch":batch.name,"audit_status":"passed","analysis_version":"background-cap-v1",
        "analyzed_at":datetime.now(timezone(timedelta(hours=8))).isoformat(),"protocol":cfg,"terminal_counts":totals,"total_requests":len(request_table),
        "primary_contrast":cfg["primary_contrast"],"measurement_revision":2,"measurement_repair":cfg["measurement_repair"],"predecessor_batches":cfg["predecessor_batches"],
        "warmup_requests_excluded":36,"runs":runs,"pairs":pairs,"r3_vs_r1_diagnostics":drift,"analysis_code_sha256":analysis_sha,
        "execution_snapshot_analysis_sha256":snapshot_sha,"source_execution_analysis_matches_current":analysis_sha==snapshot_sha,
        "scope":"Exploratory three-strategy blocks: three repeats per scenario. The 1s target is an engineering hypothesis, not a user SLO. No statistical equivalence/significance, G0 human approval, or untested complex algorithm cost/benefit claim.",
        "throughput_denominators":{"window_completion":"completed before 100s /100s","whole_run_output":"known completed output tokens / last client terminal seconds, including drain; incomplete output not estimated"}}
    output.mkdir(parents=True,exist_ok=False)
    dump(output/"summary.json",result)
    write_csv(output/"paired-summary.csv",pairs)
    write_csv(output/"request-metrics.csv",request_table)
    report=["# P0 后台并发上限：三策略同期验证","",f"批次：`{batch.name}`。18轮只读审计通过，4140条测量，36条固定预热另计。","",
        f"六终态：`{totals}`。新binary三策略同期重采，未拼接旧12轮。每场景3个三策略块，全部3种比较逐块报告。","",
        "主判断是capped相对simple，另外两种对比完整报告。1秒为见过旧结果后、新cap性能采集前登记的工程假设，不是用户业务SLO；任何非完成使目标满足标记为False。target_with_cost_supported要求两组全完成、候选≤1s及后台/速率成本过线；candidate_meets_all_exploratory_lines另加100ms且15%实质收益，capped时同值列adding_cap_meets_all_exploratory_lines。达到目标与代价线不自动说明值得增加规则。这里成本是服务性能取舍；复杂算法的增量收益、CPU计算和维护成本仍未知。不能从达标或无差异直接证明复杂不值得。","",
        "| 场景/重复 | 对照→候选 | 交互P95 对照/候选 ms | 改善 ms / % | 速率损失 % | 后台P95增幅 % | 候选≤1s | 目标和代价 | 全部探索线 |",
        "|---|---|---:|---:|---:|---:|---|---|---|"]
    for p in pairs:
        report.append(f"| {p['scenario']}/{p['repeat']} | {p['reference']}→{p['candidate']} | {fmt(p['reference_total_first_p95_ms'])}/{fmt(p['candidate_total_first_p95_ms'])} | {fmt(p['interactive_p95_improvement_ms'])}/{fmt(None if p['interactive_p95_improvement_fraction'] is None else p['interactive_p95_improvement_fraction']*100)} | {fmt(None if p['tps_loss_fraction'] is None else p['tps_loss_fraction']*100)} | {fmt(None if p['background_p95_increase_fraction'] is None else p['background_p95_increase_fraction']*100)} | {p['candidate_engineering_target_met']} | {p['target_with_cost_supported']} | {p['candidate_meets_all_exploratory_lines']} |")
    report += ["","主指标是计划到达至首段非空内容；TTFT另报。100ms且15%改善、速率损失≤5%、后台完成P95增幅≤20%仅探索线，各对完整列示，不用于挑样或自动批准G0。只有完成者进入完成分位数，全部终态、首内容覆盖及所有已观察首内容分位数同时保留。","",
        "## 全部运行、实际工作量与持久化","","| 运行 | 完成/计划 | 交互P50/P95 ms | 后台完成P95 ms | 新输入/缓存 token | 输出I/B | 窗口 req/s | 全程 token/s | 排空s | 最大迟发ms |","|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in runs:
        q,i,b=r["summary"],r["summary"]["classes"]["interactive"],r["summary"]["classes"]["background"]
        report.append(f"| {r['directory']} | {q['terminal_counts']['completed']}/{q['planned']} | {fmt(i['total_first_p50_ms'])}/{fmt(i['total_first_p95_ms'])} | {fmt(b['completion_from_planned_p95_ms'])} | {i['new_prompt_tokens']+b['new_prompt_tokens']}/{i['cached_prompt_tokens']+b['cached_prompt_tokens']} | {i['output_tokens']}/{b['output_tokens']} | {fmt(q['window_completion_throughput_rps'])} | {fmt(q['output_tps_whole_run_including_drain'])} | {fmt(q['drain_elapsed_seconds'])} | {fmt(q['send_lag_max_ms'])} |")
    report += ["","全程输出速率只使用已知完成输出，分母至最后客户端终态并包含排空；100秒窗口完成率为req/s，不用SSE字符块伪造窗口token/s。逐请求长度、finish_reason、首内容覆盖和后台逐条代价见机器摘要与CSV。","",
        "| 运行 | 持久化记录/写入 | 队列峰值 | 请求任务结束ms | 写盘排空s |","|---|---:|---:|---:|---:|"]
    for r in runs:
        p=r["summary"]["persistence"]
        report.append(f"| {r['directory']} | {p['recorded_requests']}/{p['written_requests']} | {p['queue_high_water']} | {fmt(p['request_tasks_finished_offset_ms'])} | {fmt(p['drain_seconds'])} |")
    report += ["","## Slot生命周期、上限与机制","","| 运行 | SELECT/唯一LAUNCH | 运行后台峰值 | CAP启动/释放 总数 | cap挡回事件 | 老化启动ID数 | 交互服务等待P95ms |","|---|---:|---:|---:|---:|---:|---:|"]
    for r in runs:
        q=r["server"]; c=q["cap_lifecycle"]
        report.append(f"| {r['directory']} | {q['selection_events']}/{q['launch_unique_requests']} | {c['running_background_peak']} | {c['launch_events']}/{c['release_events']} | {c['defer_events']} | {len(q['aging_launch_unique_request_ids'])} | {fmt(q['wait_by_class']['interactive']['server_wait_p95_ms'])} |")
    report += ["","CAP生命周期按服务端同一日志时序重构，含预热和控制任务；核对每个slot启动与释放、前后后台计数、capped最大≤1、结尾活跃集为空。客户端终态不替代slot释放。SELECT可重复；唯一LAUNCH才是实际启动，最近SELECT原因只是诊断。客户端在途不等于服务端排队，服务时间不与客户端/GPU墙钟相减。","",
        "## 解释边界与复核","","三策略块只有每场景3次独立重复；4140条请求共享队列，不作逐请求独立bootstrap，不将无差异写成等效。单卡单模型固定内容、缓存和短窗口限制外推；r3−r1诊断、四季度等待/在途、30后台到达检查和10突发簇见机器摘要。","",
        "cap若满足1秒工程目标且相对original与simple代价可接受，可支持当前条件下优先简单基线；若后台代价超线，应报告取舍；若未达目标，应结合实际slot生命周期定位等待来源。任何分流都不能直接宣布复杂算法不值得。","",
        f"原始证据：`{batch}`；只读分析未修改。当前分析SHA256 `{analysis_sha}`，与采前快照一致：`{analysis_sha==snapshot_sha}`。采前采后运行库清单、源码和输入快照均已核对。","",
        "[机器摘要](summary.json) · [全部三种配对](paired-summary.csv) · [逐请求指标](request-metrics.csv) · [原始证据SHA256](evidence-sha256.json)"]
    (output/"README.md").write_text("\n".join(report)+"\n",encoding="utf-8")
    dump(output/"evidence-sha256.json",{p.relative_to(batch).as_posix():sha(p) for p in sorted(batch.rglob("*")) if p.is_file()})
    dump(output/"report-sha256.json",{p.name:sha(p) for p in sorted(output.iterdir()) if p.is_file()})
    print(json.dumps({"audit":"passed","requests":len(request_table),"terminal_counts":totals,"pairs":pairs},ensure_ascii=False))
    return result


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--batch",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    analyze(args.batch,args.output)
