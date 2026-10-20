#!/usr/bin/env python3
"""Verify a sanity rerun and produce a readable Markdown evidence report."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def lines(path):
    return [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines() if s.strip()]


def verify(run):
    http = run / "http"
    trace = lines(http / "trace.jsonl")
    rows = lines(http / "client.jsonl")
    dry = lines(run / "dry-run" / "dispatch.jsonl")
    paired = lines(run / "paired-e0-trace.jsonl")
    assert len(trace) == len(rows) == len(dry) == 10
    assert len({r["req_id"] for r in trace}) == 10
    assert {r["req_id"] for r in trace} == {r["req_id"] for r in rows} == {r["req_id"] for r in dry}
    assert len({r["req_id"] for r in rows}) == len({r["req_id"] for r in dry}) == 10
    assert paired == [r for r in trace if r["category"] == "interactive"]
    assert Counter(r["category"] for r in rows) == {"interactive": 6, "background": 4}
    assert load(http / "replay-meta.json")["trace_sha256"] == hashlib.sha256((http / "trace.jsonl").read_bytes()).hexdigest()
    for r in dry:
        assert r["simulation"]
        assert r["planned_offset_ms"] <= r["dispatch_offset_ms"] <= r["end_offset_ms"]
    dry_by_id = {r["req_id"]: r for r in dry}
    assert dry_by_id["I001"]["dispatch_offset_ms"] < dry_by_id["I000"]["end_offset_ms"]
    for i in range(2):
        r = load(http / f"warmup-{i}.json")
        assert r["error"] is None and r["http_status"] == 200 and r["client"]["stream_done"]
        assert r["server"]["finish_reason"] in ("stop", "length")
    trace_by_id = {r["req_id"]: r for r in trace}
    events = []
    assert len(list((http / "requests").glob("*.json"))) == 10
    for r in rows:
        c, t = r["client"], r["replay"]
        source = trace_by_id[r["req_id"]]
        assert r["http_status"] == 200 and r["error"] is None and c["stream_done"]
        assert r["server"]["finish_reason"] in ("stop", "length")
        assert r["prompt"] == source["prompt"] and r["max_tokens"] == source["max_tokens"]
        assert r["category"] == source["category"] and r["temperature"] == source["temperature"]
        assert t["planned_offset_ms"] == source["arrival_offset_ms"]
        assert t["planned_offset_ms"] <= t["release_offset_ms"] <= t["actual_send_offset_ms"] <= t["first_content_offset_ms"] <= t["end_offset_ms"]
        assert abs(t["first_content_offset_ms"] - t["actual_send_offset_ms"] - c["ttft_ms"]) < .001
        assert abs(t["end_offset_ms"] - t["actual_send_offset_ms"] - c["total_ms"]) < .001
        assert abs(t["send_lag_ms"] - t["actual_send_offset_ms"] + t["release_offset_ms"]) < .001
        assert load(http / "requests" / f'{r["req_id"]}.json') == r
        events.extend([(t["actual_send_offset_ms"], 1), (t["end_offset_ms"], -1)])
    active = peak = 0
    for _, change in sorted(events):
        active += change
        peak = max(peak, active)
    assert active == 0 and peak >= 2
    summary = {
        "status": "sanity_passed",
        "request_count": len(rows),
        "class_counts": dict(Counter(r["category"] for r in rows)),
        "peak_client_inflight": peak,
        "max_send_lag_ms": max(r["replay"]["send_lag_ms"] for r in rows),
        "formal_performance_result": False,
    }
    (run / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    manifest = load(root / "configs" / "run-manifest.template.json")
    environment = (run / "environment.txt").read_text(encoding="utf-8").splitlines()
    manifest.update({
        "status": "sanity_passed", "run_id": run.name, "scenario": "mixed_10_request_sanity",
        "strategy": "original_engine", "recorded_at": environment[0],
        "project_commit": environment[4], "engine_commit": environment[2],
        "model_sha256": environment[3].split()[0], "environment_record": "environment.txt",
        "server_command": (http / "server-command.txt").read_text(encoding="utf-8").strip(),
        "client_command": "python3 scripts/replay/40_replay_sanity.py --trace data/workloads/prestudy-sanity-e1.jsonl --out-dir <run>/http",
        "workload_path": "http/trace.jsonl", "workload_sha256": load(http / "replay-meta.json")["trace_sha256"],
        "clock_domain": "client time.monotonic", "time_unit": "ms",
        "sampling_config": {"temperature": 0, "stream": True},
        "engine_config": {"context": 4096, "slots": 2, "gpu_layers": 99, "batch": 2048, "ubatch": 512},
        "cache_config": "Engine defaults; see http/props.json and http/server.log",
        "execution_order": ["dry_run", "server_restart", "warmup_0", "warmup_1", "mixed_sanity"],
        "denominator_rule": "All 10 planned requests were sent and completed; warmups excluded",
        "client_log": "http/client.jsonl", "server_log": "http/server.log",
        "notes": "Synthetic sanity only, not formal E0/E1. Null fields were not measured or frozen in this run. Warmup count=2. The 180s process timeout is not a unified drain deadline. Working tree is uncommitted; code hashes are in code-sha256.txt. Model source/license/build are referenced in docs/environment.md, not revalidated in this run.",
    })
    (run / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = ["# 小轨迹重跑核对报告", "", "**结果：通过。** 本轮包含调度干跑与真实流式请求核对，不构成正式 E0/E1 性能结论。", "",
              "| 核对项 | 结果 |", "|---|---|", "| 测量请求 | 10 条：6 交互、4 后台 |", "| 预热 | 2 条，独立保存、不计入样本 |",
              "| 请求 ID / 文件 / 汇总 | 唯一、完整、一致 |", "| E0/E1 交互轨迹 | 完全一致；本轮仅执行 E1 小轨迹 |",
              "| 计时与 TTFT 对账 | 通过 |", f"| 最大客户端在途数 | {peak} |",
              f'| 最大发送滞后 | {max(r["replay"]["send_lag_ms"] for r in rows):.3f} ms |',
              "", "## 逐请求结果", "", "| ID | 类别 | 计划到达 ms | 实际发送 ms | 滞后 ms | TTFT ms | 总时长 ms | 输出 token | 结束原因 |",
              "|---|---|---:|---:|---:|---:|---:|---:|---|"]
    for r in sorted(rows, key=lambda x: x["replay"]["planned_offset_ms"]):
        t, c = r["replay"], r["client"]
        tokens = (r["server"]["timings"] or {}).get("predicted_n", "未知")
        report.append(f'| {r["req_id"]} | {"交互" if r["category"] == "interactive" else "后台"} | {t["planned_offset_ms"]:.0f} | {t["actual_send_offset_ms"]:.3f} | {t["send_lag_ms"]:.3f} | {c["ttft_ms"]:.3f} | {c["total_ms"]:.3f} | {tokens} | {r["server"]["finish_reason"]} |')
    report += ["", "## 证据导航", "", "- [运行环境](environment.txt)、[代码摘要](code-sha256.txt)、[运行结束状态](run-status.txt)",
               "- [干跑记录](dry-run/dispatch.jsonl)、[真实请求汇总](http/client.jsonl)、[独立请求文件](http/requests/)",
               "- [启动命令](http/server-command.txt)、[服务端日志](http/server.log)、[指标快照](http/metrics-after.txt)",
               "", "## 解释范围", "", "后台 length 表示达到输出上限，属于本轮允许的正常结束。客户端在途数不等于 GPU 同时执行请求数。",
               "", "尚未验收异常终态分类与统一排空截止；进程级超时只是保护措施。server_overhead_ms 包含排队等时间，不能解释为纯交付开销。",
               "", "本轮使用相同的合成轨迹和引擎配置，新结果独立保存。清理记录、日期归档与当前进度见 daily/ 对应日期的记录及实验结果索引；本文只记录本轮核对结果。", ""]
    (run / "README.md").write_text("\n".join(report), encoding="utf-8")
    print(f"PASS: 10 real requests, peak_inflight={peak}; report: {run / 'README.md'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path, required=True)
    verify(ap.parse_args().run_dir)
