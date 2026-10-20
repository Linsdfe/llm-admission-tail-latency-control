#!/usr/bin/env python3
"""Render the README's editable SVG diagrams with Python's standard library.

Update dates/status here, then run: python tools/render_readme_diagrams.py
Keep README progress and docs/implementation-plan.md consistent with the figures.
"""
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "images"
INK, MUTED, TEAL = "#182d43", "#53677c", "#087f78"


def text(x, y, value, size=18, fill=INK, weight=400):
    return (f'<text x="{x}" y="{y}" font-size="{size}" '
            f'fill="{fill}" font-weight="{weight}">{escape(value)}</text>')


def rect(x, y, w, h, fill, stroke="none", radius=16):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
            f'rx="{radius}" fill="{fill}" stroke="{stroke}"/>')


def badge(x, y, label, active=False):
    return (rect(x, y, 82, 28, "#dff3ed" if active else "#edf1f5", radius=14)
            + text(x + 14, y + 20, label, 14, TEAL if active else MUTED, 600))


def frame(title, subtitle, height):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="{height}" '
            f'viewBox="0 0 1120 {height}" role="img" aria-labelledby="title desc">',
            f'<title id="title">{escape(title)}</title><desc id="desc">{escape(subtitle)}</desc>',
            '<g font-family="Segoe UI, PingFang SC, Microsoft YaHei, Noto Sans CJK SC, sans-serif">',
            rect(0, 0, 1120, height, "#f5f7fa", radius=24),
            rect(0, 0, 1120, 142, "#182d43", radius=24),
            rect(0, 80, 1120, 62, "#182d43", radius=0),
            text(40, 36, "LLM ADMISSION / PROJECT ROADMAP", 13, "#9cb6cc", 600),
            text(40, 80, title, 30, "#ffffff", 700),
            text(40, 115, subtitle, 17, "#c4d3df")]


def save(name, elements):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text("\n".join(elements + ["</g></svg>"]) + "\n", encoding="utf-8")


def roadmap():
    e = frame("从可行性验证到系统交付", "2026.10.19 — 2027.05.16  /  八个阶段 · 七个决策门", 824)
    e += [text(40, 184, "01  可行性、设计与基线", 18, INK, 600),
          text(788, 184, "进度快照  2026.10.20", 16, MUTED)]
    stages = [
        ("P0", "文献与可行性", "10.19 — 11.08", "回放器 / E0 · E1", "G0  可行性核对"),
        ("P1", "需求与预注册", "11.09 — 11.29", "需求冻结 / 实验矩阵", "G1  预注册冻结"),
        ("P2", "设计与开题", "11.30 — 12.20", "状态机 / 容量与回收", "G2  容量闭合"),
        ("P3", "基线与观测", "12.21 — 01.17", "B0 / B1 / 事件对账", "G3  基线可信"),
        ("P4", "核心策略", "01.18 — 02.28", "校准 / 软准入 / 老化", "G4  机制有效"),
        ("P5", "测试与实验", "03.01 — 03.28", "对照 / 消融 / 统计", "G5  03.31 数据冻结"),
        ("P6", "论文修订", "04.01 — 04.25", "论文 / 图表 / 格式", "交付  论文定稿"),
        ("P7", "归档与答辩", "04.26 — 05.16", "复现 / 演示 / 归档", "G6  交付核对"),
    ]
    for i, (pid, title, date, task, gate) in enumerate(stages):
        x, y = 40 + (i % 4) * 264, 206 + (i // 4) * 286
        active = i == 0
        e += [rect(x, y, 248, 230, "#ffffff", "#80c4b8" if active else "#dce3eb"),
              text(x + 20, y + 37, pid, 24, TEAL if active else INK, 700),
              badge(x + 146, y + 14, "进行中" if active else "待开始", active),
              text(x + 20, y + 80, title, 23, INK, 600),
              text(x + 20, y + 111, date, 17, MUTED),
              text(x + 20, y + 151, task, 16, MUTED),
              f'<path d="M{x+20} {y+174}h208" stroke="#e3e9ef"/>',
              text(x + 20, y + 206, gate, 16, TEAL if active else MUTED, 600)]
        if i % 4 < 3:
            e += [text(x + 250, y + 117, "›", 22, "#8b9bab")]
    e += [text(40, 470, "02  策略实现、验证与交付", 18, INK, 600),
          rect(40, 746, 1040, 46, "#e6eeF4", radius=12),
          text(60, 775, "当前：P0 第一步已完成；下一项是开放到达回放器。G0 尚未通过，后续阶段均未开始。", 17, INK)]
    save("project-roadmap.svg", e)


def p0():
    e = frame("P0 进度与下一步", "阶段截止 2026.11.08  /  已完成环境与链路验证，进入回放与干扰复现准备", 760)
    e += [text(40, 182, "已完成  /  第一步 · 2026.10.19", 18, TEAL, 600)]
    for i, (title, detail) in enumerate([
        ("环境冻结", "WSL2 / CUDA / GPU"), ("引擎与模型", "固定版本 / 构建 / 加载"),
        ("流式冒烟", "逐块计时 / 首内容核对"), ("源码定位", "7 条链路 / 15 个接入点")
    ]):
        x = 40 + i * 264
        e += [rect(x, 202, 248, 100, "#e7f4ef", "#c4dfd5"),
              text(x + 18, 237, f"{i+1:02d}  {title}", 21, TEAL, 600),
              text(x + 18, 274, detail, 16, MUTED)]
    e += [text(40, 346, "接下来  /  第二步 · 按验收结果依次推进", 18, INK, 600)]
    tasks = [
        ("05", "开放到达回放器", "计划表独立发送；记录滞后、首内容和终态", "下一项", True),
        ("06", "10 条小轨迹核对", "ID 唯一、发送准确、计时有序、终态与排空闭合", "待开始", False),
        ("07", "E0 / E1 配对预实验", "每组 200 交互 × 3；E1 同轨迹叠加约 100 后台", "待开始", False),
    ]
    for i, (num, title, detail, status, active) in enumerate(tasks):
        y = 366 + i * 92
        e += [rect(40, y, 1040, 78, "#ffffff", "#80c4b8" if active else "#dce3eb"),
              rect(56, y + 15, 48, 48, TEAL if active else "#edf1f5", radius=12),
              text(65, y + 47, num, 22, "#ffffff" if active else MUTED, 700),
              text(124, y + 33, title, 21, INK, 600),
              text(124, y + 60, detail, 17, MUTED),
              badge(972, y + 24, status, active)]
    e += [rect(40, 662, 1040, 60, "#e9edf7", radius=14),
          text(60, 700, "G0", 23, "#4c5885", 700),
          text(126, 686, "核对可行性：测量可信、容量可观测、接入路径可达；据 E0/E1 判断干扰与范围。", 17, INK),
          text(126, 710, "字段不足先修测量；未见实质干扰则核对条件、收窄命题并复审。", 16, MUTED)]
    save("p0-progress.svg", e)


if __name__ == "__main__":
    roadmap()
    p0()
