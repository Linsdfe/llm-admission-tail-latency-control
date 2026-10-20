#!/usr/bin/env python3
"""Audit paired prestudy evidence and regenerate compact, publishable results."""
import argparse
from collections import Counter
import csv
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/replay"))
from open_loop import dump, percentile, STATES


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def lines(path):
    return [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines() if s.strip()]


def analyze(batch, dest, additional_step7=None):
    batch = batch.resolve()
    assert read(batch / "batch-status.json")["status"] == "completed"
    cfg = read(batch / "protocol.json")
    environment = read(batch / "environment.json")
    extra_summary = None
    if additional_step7 is not None:
        extra_summary = read(additional_step7 / "summary.json")
        assert extra_summary["status"] == "passed"
        assert extra_summary["total_timeout_with_continuous_content"]
        assert extra_summary["race_terminal_counts"]["completed"] > 0
        assert extra_summary["race_terminal_counts"]["cancelled"] > 0
    assert cfg["repeats"] == 3
    for rel, digest in read(batch / "code-sha256.json").items():
        assert hashlib.sha256((batch / "source" / rel).read_bytes()).hexdigest() == digest
    assert read(batch / "engine-lifecycle/summary.json")["status"] == "passed"
    lifecycle_metrics = (batch / "engine-lifecycle/metrics-after.txt").read_text(encoding="utf-8")
    for name in ("llamacpp:requests_processing", "llamacpp:requests_deferred"):
        matches = [line.split()[1] for line in lifecycle_metrics.splitlines() if line.startswith(name + " ")]
        assert matches and all(float(value) == 0 for value in matches), name
    totals, pairs, tables = Counter(), [], []
    trace_digests = {}
    flags = []
    all_hashes = {}
    for p in sorted(batch.rglob("*")):
        if p.is_file():
            all_hashes[p.relative_to(batch).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    for repeat in range(1, 4):
        summaries, inter_traces = {}, {}
        for scenario in ("E0", "E1"):
            directory = batch / f"r{repeat}-{scenario}"
            manifest = read(directory / "manifest.json")
            assert manifest["status"] == "completed" and manifest["repeat_index"] == repeat
            assert manifest["scenario"] == scenario and manifest["seed"] == cfg["seed"]
            assert manifest["engine_commit"] == environment["engine_commit"]
            assert manifest["model_sha256"] == environment["model_sha256"]
            assert manifest["execution_order"] == cfg["execution_order"][repeat - 1]
            rows, trace = lines(directory / "replay/client.jsonl"), lines(directory / "replay/trace.jsonl")
            summary = read(directory / "replay/summary.json")
            n = 200 if scenario == "E0" else 300
            assert len(rows) == len(trace) == summary["planned"] == n
            assert len({r["req_id"] for r in rows}) == n
            assert {r["req_id"] for r in rows} == {r["req_id"] for r in trace}
            assert len(list((directory / "replay/requests").glob("*.json"))) == n
            assert sum(summary["terminal_counts"].values()) == n
            assert read(directory / "warmup/summary.json")["terminal_counts"]["completed"] == 2
            server_log = (directory / "server.log").read_text(encoding="utf-8")
            assert "n_slots = 2, n_ctx_slot = 2048" in server_log
            for error in ("Context size has been exceeded", "failed to allocate buffer", "truncated = 1"):
                if error in server_log:
                    flags.append(f"r{repeat}-{scenario}: engine capacity/truncation warning: {error}")
            sources = {r["req_id"]: r for r in trace}
            for r in rows:
                assert read(directory / "replay/requests" / f'{r["req_id"]}.json') == r
                source = sources[r["req_id"]]
                for key in ("prompt", "max_tokens", "category", "arrival_offset_ms"):
                    assert source[key] == r[key]
                assert r["terminal"] in STATES
                if r["actual_send_offset_ms"] is not None:
                    assert r["actual_send_offset_ms"] >= r["arrival_offset_ms"]
                if r["terminal"] == "completed":
                    assert r["stream_done"] and r["finish_reason"] in ("stop", "length")
                    assert r["http_status"] == 200 and r["error"] is None
                    assert r["actual_send_offset_ms"] <= r["first_content_offset_ms"] <= r["end_offset_ms"]
                    assert abs(r["first_content_offset_ms"] - r["actual_send_offset_ms"] - r["ttft_ms"]) < .001
                    assert sum(c["chars"] for c in r["chunk_series"]) == len(r["content"])
                    assert r["chunk_series"][0]["offset_ms"] == r["first_content_offset_ms"]
                    assert [c["offset_ms"] for c in r["chunk_series"]] == sorted(c["offset_ms"] for c in r["chunk_series"])
                    predicted = (r["timings"] or {}).get("predicted_n")
                    assert predicted is None or 0 <= predicted <= r["max_tokens"]
                    timing = r["timings"] or {}
                    if timing.get("prompt_n") is not None and timing.get("cache_n") is not None:
                        assert timing["prompt_n"] + timing["cache_n"] + r["max_tokens"] <= 2048
            if summary["late_send_count"]:
                flags.append(f"r{repeat}-{scenario}: {summary['late_send_count']} sends exceeded frozen 50ms limit")
            if summary["sent"] != n or summary["actual_send_window_denominator"] != n:
                flags.append(f"r{repeat}-{scenario}: sent/window denominator does not equal planned count")
            if summary["terminal_counts"]["completed"] != n:
                flags.append(f"r{repeat}-{scenario}: incomplete/non-successful requests; report all states")
            for name, stats in summary["classes"].items():
                if stats["output_length_known"] != stats["completed"]:
                    flags.append(f"r{repeat}-{scenario}: missing actual output lengths for {name}")
            inter = [r for r in rows if r["category"] == "interactive" and r["terminal"] == "completed" and r["ttft_ms"] is not None]
            assert len(inter) == summary["classes"]["interactive"]["ttft_samples"]
            assert abs(percentile([r["ttft_ms"] for r in inter], 95) - summary["classes"]["interactive"]["ttft_p95_ms"]) < .001
            totals.update(summary["terminal_counts"])
            summaries[scenario] = summary
            inter_traces[scenario] = [r for r in trace if r["category"] == "interactive"]
            trace_digests[f"r{repeat}-{scenario}"] = hashlib.sha256((directory / "replay/trace.jsonl").read_bytes()).hexdigest()
            assert trace_digests[f"r{repeat}-{scenario}"] == manifest["workload_sha256"]
            elapsed = summary["last_end_seconds"]
            out_tokens = sum(v["output_tokens"] for v in summary["classes"].values())
            tables.append({"repeat": repeat, "scenario": scenario, "requests": n,
                           "completed": summary["terminal_counts"]["completed"],
                           "interactive_p50_ms": summary["classes"]["interactive"]["ttft_p50_ms"],
                           "interactive_p95_ms": summary["classes"]["interactive"]["ttft_p95_ms"],
                           "max_send_lag_ms": summary["send_lag_max_ms"], "late_send_count": summary["late_send_count"],
                           "completion_rps_window": summary["window_completion_throughput_rps"],
                           "output_tps_whole_run_including_drain": out_tokens / elapsed,
                           "run_seconds": elapsed, "drain_seconds": summary["drain_elapsed_seconds"],
                           "background_completion_p95_ms": summary["classes"]["background"]["completion_from_planned_p95_ms"],
                           "terminal_counts": summary["terminal_counts"], "classes": summary["classes"]})
        assert inter_traces["E0"] == inter_traces["E1"]
        p0 = summaries["E0"]["classes"]["interactive"]["ttft_p95_ms"]
        p1 = summaries["E1"]["classes"]["interactive"]["ttft_p95_ms"]
        pairs.append({"repeat": repeat, "e0_p95_ms": p0, "e1_p95_ms": p1, "delta_ms": p1 - p0})
    assert len({trace_digests[f"r{i}-E0"] for i in range(1, 4)}) == 1
    assert len({trace_digests[f"r{i}-E1"] for i in range(1, 4)}) == 1
    gpu = {"memory_mib": [], "utilization_percent": [], "temperature_c": [], "power_w": []}
    with (batch / "gpu-samples.csv").open(encoding="utf-8") as f:
        for record in csv.reader(f):
            if len(record) == 5 and not record[0].startswith("timestamp"):
                for name, value in zip(gpu, record[1:]):
                    try:
                        gpu[name].append(float(value.strip().split()[0]))
                    except (ValueError, IndexError):
                        pass
    telemetry = {name: {"min": min(values), "max": max(values), "samples": len(values)} for name, values in gpu.items() if values}
    result = {"batch": batch.name, "scope": "synthetic exploratory prestudy; three repeats; not final significance evidence",
              "audit_status": "passed" if not flags else "needs_review", "flags": flags,
              "total_requests": sum(totals.values()), "terminal_counts": dict(totals), "pairs": pairs,
              "runs": tables, "trace_sha256": trace_digests, "protocol": cfg, "gpu_whole_batch": telemetry,
              "analysis_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if extra_summary is not None:
        result["additional_step7"] = {"run": additional_step7.name, "summary": extra_summary}
    dest.mkdir(parents=True, exist_ok=False)
    dump(dest / "summary.json", result)
    dump(dest / "evidence-sha256.json", all_hashes)
    with (dest / "paired-summary.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(pairs[0]))
        writer.writeheader()
        writer.writerows(pairs)
    link = Path(os.path.relpath(batch, dest)).as_posix()
    report = ["# P0 第七、八步：异常终态与配对预实验结果", "",
              f"运行批次：`{batch.name}`。审计结果：**{result['audit_status']}**。完整原始文件位于[本地运行目录](" + link + ")，默认不提交 Git。", "",
              "## 第七步验收范围", "", "受控 HTTP/SSE 测试覆盖正常完成、429/503 拒绝、400/断流/缺少 DONE/非法 JSON 失败、请求总超时、主动取消、统一截止在途未完成及尚未释放请求。20 条完成/取消竞争与 200 条每秒 20 请求校准通过，门槛预先固定为 50 ms。", "",
              "真实引擎探针覆盖取消、超时、在途截止、尚未释放，并在同一服务上完成 2 条恢复请求；这支持客户端生命周期与服务恢复核对，不代表自研 KV 账本或内部回收的工程验收已完成。", "",
              "首次每秒 500 请求的过载校准存在客户端迟发，作为失败证据保留；调整的是校准负载，50 ms 门槛未放宽，且均发生在 E0/E1 数据采集之前。", "",
              "## 第八步协议", "", "三组配对，每组 E0=200 交互、E1=相同 200 交互 + 100 后台，共 1500 条测量请求。每轮全新服务、2 条预热（共 12 条，不计样本），顺序为 E0/E1、E1/E0、E0/E1。交互间隔 500 ms、后台间隔 1000 ms；窗口 100 s，之后最多排空 180 s，请求总超时 180 s。", "",
              "相同合成轨迹、temperature=0、默认前缀缓存；每轮记录 cache_n/prompt_n 与实际输出。业务类别不由实际长度反推。协议、版本、模型摘要、源码快照、启动参数、GPU 采样、原始流式块与全部终态均保存。没有按结果挑样或删失败运行。", "",
              "计时边界为 TCP 建连后、写 HTTP 前至首个非空生成内容；建连时间计入发送滞后。两场景边界相同，不与旧 urllib 冒烟直接作严格性能对照；客户端相对单调时钟不与 GPU/服务端墙钟相减。", "",
              "## 逐轮结果", "", "| 重复 | 场景 | 完成/计划 | 交互 P50 ms | 交互 P95 ms | 最大发送滞后 ms | 窗口完成 req/s | 全程输出 token/s | 排空 s |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for t in tables:
        report.append(f"| {t['repeat']} | {t['scenario']} | {t['completed']}/{t['requests']} | {t['interactive_p50_ms']:.3f} | {t['interactive_p95_ms']:.3f} | {t['max_send_lag_ms']:.3f} | {t['completion_rps_window']:.3f} | {t['output_tps_whole_run_including_drain']:.3f} | {t['drain_seconds']:.3f} |")
    if extra_summary is not None:
        report += ["", f"补充验证 `{additional_step7.name}` 已通过：持续滴流内容不能规避请求总超时；30 条近同时完成/取消测试产生完成和取消两类结果，唯一终态与独立文件数量闭合。该补充在六轮性能采集全部结束之后执行，未向性能采集期间叠加压力。", ""]
    report += ["", "全程输出 token/s = 全部已完成请求的服务端实际输出 token / 从测量起点至最后客户端终态的时长（含排空）；窗口完成 req/s 仅计 100 s 窗口内完成的请求，两个分母不可混用。", "",
               "## 配对差异", "", "| 重复 | E0 交互 P95 ms | E1 交互 P95 ms | Δ=E1−E0 ms |", "|---|---:|---:|---:|"]
    for p in pairs: report.append(f"| {p['repeat']} | {p['e0_p95_ms']:.3f} | {p['e1_p95_ms']:.3f} | {p['delta_ms']:.3f} |")
    report += ["", "## 后台代价与全体终态", "", "后台完成时延从计划到达计算；尚未完成者单独列入终态，不作为完成样本消失。", "", "| 重复 | 后台完成/计划 | 后台完成 P50 ms | P95 ms | 最大 ms | 输出 token |", "|---|---:|---:|---:|---:|---:|"]
    for t in tables:
        if t["scenario"] == "E1":
            b = t["classes"]["background"]
            report.append(f"| {t['repeat']} | {b['completed']}/{b['planned']} | {b['completion_from_planned_p50_ms']:.3f} | {b['completion_from_planned_p95_ms']:.3f} | {b['completion_from_planned_max_ms']:.3f} | {b['output_tokens']} |")
    report += ["", "## 实际长度与前缀缓存", "", "| 重复 | 场景 | 交互新处理 / 缓存输入 token | 交互输出 token | 后台新处理 / 缓存输入 token | 后台输出 token |",
               "|---|---|---:|---:|---:|---:|"]
    for t in tables:
        a, b = t["classes"]["interactive"], t["classes"]["background"]
        report.append(f"| {t['repeat']} | {t['scenario']} | {a['new_prompt_tokens']} / {a['cached_prompt_tokens']} | {a['output_tokens']} | {b['new_prompt_tokens']} / {b['cached_prompt_tokens']} | {b['output_tokens']} |")
    report += ["", "交互轨迹相同并不强制实际输出逐字相同；temperature=0 下仍记录实际生成长度与缓存变化，不以输出上限替代实际工作量。", ""]
    report += ["", "六类终态合计：`" + json.dumps(dict(totals), ensure_ascii=False) + "`。详细输入/输出长度、缓存量、每轮终态与超阈值数见 [summary.json](summary.json)。原始证据摘要见 [evidence-sha256.json](evidence-sha256.json)。", "",
               "整个批次 GPU 采样范围（包含预热、空闲与各轮，不能代替同请求分段归因）：`" + json.dumps(telemetry, ensure_ascii=False) + "`。", "",
               "## 判断与限制", ""]
    if flags:
        report += ["存在需要复核的测量/终态问题，暂不据此通过 G0："] + ["- " + f for f in flags]
    elif all(p["delta_ms"] > 0 for p in pairs):
        report += ["这组三次合成负载对照中，加入后台后交互 P95 均上升，支持在当前设备、模型与设置下继续研究混合负载干扰。三次重复不承担正式显著性判断，不能推断异质性原因，也未验证内部软预算收益。"]
    else:
        report += ["当前三次配对未形成一致的交互 P95 恶化证据；先核对负载代表性及波动，必要时复验或收窄命题，不据此宣称准入策略有效。"]
    report += ["", "G0 仍需结合容量可观测性、接入路径、文献与研究范围复审；第七、八步完成不自动等于 P0 全阶段或 G0 已通过。当前未实现 C++ 调度策略、硬账本或服务端排队分段插桩。", "",
               "引擎日志核对 2 个 slot、每 slot 上下文 2048，已知输入 token + 最大输出上限在该范围内，且未见容量分配失败、上下文越界或 truncated=1；这些是配置与日志观测，不是动态硬预留账本。", "",
               "E1 的输出总工作量明显高于 E0，长排空和累积等待表明本设置可能由过载主导；总工作量与长度构成同时变化，不能从 E0/E1 单独分离长短异质性的独立作用。CPU 仅保存运行中快照，宿主其他应用未强制关闭，无法声称完全隔离的性能环境。", "",
               "合成 prompt 与输出上限并不代表真实生产分布；本轮模型、缓存和采样协议固定，输入/输出 token 与前缀缓存仍需和后续主场景共同解释。性能表只描述本轮预实验，不能当作最终测试结果。", ""]
    (dest / "README.md").write_text("\n".join(report), encoding="utf-8")
    print(json.dumps({"audit_status": result["audit_status"], "pairs": pairs, "total": sum(totals.values()), "report": str(dest)}, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--additional-step7", type=Path)
    args = ap.parse_args()
    analyze(args.batch, args.output, args.additional_step7)
