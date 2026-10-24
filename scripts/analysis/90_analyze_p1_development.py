#!/usr/bin/env python3
"""Independently recompute P1 development records; write a new report directory.

No collector summary is used as a numerical source. All request metrics come
from raw client records; server release times come only from server CAP events.
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

ROOT = Path(__file__).resolve().parents[2]
STATES = ("completed", "rejected", "timeout", "cancelled", "failed", "unfinished")
CLASSES = ("interactive", "background")
SCENARIOS = ("main_bursty_mixed", "boundary_steady", "boundary_sparse")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def jsonlines(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return "infinity" if value > 0 else "negative_infinity" if value < 0 else "not_a_number"
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(v) for v in value]
    return value


def dump(path, value):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(safe(value), handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def quantile(values, p):
    """Type 7 with an explicit extended-real rule, avoiding inf * zero NaN."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    low = int(position)
    fraction = position - low
    a, b = ordered[low], ordered[min(low + 1, len(ordered) - 1)]
    if fraction == 0 or a == b:
        return a
    if math.isinf(a) or math.isinf(b):
        return math.inf
    return a + fraction * (b - a)


def distribution(values):
    known = [v for v in values if v is not None]
    return {"known": len(known), "missing": len(values) - len(known),
            "min": min(known, default=None), "p50": quantile(known, .5),
            "p95": quantile(known, .95), "max": max(known, default=None),
            "sum": sum(known)}


def outcome_bounds(rows, event):
    """Bounds on all-arrival planned latency, with explicit non-success policy."""
    lower, upper = [], []
    observed = 0
    for row in rows:
        first = row.get("first_content_offset_ms")
        if event == "first" and first is not None:
            value = first - row["arrival_offset_ms"]
            lower.append(value)
            upper.append(value)
            observed += 1
        elif event == "completion" and row["terminal"] == "completed":
            value = row["end_offset_ms"] - row["arrival_offset_ms"]
            lower.append(value)
            upper.append(value)
            observed += 1
        elif row["terminal"] in ("timeout", "unfinished"):
            lower.append(max(0, row["end_offset_ms"] - row["arrival_offset_ms"]))
            upper.append(math.inf)
        else:
            lower.append(math.inf)
            upper.append(math.inf)
    bounds = {f"p{int(p*100)}_ms": {"lower": quantile(lower, p), "upper": quantile(upper, p)} for p in (.5, .95)}
    return {"population": len(rows), "event_observed": observed,
            "identification": "point" if lower == upper else "interval",
            "bounds": bounds,
            "rule": "observed event exact; no-event timeout/unfinished is [observation, infinity]; no-event rejected/failed/cancelled is service failure at infinity"}


def normalized_record(row, run_id):
    timings = row.get("timings") or {}
    predicted = timings.get("predicted_n") if row.get("server_final_received") else None
    new = row.get("new_prompt_tokens")
    cached = row.get("cached_prompt_tokens")
    if new is None:
        new = timings.get("prompt_n")
    if cached is None:
        cached = timings.get("cache_n")
    output = row.get("output_tokens") if row.get("output_tokens_known") is True else None
    if output is not None:
        assert isinstance(output, int) and output >= 0 and output == predicted, "Output count must match the native final timings"
    actual_input = new + cached if new is not None and cached is not None else None
    first = row.get("first_content_offset_ms")
    sent = row.get("actual_send_offset_ms")
    planned = row["arrival_offset_ms"]
    assert row["terminal"] in STATES and row["end_offset_ms"] >= planned
    if first is not None:
        assert sent is not None and sent <= first <= row["end_offset_ms"]
    native = row.get("native_final") or {}
    stop_type = native.get("stop_type")
    hit_limit = native.get("stopped_limit") is True or stop_type == "limit" or row.get("finish_reason") == "length"
    eos = native.get("stopped_eos") is True or stop_type == "eos"
    result = {key: row.get(key) for key in ("req_id", "content_id", "category", "split", "seed", "category_index", "template_family", "source_id", "content_sha256", "input_token_bucket", "input_token_range", "max_tokens", "terminal", "http_status", "error", "finish_reason", "actual_send_offset_ms", "first_content_offset_ms", "end_offset_ms", "arrival_offset_ms", "tokens_evaluated")}
    result.update(run_id=run_id, expected_input_tokens=row.get("input_tokens"), input_tokens=actual_input,
                  new_prompt_tokens=new, cached_prompt_tokens=cached,
                  output_tokens=output, output_tokens_known=output is not None,
                  ttft_ms=first - sent if first is not None else None,
                  total_first_ms=first - planned if first is not None else None,
                  completion_from_planned_ms=row["end_offset_ms"] - planned,
                  send_lag_ms=sent - row.get("release_offset_ms", planned) if sent is not None else None,
                  stopped_limit=hit_limit, stopped_eos=eos, stop_type=stop_type,
                  input_length_verified=actual_input is not None and actual_input == row.get("input_tokens") == row.get("tokens_evaluated"))
    for field in ("ttft_ms", "total_first_ms", "completion_from_planned_ms", "send_lag_ms"):
        if row.get(field) is not None and result[field] is not None:
            assert abs(row[field] - result[field]) < .001, f"Stored {field} differs from event timestamps"
    return result


