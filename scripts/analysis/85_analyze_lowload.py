#!/usr/bin/env python3
"""Audit all six low-load runs and create a new versioned evidence report."""
import argparse
import csv
import hashlib
from pathlib import Path
import sys

from lowload_evidence import audit_run, diagnostics, jsonlines, read

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/replay"))
from open_loop import dump, percentile, STATES


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def analyze(batch, out):
    batch = batch.resolve()
    assert read(batch / "batch-status.json")["status"] == "completed"
    cfg = read(batch / "protocol.json")
    for rel, digest in read(batch / "code-sha256.json").items():
        assert sha(batch / "source" / rel) == digest
    for rel, digest in read(batch / "input-source-sha256.json").items():
        assert sha(batch / "input-source" / rel) == digest
    original_c = jsonlines(batch / "input-source/r1-E0/replay/trace.jsonl")
    original_l = jsonlines(batch / "input-source/r1-E1/replay/trace.jsonl")
    selected_l = [r for r in original_l if r["category"] == "interactive" or r["req_id"] in cfg["background_ids"]]
    assert len(original_c) == 200 and len(selected_l) == 210
    assert [r for r in selected_l if r["category"] == "interactive"] == original_c
    traces = {s: jsonlines(batch / f"trace-{s}.jsonl") for s in ("C", "L")}
    assert traces == {"C": original_c, "L": selected_l}
    out.mkdir(parents=True, exist_ok=False)
    totals = {s: 0 for s in STATES}
    runs, pairs, request_table = [], [], []
    for repeat, order in enumerate(cfg["execution_order"], 1):
        paired = {}
        for scenario in order:
            directory = batch / f"r{repeat}-{scenario}"
            manifest = read(directory / "manifest.json")
            env = read(batch / "environment.json")
            assert manifest["status"] == "completed"
            assert manifest["engine_commit"] == env["engine_commit"] == cfg["engine_commit"]
            assert manifest["model_sha256"] == env["model_sha256"] == cfg["model_sha256"]
            assert manifest["workload_sha256"] == sha(directory / "replay/trace.jsonl")
            assert manifest["repeat"] == repeat and manifest["scenario"] == scenario and manifest["order"] == order
            assert read(directory / "warmup/summary.json")["terminal_counts"]["completed"] == 2
            audit, rows = audit_run(directory, traces[scenario], cfg)
            assert not audit["flags"], audit["flags"]
            metrics = (directory / "metrics-after.txt").read_text(encoding="utf-8")
            metrics_zero = {}
            for name in ("llamacpp:requests_processing", "llamacpp:requests_deferred"):
                vals = [float(line.split()[1]) for line in metrics.splitlines() if line.startswith(name + " ")]
                metrics_zero[name] = bool(vals) and all(v == 0 for v in vals)
            assert all(metrics_zero.values()), metrics_zero
            summary = audit["summary"]
            for s in STATES:
                totals[s] += summary["terminal_counts"][s]
            all_inter = [r for r in rows if r["category"] == "interactive"]
            first_content = [r["ttft_ms"] for r in all_inter if r["ttft_ms"] is not None]
            outputs = sum(c["output_tokens"] for c in summary["classes"].values())
            background = [{"req_id": r["req_id"], "terminal": r["terminal"], "completion_from_planned_ms": r["end_offset_ms"] - r["arrival_offset_ms"],
                           "output_tokens": (r.get("timings") or {}).get("predicted_n"), "finish_reason": r["finish_reason"]}
                          for r in sorted(rows, key=lambda r: r["arrival_offset_ms"]) if r["category"] == "background"]
            info = {"repeat": repeat, "scenario": scenario, **audit,
                    "interactive_first_content_samples": len(first_content), "all_observed_interactive_ttft_p95_ms": percentile(first_content, 95),
                    "metrics_zero": metrics_zero, "background_requests": background,
                    "output_tps_whole_run_including_drain": outputs / summary["last_end_seconds"],
                    "server_exit_code": manifest["server_exit_code"]}
            runs.append(info)
            paired[scenario] = info
            for r in rows:
                request_table.append({"run": directory.name, "req_id": r["req_id"], "category": r["category"],
                                      "planned_ms": r["arrival_offset_ms"], "actual_send_ms": r["actual_send_offset_ms"],
                                      "first_content_ms": r["first_content_offset_ms"], "end_ms": r["end_offset_ms"],
                                      "ttft_ms": r["ttft_ms"], "send_lag_ms": r["send_lag_ms"], "terminal": r["terminal"],
                                      "input_new_tokens": (r.get("timings") or {}).get("prompt_n"), "input_cached_tokens": (r.get("timings") or {}).get("cache_n"),
                                      "output_tokens": (r.get("timings") or {}).get("predicted_n"), "finish_reason": r["finish_reason"]})
        c = paired["C"]["summary"]["classes"]["interactive"]["ttft_p95_ms"]
        l = paired["L"]["summary"]["classes"]["interactive"]["ttft_p95_ms"]
        pairs.append({"repeat": repeat, "c_p95_ms": c, "l_p95_ms": l, "delta_ms": l-c,
                      "l_no_obvious_persistent_backlog": paired["L"]["diagnostics"]["no_obvious_persistent_backlog"]})
    assert sum(totals.values()) == 1230
    support = totals["completed"] == 1230 and all(p["l_no_obvious_persistent_backlog"] and p["delta_ms"] >= cfg["practical_delta_ms"] for p in pairs)
    result = {"batch": batch.name, "audit_status": "passed", "scope": "Exploratory paired runs on one deterministic synthetic workload; not final significance evidence",
              "protocol": cfg, "terminal_counts": totals, "total_requests": sum(totals.values()), "pairs": pairs, "runs": runs,
              "hypothesis_screen_supported": support, "analysis_code_sha256": sha(Path(__file__)),
              "analysis_module_sha256": sha(Path(__file__).with_name("lowload_evidence.py")),
              "source_execution_analysis_matches_current": sha(Path(__file__)) == sha(batch / "source/scripts/analysis/85_analyze_lowload.py")}
    dump(out / "summary.json", result)
    dump(out / "evidence-sha256.json", {p.relative_to(batch).as_posix(): sha(p) for p in sorted(batch.rglob("*")) if p.is_file()})
    for name, values in (("paired-summary.csv", pairs), ("request-metrics.csv", request_table)):
        with (out / name).open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(values[0]))
            w.writeheader()
            w.writerows(values)
    report = ["# P0 低强度混合负载补充实验", "", f"批次：`{batch.name}`；审计：passed；分析版本：v1。", "",
              "## 研究问题和协议", "", "比较同期交互独占 C（200 条）与低强度混合 L（相同交互加 10 条后台）；后台每 10 s 一条，按预先固定编号稀疏化。",
              "3 对、6 轮，每轮全新服务、两条预热；100 s 窗口、最多 180 s 排空、180 s 请求总超时、50 ms 迟发门槛。",
              "配置和协议在采集前冻结并保存在原始批次 source/；预热共 12 条不计 1230 条测量。独立效应重复单位为 3 个配对，不是 1230 条请求。", "",
              "[执行协议](../../prestudy-lowload-protocol-v1.md) · [机器摘要](summary.json) · [配对表](paired-summary.csv) · [逐请求指标](request-metrics.csv) · [原始证据摘要](evidence-sha256.json)", "",
              "## 配对主指标", "", "| 对 | C P95 ms | L P95 ms | Δ ms | L 未见明显持续积压 |", "|---|---:|---:|---:|---|"]
    for p in pairs:
        report.append(f"| {p['repeat']} | {p['c_p95_ms']:.3f} | {p['l_p95_ms']:.3f} | {p['delta_ms']:.3f} | {p['l_no_obvious_persistent_backlog']} |")
    report += ["", "## 完整性、实际工作量与代价", "", f"六类终态：`{totals}`。", "",
               "| 轮 | 完成/计划 | 交互 P50 ms | 最大迟发 ms | 实际输出 token（交互/后台） | 新处理/缓存输入 token（全体） | 窗口完成 req/s | 全程输出 token/s | 排空 s |",
               "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in runs:
        s = r["summary"]; i = s["classes"]["interactive"]; b = s["classes"]["background"]
        report.append(f"| r{r['repeat']}-{r['scenario']} | {s['terminal_counts']['completed']}/{s['planned']} | {i['ttft_p50_ms']:.3f} | {s['send_lag_max_ms']:.3f} | {i['output_tokens']}/{b['output_tokens']} | {i['new_prompt_tokens']+b['new_prompt_tokens']}/{i['cached_prompt_tokens']+b['cached_prompt_tokens']} | {s['window_completion_throughput_rps']:.3f} | {r['output_tps_whole_run_including_drain']:.3f} | {s['drain_elapsed_seconds']:.3f} |")
    report += ["", "TTFT 从 TCP 建连后、写 HTTP 前至首个非空生成内容。分位数采用线性插值，主表基于 completed 交互，并在 summary 中另列所有已观测首内容及覆盖数。",
               "窗口吞吐分母为 100 s；全程 token/s 分母为测量起点至最后客户端终态，包含排空。actual output 使用 predicted_n，输出上限不替代实际值。", "",
               "| L 轮 | 9 个后台到达前在途数 | 末段−首段交互 P50 ms | 后台完成中位数 ms | 后台完成最大 ms |", "|---|---|---:|---:|---:|"]
    for r in runs:
        if r["scenario"] == "L":
            d = r["diagnostics"]; b = r["summary"]["classes"]["background"]
            report.append(f"| r{r['repeat']}-L | {','.join(str(c['active_before']) for c in d['background_arrival_checks'])} | {d['last_minus_first_quarter_p50_ms']:.3f} | {b['completion_from_planned_p50_ms']:.3f} | {b['completion_from_planned_max_ms']:.3f} |")
    report += ["", "客户端在途数包括生成及网络等待，不等于服务端排队数。工作定义要求 9 个检查点均清空、四段中末段相对首段 P50 增加≤100 ms、排空≤10 s、全体正常完成。满足不代表长期稳态。后台每轮仅 10 条，逐条代价保存在 summary.json 和逐请求表，不以后台 P95 作主要判断。", "", "## 判读与边界", ""]
    if support:
        report.append("三对均满足预设的未见明显持续积压诊断，且交互 P95 配对增加均达到预设的 100 ms 探索筛选幅度。这为持续积压之外仍存在交互干扰提供线索，支持继续研究准入价值。")
    else:
        report.append("本批次未同时满足三对均无明显持续积压且 P95 配对增加均≥100 ms 的预设筛选条件；按逐对结果判断，不能只凭完成者 P95 宣称假设成立。")
    report += ["100 ms 是事先固定的探索性工程门槛，不是正式 SLO 或显著性阈值。三对不提供正式显著性结论，不对共享队列请求逐条 bootstrap。",
               "L 同时降低后台请求数、工作量并改变缓存路径；与旧 E1 的比较仅为跨批次背景参照。本实验没有识别长度构成的独立作用，未验证自研 C++ 策略、KV 账本或长期公平性。固定相位、合成短输入、单设备模型与宿主应用竞争均限制外推。",
               "实际长度及缓存差异完整报告，不按结果删样凑等量。服务端排队/输入处理/交付分段仍未知，TTFT 不能全部称为纯排队。",
               "G0 仍待文献、范围、人时/机时与评审确认；P0 未标为全阶段完成。", "", "## 复核与数据位置", "",
               f"原始目录：`results/raw/prestudy/{batch.name}/`，其中 source/ 为执行快照；输入旧轨迹副本与 SHA 在 input-source/。",
               "全部运行的前后 metrics、server-command、props、服务日志、逐请求和 GPU 采样保留。运行版本以快照和 SHA 为准，不能用当时 Git HEAD 代替未提交源码版本。",
               "原始证据与日期 ZIP 在本地保留，GitHub 发布摘要和代码，不等于完整原始数据异地备份。"]
    (out / "README.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print({"audit": "passed", "requests": sum(totals.values()), "pairs": pairs, "screen_supported": support})


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--batch", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    analyze(a.batch, a.output)
