#!/usr/bin/env python3
"""Render standalone scientific figures from the audited low-load batch."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import MaxNLocator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/analysis"))
from lowload_evidence import jsonlines, read


def render(batch, report):
    result = read(report / "summary.json")
    assert result["batch"] == batch.name and result["audit_status"] == "passed"
    font = Path("C:/Windows/Fonts/msyh.ttc")
    if font.exists():
        font_manager.fontManager.addfont(str(font))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(font)).get_name()
    plt.rcParams.update({"axes.unicode_minus": False, "font.size": 10, "svg.fonttype": "none"})
    colors = {"C": "#3975a6", "L": "#c36c2f"}
    fig, axes = plt.subplots(2, 3, figsize=(13, 6.5), sharex="col", sharey="row", constrained_layout=True)
    largest_ttft = 0
    for repeat in range(1, 4):
        for scenario in ("C", "L"):
            rows = sorted(jsonlines(batch / f"r{repeat}-{scenario}/replay/client.jsonl"), key=lambda r: r["arrival_offset_ms"])
            inter = [r for r in rows if r["category"] == "interactive" and r["ttft_ms"] is not None]
            largest_ttft = max(largest_ttft, max(r["ttft_ms"] for r in inter))
            axes[0, repeat-1].plot([r["arrival_offset_ms"]/1000 for r in inter], [r["ttft_ms"] for r in inter],
                                  color=colors[scenario], linewidth=.8, marker=".", markersize=2, label=scenario)
            events = []
            for r in rows:
                if r["actual_send_offset_ms"] is not None:
                    events += [(r["actual_send_offset_ms"]/1000, 1), (r["end_offset_ms"]/1000, -1)]
            grouped = {}
            for t, delta in events:
                grouped[t] = grouped.get(t, 0) + delta
            times, counts, count = [0], [0], 0
            for t, delta in sorted(grouped.items()):
                count += delta
                times.append(t); counts.append(count)
            axes[1, repeat-1].step(times, counts, where="post", linewidth=.9, alpha=.8, color=colors[scenario], label=scenario)
        pair = result["pairs"][repeat-1]
        axes[0, repeat-1].set_title(f"配对 {repeat} · P95 增加 {pair['delta_ms']:.1f} ms")
        axes[0, repeat-1].set_ylabel("交互 TTFT (ms)")
        axes[1, repeat-1].set_ylabel("客户端在途请求数")
        axes[1, repeat-1].set_xlabel("计划到达 / 事件时间 (s)")
        axes[1, repeat-1].yaxis.set_major_locator(MaxNLocator(integer=True))
        for ax in axes[:, repeat-1]:
            ax.grid(True, alpha=.2)
            ax.set_xlim(0, 105)
        for i in range(10):
            axes[1, repeat-1].axvline(.25+i*10, color="#969696", alpha=.2, linewidth=.7)
    axes[0, 0].set_ylim(0, largest_ttft * 1.12)
    fig.legend(*axes[0, 0].get_legend_handles_labels(), loc="outside lower center", ncol=2,
               title="C 交互独占 / L 低强度混合", fontsize=9)
    fig.suptitle("P0 低强度补充：逐请求等待与客户端在途轨迹\n灰线标记 L 后台计划到达；在途数包含生成和网络等待，不能等同于引擎排队", fontsize=12)
    for suffix in ("png", "svg"):
        fig.savefig(report / f"lowload-timeline.{suffix}", dpi=160)
    plt.close(fig)
    metadata = {"batch": batch.name, "analysis_summary_sha256": hashlib.sha256((report / "summary.json").read_bytes()).hexdigest(),
                "plot_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "matplotlib_version": matplotlib.__version__, "python": sys.version,
                "scope": "Static scientific plots; no inferred server queue or uncertainty intervals"}
    (report / "figure-provenance.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--batch", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    a = p.parse_args()
    render(a.batch.resolve(), a.report.resolve())