def checkpoints(rows, measurement_seconds):
    end = max(measurement_seconds, math.ceil(max(r["end_offset_ms"] for r in rows) / 10000) * 10)
    points = []
    for second in range(10, int(end) + 1, 10):
        cutoff, previous = second * 1000, (second - 10) * 1000
        arrived = [r for r in rows if r["arrival_offset_ms"] <= cutoff]
        terminal = [r for r in arrived if r["end_offset_ms"] <= cutoff]
        complete = [r for r in terminal if r["terminal"] == "completed"]
        points.append({"at_seconds": second, "arrived_cumulative": len(arrived),
                       "completed_cumulative": len(complete), "terminal_cumulative": len(terminal),
                       "still_in_client_system": len(arrived) - len(terminal),
                       "not_successfully_completed": len(arrived) - len(complete),
                       "arrived_last_10s": sum(previous < r["arrival_offset_ms"] <= cutoff or previous == 0 == r["arrival_offset_ms"] for r in rows),
                       "completed_last_10s": sum(r["terminal"] == "completed" and previous < r["end_offset_ms"] <= cutoff for r in rows),
                       "by_class_still_in_client_system": {c: sum(r["category"] == c and r["end_offset_ms"] > cutoff for r in arrived) for c in CLASSES}})
    return points


def summarize_records(rows, measurement_seconds):
    last_end = max(r["end_offset_ms"] for r in rows) / 1000
    denominator = max(measurement_seconds, last_end)
    count = Counter(r["terminal"] for r in rows)
    known = [r["output_tokens"] for r in rows if r["output_tokens_known"]]
    all_known = len(known) == len(rows)
    sent = [r for r in rows if r["actual_send_offset_ms"] is not None]
    result = {"planned": len(rows), "sent": len(sent), "terminal_counts": {s: count[s] for s in STATES},
              "output_tokens_known_requests": len(known), "output_tokens_missing_requests": len(rows) - len(known),
              "observed_output_tokens_lower_bound": sum(known),
              "all_request_output_tokens": sum(known) if all_known else None,
              "whole_output_rate_tokens_per_second": sum(known) / denominator if all_known else None,
              "observed_output_rate_lower_bound_tokens_per_second": sum(known) / denominator,
              "rate_denominator_seconds": denominator, "last_terminal_seconds": last_end,
              "client_drain_seconds": max(0, last_end - measurement_seconds),
              "window_output_rate_tokens_per_second": None,
              "window_output_rate_note": "Per-token timestamps are unavailable; character/SSE chunks are not token counts.",
              "send_lag_ms": distribution([r["send_lag_ms"] for r in sent]),
              "late_send_over_50ms": sum(r["send_lag_ms"] > 50 for r in sent),
              "at_window_end_in_client_system": sum(r["arrival_offset_ms"] < measurement_seconds * 1000 < r["end_offset_ms"] for r in rows),
              "completed_by_window_end": sum(r["terminal"] == "completed" and r["end_offset_ms"] <= measurement_seconds * 1000 for r in rows),
              "input_verified": sum(r["input_length_verified"] for r in rows),
              "classes": {}}
    for category in CLASSES:
        group = [r for r in rows if r["category"] == category]
        if not group:
            continue
        complete = [r for r in group if r["terminal"] == "completed"]
        first = [r for r in group if r["first_content_offset_ms"] is not None]
        result["classes"][category] = {
            "planned": len(group), "terminal_counts": {s: sum(r["terminal"] == s for r in group) for s in STATES},
            "input_tokens": distribution([r["input_tokens"] for r in group]),
            "expected_input_tokens": distribution([r["expected_input_tokens"] for r in group]),
            "new_prompt_tokens": distribution([r["new_prompt_tokens"] for r in group]),
            "cached_prompt_tokens": distribution([r["cached_prompt_tokens"] for r in group]),
            "output_tokens": distribution([r["output_tokens"] for r in group]),
            "limit_stopped": sum(r["stopped_limit"] for r in group),
            "eos_stopped": sum(r["stopped_eos"] for r in group),
            "other_or_unknown_stop": sum(not r["stopped_limit"] and not r["stopped_eos"] for r in group),
            "first_content_observed": len(first), "first_content_missing": len(group) - len(first),
            "observed_ttft_ms": distribution([r["ttft_ms"] for r in first]),
            "observed_total_first_ms": distribution([r["total_first_ms"] for r in first]),
            "all_arrival_total_first": outcome_bounds(group, "first"),
            "completed_only_completion_ms": distribution([r["completion_from_planned_ms"] for r in complete]),
            "all_arrival_completion": outcome_bounds(group, "completion"),
            "input_token_buckets": dict(Counter(str(r["input_token_bucket"]) for r in group)),
            "template_families": sorted({r["template_family"] for r in group if r["template_family"]})}
    return result


def server_events(path, ids):
    launch, release, active, selection_wait, flags = {}, {}, {}, {}, []
    max_active = max_background = 0
    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not any(marker in line for marker in ("SCHED_CAP_LAUNCH", "SCHED_CAP_RELEASE", "SCHED_LAUNCH")):
            continue
        fields = dict(re.findall(r"(\w+)=([^\s]+)", line))
        rid = fields.get("clientid")
        if "SCHED_LAUNCH" in line and "SCHED_CAP_LAUNCH" not in line:
            if rid in ids:
                if rid in selection_wait:
                    flags.append(f"duplicate_schedule_launch:{rid}")
                selection_wait[rid] = int(fields["wait_ms"])
            continue
        key = (fields.get("id"), fields.get("slot"))
        value = {"request_id": rid, "task_id": fields.get("id"), "slot": fields.get("slot"),
                 "category": fields.get("class"), "event_ms": int(fields["event_ms"]), "line": line_no}
        if "SCHED_CAP_LAUNCH" in line:
            if key in active or key in launch:
                flags.append(f"duplicate_cap_launch:{key}")
            active[key] = value
            launch[key] = value
            max_active = max(max_active, len(active))
            max_background = max(max_background, sum(v["category"] == "background" for v in active.values()))
        else:
            if key not in active:
                flags.append(f"unmatched_cap_release:{key}")
            active.pop(key, None)
            release[key] = value
    durations = {v["request_id"]: release[key]["event_ms"] - v["event_ms"] for key, v in launch.items() if key in release and v["request_id"] in ids}
    if active:
        flags.append("final_active_slots_nonzero")
    return {"cap_launch_count_including_warmups": len(launch), "cap_release_count_including_warmups": len(release),
            "max_active_slots": max_active, "max_running_background": max_background,
            "final_active_slots": len(active), "flags": flags,
            "measurement_unique_launches": sum(v["request_id"] in ids for v in launch.values()),
            "measurement_server_queue_ms": distribution(list(selection_wait.values())),
            "queue_ms_by_request": selection_wait, "residence_ms_by_request": durations,
            "clock_note": "Server event_ms differences only; never subtract client timestamps from server timestamps."}


def gpu_samples(path):
    if not path.exists():
        return {"status": "missing"}
    values = {"memory_used_mib": [], "utilization_percent": [], "temperature_c": [], "power_w": []}
    bad = 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        for row in csv.reader(handle):
            if len(row) < 5 or "timestamp" in row[0].lower():
                continue
            for field, cell in zip(values, row[1:5]):
                match = re.search(r"[-+]?\d+(?:\.\d+)?", cell)
                if match:
                    values[field].append(float(match.group()))
                else:
                    bad += 1
    return {"status": "observed", "scope": "whole batch, including preparation, idle and cleanup; no client/server clock alignment assumed",
            "unparsed_cells": bad, **{name: distribution(samples) for name, samples in values.items()}}


def identify(directory, manifest):
    scenario = manifest.get("scenario")
    if scenario not in SCENARIOS:
        scenario = next((name for name in SCENARIOS if name in directory.name), None)
    repeat = manifest.get("repeat")
    if repeat is None:
        match = re.match(r"r(\d+)-", directory.name)
        repeat = int(match.group(1)) if match else None
    if scenario is None or repeat is None:
        raise ValueError(f"Cannot identify development run: {directory}")
    strategy = manifest.get("strategy", manifest.get("configuration", "prototype_original_order"))
    if strategy not in ("prototype_original_order", "prototype", "original"):
        raise ValueError(f"Unexpected strategy: {strategy}")
    return {"run_id": directory.name, "repeat": int(repeat), "scenario": scenario,
            "strategy": "prototype_original_order"}


def n(value, digits=3):
    if value is None:
        return "未知"
    if isinstance(value, float) and math.isinf(value):
        return "无穷"
    return f"{value:.{digits}f}"


def range_text(values, digits=3):
    vals = [v for v in values if v is not None]
    return "未知" if not vals else f"{n(min(vals), digits)}—{n(max(vals), digits)}"


def first_p95(run):
    value = run["metrics"]["classes"]["interactive"]["all_arrival_total_first"]["bounds"]["p95_ms"]
    return value["lower"] if value["lower"] == value["upper"] else None


def aggregate_by_scenario(runs):
    result = {}
    for scenario in SCENARIOS:
        group = [r for r in runs if r["scenario"] == scenario]
        result[scenario] = {"independent_development_runs": len(group),
            "run_ids": [r["run_id"] for r in group],
            "interactive_total_first_p95_ms": distribution([first_p95(r) for r in group]),
            "background_completed_p95_ms": distribution([r["metrics"]["classes"]["background"]["completed_only_completion_ms"]["p95"] for r in group]),
            "whole_output_rate_tokens_per_second": distribution([r["metrics"]["whole_output_rate_tokens_per_second"] for r in group]),
            "drain_seconds": distribution([r["metrics"]["client_drain_seconds"] for r in group]),
            "window_end_in_client_system": [r["metrics"]["at_window_end_in_client_system"] for r in group]}
    return result


def lengths_only(metrics):
    allowed = ("planned", "terminal_counts", "input_tokens", "expected_input_tokens", "new_prompt_tokens", "cached_prompt_tokens", "output_tokens", "limit_stopped", "eos_stopped", "other_or_unknown_stop", "input_token_buckets", "template_families")
    return {"classes": {name: {k: v for k, v in values.items() if k in allowed}
                        for name, values in metrics.get("classes", {}).items()}}


def report_text(summary):
    runs = summary["runs"]
    lines = ["# P1开发场景验证：实际输入、运行数据与下一步", "",
        f"采集批次：`{summary['batch_id']}`。分析日期：{summary['analyzed_at_local']}。", "",
        "这次先验证新提示和新场景能否可靠运行，记录聊天等待、后台进度与积压。所有轮次只运行现有引擎的原始选择路径。在线余量M、固定余量B3和正式硬容量账本尚未实现，本次没有比较这些方法。G1仍待审。", "",
        f"本次纳入{len(runs)}轮开发运行、{summary['measurement_requests']}条开发请求；另有{summary['calibration']['requests']}条串行校准、{summary['warmups']['requests']}条预热，分别统计。三次开发重复只能描述波动，不能证明最终样本够用或长期稳定。", "",
        "## 1. 按轮看实际结果", "",
        "聊天等待从计划到达算到首段非空回答。P95可以理解为约95%的请求不超过的时间。后台完成时间也从计划到达算起。", "",
        "|重复|场景|发送后首响应P50/P95（毫秒）|计划到首响应P50/P95（毫秒）|后台完成P95（秒，完成者）|全程输出token/秒|100秒末仍未结束|排空（秒）|", "|---|---|---:|---:|---:|---:|---:|---:|"]
    names = {"main_bursty_mixed": "突发后台", "boundary_steady": "平缓后台", "boundary_sparse": "稀疏后台"}
    for run in runs:
        m = run["metrics"]
        first = m["classes"]["interactive"]["all_arrival_total_first"]["bounds"]
        fmt = lambda x: n(x["lower"]) if x["lower"] == x["upper"] else f"[{n(x['lower'])}, {n(x['upper'])}]"
        ttft = m["classes"]["interactive"]["observed_ttft_ms"]
        lines.append(f"|{run['repeat']}|{names[run['scenario']]}|{n(ttft['p50'])} / {n(ttft['p95'])}|{fmt(first['p50_ms'])} / {fmt(first['p95_ms'])}|{n(m['classes']['background']['completed_only_completion_ms']['p95']/1000) if m['classes']['background']['completed_only_completion_ms']['p95'] is not None else '未知'}|{n(m['whole_output_rate_tokens_per_second'])}|{m['at_window_end_in_client_system']}|{n(m['client_drain_seconds'])}|")
    lines += ["", "这些行各自是一轮，每轮内部请求共享队列，不能把全部开发请求当成同样多次独立重复。发送后首响应一列只描述已观测到首内容的请求，覆盖数另列于summary.json；计划至首响应一列使用全部到达请求及事先规定的失败、删失规则。若出现失败或缺失，完成者后台延迟只能作辅助。", "",
        "## 2. 同类请求实际有多长", "",
        "输入按模型实际编码计算，包含ChatML模板和特殊token；输出上限不等于实际输出。下表只合并长度计数，不把合并请求用于显著性检验。", "",
        "|用途|类别|数量|输入min/P50/P95/max|输出min/P50/P95/max|输出已知/缺失|触顶 / EOS / 其他或未知|", "|---|---|---:|---:|---:|---:|---:|"]
    for purpose in ("calibration", "development"):
        group = summary["calibration"]["metrics"] if purpose == "calibration" else summary["development_length_summary"]
        for category, values in group.get("classes", {}).items():
            def four(d):
                return " / ".join(n(d[k], 1) for k in ("min", "p50", "p95", "max"))
            lines.append(f"|{'校准' if purpose=='calibration' else '开发'}|{'聊天' if category=='interactive' else '后台'}|{values['planned']}|{four(values['input_tokens'])}|{four(values['output_tokens'])}|{values['output_tokens']['known']} / {values['output_tokens']['missing']}|{values['limit_stopped']} / {values['eos_stopped']} / {values['other_or_unknown_stop']}|")
    lines += ["", "长度表按实际执行次数计数，同一提示在不同场景重放会再次计入；它只描述本批输入与执行量。生成器选择首个进入预设桶的候选，长度可能集中在桶下缘，不能称作均匀覆盖整个范围。若多数输出达到上限，说明这批任务主要检验了有输出上限的执行需求，不能称作自然输出长度分布已得到充分覆盖。24条校准只作本轮可行性检查，不足以训练或冻结正式估计器。", "",
        "## 3. 完整性和计时核对", "",
        f"开发终态合计：{summary['measurement_terminal_counts']}。输入响应与冻结分词长度一致：{summary['verified_input_requests']} / {summary['measurement_requests']}。最大迟发{n(summary['maximum_send_lag_ms'])}毫秒；超过50毫秒：{summary['late_send_count']}条。", "",
        "全程输出速率的分母为max(100秒，最后测量请求终态秒数)，不包括后续写盘与服务恢复。任何请求缺少最终token计数时，全体速率记为未知，并单列可核对的下界。100秒窗口输出速率没有逐token时间来源，保持未知。", "",
        "每10秒的到达、完成、终态和剩余请求数保存在summary.json的checkpoints。这里的剩余是客户端尚未结束请求，不能直接称为服务器队列。服务端排队时长和占用仅使用同一服务进程的事件时钟计算；客户端结束不充当服务端释放时间。", "",
        "## 4. 怎样理解场景差异", "",
        "突发与平缓场景在同一重复中使用同样内容和输出上限，只改变后台到达时刻，按重复列出差值。稀疏场景还减少了后台数量和总工作量，不能把差异单独归因于长度或突发形状。执行顺序按三种轮换平衡位置，但只有三次重复，时间漂移、温度和宿主竞争仍限制解释。", ""]
    for scene, values in summary["scenario_ranges"].items():
        lines.append(f"- {names[scene]}：聊天P95范围{n(values['interactive_total_first_p95_ms']['min'])}—{n(values['interactive_total_first_p95_ms']['max'])}毫秒；排空范围{n(values['drain_seconds']['min'])}—{n(values['drain_seconds']['max'])}秒。")
    lines += ["", "本批不据某个场景更容易让算法获胜来筛选场景，也不据这三次原型运行宣布聊天SLO或复杂方法收益成立。后续仍需完成共同容量机制、可解释的软准入介入状态，以及M/B3公平对照。", "",
        "## 5. 版本、限制与可复算材料", "",
        f"分析脚本SHA256：`{summary['analysis_code_sha256']}`。原始数据目录：`{summary['raw_directory']}`。", "",
        "- [summary.json](summary.json)：逐轮重算指标、长度、终态、10秒进度、服务器事件、GPU样本和场景差值。",
        "- [request-metrics.jsonl](request-metrics.jsonl)：开发请求的精简逐条指标，不混入校准和预热。",
        "- [input-fingerprints.json](input-fingerprints.json)：本次实际读取的原始文件哈希。",
        "- [report-fingerprints.json](report-fingerprints.json)：本次派生报告哈希。", "",
        f"分析完整性标记：`{summary['status']}`。待核对事项：{summary['flags'] if summary['flags'] else '无本脚本发现的结构或计数异常；这不等于G1通过、长期稳定或算法验证完成。'}", ""]
    return "\n".join(lines)


def analyze(run_dir, out_dir, allow_partial=False):
    run_dir = run_dir.resolve(strict=True)
    if out_dir.exists():
        raise FileExistsError("Choose a new report directory; old evidence is immutable")
    tracked = set()

    def tracked_read(path):
        tracked.add(path)
        return read(path)

    def tracked_lines(path):
        tracked.add(path)
        return jsonlines(path)

    batch_status = tracked_read(run_dir / "batch-status.json")
    batch_summary = tracked_read(run_dir / "batch-summary.json")
    flags = []
    if batch_status.get("status") != "completed":
        flags.append("batch_not_completed")
    entries = batch_summary if isinstance(batch_summary, list) else batch_summary.get("runs", [])
    by_directory = {str(item["directory"]): item for item in entries}
    dirs = sorted((p for p in run_dir.glob("r*-*-prototype") if p.is_dir()), key=lambda p: p.name)
    if not dirs:
        dirs = sorted({run_dir / d for d in by_directory})
    if len(dirs) != 9:
        flags.append(f"development_run_count:{len(dirs)}_expected_9")
    if set(by_directory) != {d.name for d in dirs}:
        flags.append("batch_summary_run_directories_do_not_match_raw")
    if flags and not allow_partial:
        raise ValueError(f"Incomplete batch: {flags}; preserve evidence and use --allow-partial only for an explicitly partial report")
    runs, all_rows, pairing = [], [], {}
    for directory in dirs:
        manifest = tracked_read(directory / "manifest.json")
        ident = identify(directory, manifest)
        if manifest.get("status") != "completed" or manifest.get("server_exit_code") != 0:
            flags.append(f"manifest_not_completed_or_server_exit_nonzero:{directory.name}")
        raw_rows = tracked_lines(directory / "replay/client.jsonl")
        trace = tracked_lines(directory / "replay/trace.jsonl")
        ids = [r["req_id"] for r in raw_rows]
        assert len(ids) == len(set(ids)) and set(ids) == {r["req_id"] for r in trace}, "Trace and terminal IDs do not close"
        trace_map = {r["req_id"]: r for r in trace}
        for row in raw_rows:
            for key, value in trace_map[row["req_id"]].items():
                assert row.get(key) == value, f"Raw row differs from the frozen trace: {key}"
            assert hashlib.sha256(row["prompt"].encode("utf-8")).hexdigest() == row["content_sha256"], "Prompt digest differs"
            assert row["split"] == "development" and row["seed"] == 21000 + ident["repeat"]
            assert row["input_token_range"][0] <= row["input_tokens"] <= row["input_token_range"][1]
            assert row["input_tokens"] + row["max_tokens"] + 1 <= 2048
            k = row["category_index"]
            expected_arrival = 500 * k if row["category"] == "interactive" else (10000 * (k // 3) + 250 + 50 * (k % 3) if ident["scenario"] == "main_bursty_mixed" else 250 + 10000 * k / 3)
            # Sparse uses indices 0,3,...27, so the latter expression is exact.
            assert abs(row["arrival_offset_ms"] - expected_arrival) < .001, "Arrival schedule differs from the registered formula"
            per_request = directory / "replay/requests" / f"{row['req_id']}.json"
            if per_request.exists():
                assert tracked_read(per_request) == row, "Per-request and jsonl records differ"
            else:
                flags.append(f"missing_individual_record:{directory.name}:{row['req_id']}")
        rows = [normalized_record(row, ident["run_id"]) for row in raw_rows]
        if any(r["output_tokens"] is not None and r["output_tokens"] > r["max_tokens"] for r in rows):
            flags.append(f"output_budget_exceeded:{directory.name}")
        metrics = summarize_records(rows, 100)
        if metrics["input_verified"] != len(rows):
            flags.append(f"input_length_unverified:{directory.name}")
        if metrics["late_send_over_50ms"]:
            flags.append(f"late_send:{directory.name}")
        expected = 210 if ident["scenario"] == "boundary_sparse" else 230
        if len(rows) != expected:
            flags.append(f"request_count:{directory.name}:{len(rows)}_expected_{expected}")
        server_path = directory / "server.log"
        tracked.add(server_path)
        events = server_events(server_path, set(ids))
        if events["flags"] or events["max_active_slots"] > 2:
            flags.append(f"server_capacity_or_release:{directory.name}")
        if any(r["terminal"] == "completed" and r["req_id"] not in events["queue_ms_by_request"] for r in rows):
            flags.append(f"completed_without_server_launch:{directory.name}")
        for row in rows:
            row["server_queue_ms"] = events["queue_ms_by_request"].get(row["req_id"])
            row["server_slot_residence_ms"] = events["residence_ms_by_request"].get(row["req_id"])
        events["classes"] = {c: {"queue_ms": distribution([r["server_queue_ms"] for r in rows if r["category"] == c]),
                                  "slot_residence_ms": distribution([r["server_slot_residence_ms"] for r in rows if r["category"] == c])} for c in CLASSES}
        checks = directory / "audit.json"
        audit = tracked_read(checks) if checks.exists() else None
        if audit and audit.get("flags"):
            flags.append(f"collector_audit_flags:{directory.name}")
        runs.append({**ident, "manifest_status": manifest.get("status"), "metrics": metrics,
                     "checkpoints": checkpoints(rows, 100), "server_events": events,
                     "collector_audit": audit,
                     "server_command": tracked_read(directory / "server-command.json") if (directory / "server-command.json").exists() else manifest.get("server_command"),
                     "policy_environment": tracked_read(directory / "server-policy-environment.json") if (directory / "server-policy-environment.json").exists() else manifest.get("policy_environment")})
        all_rows.extend(rows)
        pairing[(ident["repeat"], ident["scenario"])] = {r["req_id"]: r for r in trace}
    # Verify content sharing independently of arrival metadata and scenario labels.
    paired_differences = []
    for repeat in (1, 2, 3):
        main = pairing.get((repeat, "main_bursty_mixed"))
        steady = pairing.get((repeat, "boundary_steady"))
        sparse = pairing.get((repeat, "boundary_sparse"))
        if not all((main, steady, sparse)):
            flags.append(f"incomplete_paired_scenarios:r{repeat}")
            continue
        strip = lambda value: {k: v for k, v in value.items() if k not in ("arrival_offset_ms", "scenario", "run_id")}
        assert set(main) == set(steady)
        assert all(strip(main[k]) == strip(steady[k]) for k in main), "Paired main/steady content differs"
        assert set(sparse) <= set(main) and all(strip(sparse[k]) == strip(main[k]) for k in sparse)
        assert sorted(r["category_index"] for r in sparse.values() if r["category"] == "background") == list(range(0, 30, 3))
        current = {r["scenario"]: r for r in runs if r["repeat"] == repeat}
        for comparator in ("boundary_steady", "boundary_sparse"):
            one, two = current["main_bursty_mixed"], current[comparator]
            a, b = first_p95(one), first_p95(two)
            paired_differences.append({"repeat": repeat, "comparison": f"main_bursty_mixed_minus_{comparator}",
                "interactive_total_first_p95_difference_ms": a - b if a is not None and b is not None else None,
                "interpretation": "same content; arrival shape differs" if comparator == "boundary_steady" else "background request count and work budget also differ; not a pure arrival-shape effect"})
    calibration_files = sorted((run_dir / "prep/calibration").glob("c*/replay/client.jsonl"))
    calibration_rows = [normalized_record(row, str(path.parent.parent.relative_to(run_dir))) for path in calibration_files for row in tracked_lines(path)]
    warm_files = sorted(path for path in run_dir.rglob("client.jsonl") if "warmup" in path.parts)
    warm_rows = [normalized_record(row, str(path.parent.relative_to(run_dir))) for path in warm_files for row in tracked_lines(path)]
    if len(calibration_rows) != 24:
        flags.append(f"calibration_count:{len(calibration_rows)}_expected_24")
    if len(warm_rows) != 20:
        flags.append(f"warmup_count:{len(warm_rows)}_expected_20")
    if any(r["terminal"] != "completed" for r in calibration_rows + warm_rows):
        flags.append("noncompleted_calibration_or_warmup")
    if any(not r["input_length_verified"] for r in calibration_rows):
        flags.append("calibration_input_lengths_not_verified")
    cal_families = {r["template_family"] for r in calibration_rows}
    dev_families = {r["template_family"] for r in all_rows}
    if cal_families & dev_families:
        flags.append("calibration_development_template_overlap")
    if {r["content_sha256"] for r in calibration_rows} & {r["content_sha256"] for r in all_rows}:
        flags.append("calibration_development_content_overlap")
    for path in run_dir.glob("*.json"):
        if any(key in path.name for key in ("sha256", "fingerprint", "environment", "protocol", "config")):
            tracked.add(path)
    gpu_path = run_dir / "gpu-samples.csv"
    if gpu_path.exists():
        tracked.add(gpu_path)
    environment = tracked_read(run_dir / "environment.json") if (run_dir / "environment.json").exists() else None
    provenance_checks = {}
    for manifest_name, snapshot_name in (("source-sha256.json", "source"), ("engine-source-sha256.json", "engine-source")):
        path = run_dir / manifest_name
        expected = tracked_read(path)
        mismatches = []
        for relative, digest in expected.items():
            source = run_dir / snapshot_name / relative
            tracked.add(source)
            if not source.is_file() or sha(source) != digest:
                mismatches.append(relative)
        provenance_checks[manifest_name] = {"files": len(expected), "snapshot_mismatches": mismatches}
        if mismatches:
            flags.append(f"snapshot_sha_mismatch:{manifest_name}")
    before_path, after_path = run_dir / "engine-runtime-sha256.json", run_dir / "engine-runtime-sha256-after.json"
    if after_path.exists():
        runtime_same = tracked_read(before_path) == tracked_read(after_path)
        provenance_checks["runtime_before_after_identical"] = runtime_same
        if not runtime_same:
            flags.append("runtime_before_after_changed")
    else:
        provenance_checks["runtime_before_after_identical"] = None
        flags.append("runtime_after_manifest_missing")
    model_after_path = run_dir / "model-sha256-after.json"
    if model_after_path.exists() and environment:
        model_same = tracked_read(model_after_path).get("sha256") == environment.get("model_sha256")
        provenance_checks["model_before_after_identical"] = model_same
        if not model_same:
            flags.append("model_before_after_changed")
    else:
        provenance_checks["model_before_after_identical"] = None
        flags.append("model_after_manifest_missing")
    if batch_status.get("final_test_generated_or_read") is not False:
        flags.append("final_test_access_attestation_missing_or_not_false")
    terminal_counts = Counter(r["terminal"] for r in all_rows)
    result = {"schema_version": 1, "status": "passed_recomputation" if not flags else "needs_review",
              "batch_id": run_dir.name, "raw_directory": str(run_dir),
              "analyzed_at_local": datetime.now(timezone(timedelta(hours=8))).isoformat(),
              "analysis_code": str(Path(__file__).resolve()), "analysis_code_sha256": sha(Path(__file__)),
              "quantile_method": "type7 linear interpolation; infinity propagation explicit",
              "scope": "P1 development feasibility only; no M/B3 comparison; no formal hard reservation ledger; 3 independent blocks per scenario; not final evidence or long-term stability",
              "configuration": "prototype_original_order", "final_test_accessed": False,
              "measurement_requests": len(all_rows), "measurement_terminal_counts": {s: terminal_counts[s] for s in STATES},
              "verified_input_requests": sum(r["input_length_verified"] for r in all_rows),
              "maximum_send_lag_ms": max((r["send_lag_ms"] for r in all_rows if r["send_lag_ms"] is not None), default=None),
              "late_send_count": sum(r["send_lag_ms"] is not None and r["send_lag_ms"] > 50 for r in all_rows),
              "calibration": {"requests": len(calibration_rows), "metrics": lengths_only(summarize_records(calibration_rows, 0)) if calibration_rows else {}, "serial_execution_not_a_pooled_performance_sample": True},
              "warmups": {"requests": len(warm_rows), "terminal_counts": dict(Counter(r["terminal"] for r in warm_rows)), "not_in_performance_or_feedback": True},
              "development_length_summary": lengths_only(summarize_records(all_rows, 100)) if all_rows else {},
              "runs": runs, "scenario_ranges": aggregate_by_scenario(runs),
              "paired_scenario_differences": paired_differences,
              "gpu_samples": gpu_samples(gpu_path), "environment": environment, "provenance_checks": provenance_checks,
              "source_runtime_model_manifest_files": sorted(str(p.relative_to(run_dir)) for p in tracked if any(key in p.name for key in ("sha256", "fingerprint"))),
              "batch_status": batch_status, "flags": flags}
    out_dir.mkdir(parents=True, exist_ok=False)
    dump(out_dir / "summary.json", result)
    dump(out_dir / "input-fingerprints.json", {str(path.relative_to(run_dir)): sha(path) for path in sorted(tracked)})
    with (out_dir / "request-metrics.jsonl").open("x", encoding="utf-8") as handle:
        for row in all_rows:
            handle.write(json.dumps(safe(row), ensure_ascii=False, allow_nan=False) + "\n")
    with (out_dir / "README.md").open("x", encoding="utf-8") as handle:
        handle.write(report_text(result))
    dump(out_dir / "report-fingerprints.json", {p.name: sha(p) for p in sorted(out_dir.iterdir()) if p.is_file()})
    return result


def self_test():
    assert quantile([1, 2, 3], .95) == 2.9
    assert quantile([1, math.inf], 0) == 1
    assert quantile([1, math.inf], .95) == math.inf
    observed = {"arrival_offset_ms": 0, "end_offset_ms": 100, "first_content_offset_ms": 10, "terminal": "failed"}
    assert outcome_bounds([observed], "first")["bounds"]["p95_ms"] == {"lower": 10, "upper": 10}
    assert outcome_bounds([observed], "completion")["bounds"]["p95_ms"]["lower"] == math.inf
    censored = dict(observed, first_content_offset_ms=None, terminal="timeout")
    assert outcome_bounds([censored], "first")["bounds"]["p95_ms"] == {"lower": 100, "upper": math.inf}
    fake = dict(observed, category="interactive", actual_send_offset_ms=0, output_tokens=7, output_tokens_known=True,
                send_lag_ms=0, input_length_verified=True, input_tokens=32, expected_input_tokens=32,
                new_prompt_tokens=32, cached_prompt_tokens=0, max_tokens=64, stopped_limit=False, stopped_eos=True,
                ttft_ms=10, total_first_ms=10, completion_from_planned_ms=100, input_token_bucket=0, template_family="test", terminal="completed")
    result = summarize_records([fake], 100)
    assert result["rate_denominator_seconds"] == 100 and result["whole_output_rate_tokens_per_second"] == .07
    fake["output_tokens"], fake["output_tokens_known"] = None, False
    result = summarize_records([fake], 100)
    assert result["whole_output_rate_tokens_per_second"] is None and result["output_tokens_missing_requests"] == 1
    return {"status": "passed", "checks": 8, "note": "model-free quantile, censoring, partial output and denominator checks only"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test()))
        return
    if not args.run_dir or not args.out_dir:
        parser.error("--run-dir and --out-dir are required")
    result = analyze(args.run_dir, args.out_dir, args.allow_partial)
    print(json.dumps({"status": result["status"], "runs": len(result["runs"]), "requests": result["measurement_requests"], "flags": result["flags"], "out_dir": str(args.out_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
