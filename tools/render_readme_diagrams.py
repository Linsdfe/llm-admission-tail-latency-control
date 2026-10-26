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
          text(788, 184, "进度快照  2026.10.26", 16, MUTED)]
    stages = [
        ("P0", "文献与可行性", "10.19 — 10.24 实际", "42 轮审计 / 容量补测", "G0  用户确认通过"),
        ("P1", "需求与预注册", "10.24 启动 / 11.29 计划", "质量补强 76 → 84 分", "G1  等待用户评审"),
        ("P2", "设计与开题", "11.30 — 12.20", "状态机 / 容量与回收", "G2  容量闭合"),
        ("P3", "基线与观测", "12.21 — 01.17", "B0 / B1 / 事件对账", "G3  基线可信"),
        ("P4", "核心策略", "01.18 — 02.28", "校准 / 软准入 / 老化", "G4  机制有效"),
        ("P5", "测试与实验", "03.01 — 03.28", "对照 / 消融 / 统计", "G5  03.31 数据冻结"),
        ("P6", "论文修订", "04.01 — 04.25", "论文 / 图表 / 格式", "交付  论文定稿"),
        ("P7", "归档与答辩", "04.26 — 05.16", "复现 / 演示 / 归档", "G6  交付核对"),
    ]
    for i, (pid, title, date, task, gate) in enumerate(stages):
        x, y = 40 + (i % 4) * 264, 206 + (i // 4) * 286
        active = i == 1
        completed = i == 0
        marked = active or completed
        e += [rect(x, y, 248, 230, "#ffffff", "#80c4b8" if marked else "#dce3eb"),
              text(x + 20, y + 37, pid, 24, TEAL if active else INK, 700),
              badge(x + 146, y + 14, "进行中" if active else "已完成" if completed else "待开始", marked),
              text(x + 20, y + 80, title, 23, INK, 600),
              text(x + 20, y + 111, date, 17, MUTED),
              text(x + 20, y + 151, task, 16, MUTED),
              f'<path d="M{x+20} {y+174}h208" stroke="#e3e9ef"/>',
              text(x + 20, y + 206, gate, 16, TEAL if marked else MUTED, 600)]
        if i % 4 < 3:
            e += [text(x + 250, y + 117, "›", 22, "#8b9bab")]
    e += [text(40, 470, "02  策略实现、验证与交付", 18, INK, 600),
          rect(40, 746, 1040, 46, "#e6eeF4", radius=12),
          text(60, 775, "G1 待审：采前门禁与真实反馈已补；复杂方法无已证实收益，正式规则待重锁。", 17, INK)]
    save("project-roadmap.svg", e)


def stage_progress():
    e = frame("P0 成果与 P1 进展", "进度快照 2026.10.26  /  P0 已完成 · G0 通过  /  P1 进行中 · G1 待审", 2424)
    e += [text(40, 182, "P0 已完成  /  第一步 · 2026.10.19", 18, TEAL, 600)]
    for i, (title, detail) in enumerate([
        ("环境冻结", "WSL2 / CUDA / GPU"), ("引擎与模型", "固定版本 / 构建 / 加载"),
        ("流式冒烟", "逐块计时 / 首内容核对"), ("源码定位", "7 条链路 / 15 个接入点")
    ]):
        x = 40 + i * 264
        e += [rect(x, 202, 248, 100, "#e7f4ef", "#c4dfd5"),
              text(x + 18, 237, f"{i+1:02d}  {title}", 21, TEAL, 600),
              text(x + 18, 274, detail, 16, MUTED)]
    e += [text(40, 346, "四批 42 轮、9630 条测量已审计  /  84 预热另计，批次分别解释", 18, INK, 600)]
    tasks = [
        ("05", "小轨迹回放骨架", "轨迹生成 / 调度干跑 / 真实流式回放（限 10 条）", "已完成", True),
        ("06", "正常路径小轨迹核对", "6 交互 + 4 后台；计时、文件完整性与在途重叠通过", "已通过", True),
        ("07", "异常终态与统一排空", "六类终态闭合 / 统一截止 / 真实引擎取消、超时与恢复", "已通过", True),
        ("08", "E0 / E1 配对预实验", "3 组配对 / 1500 条测量 / 计时、终态、后台代价已审计", "已完成", True),
        ("09", "低强度 C / L 补充", "3 对 1230 条完成 / P95 增加 14.478–23.176 ms / 未达 100 ms 筛选幅度", "已完成", True),
        ("10", "C++ 简单规则与对照", "12 轮简单优先 + 18 轮后台上限，共 6900 条；收益和后台代价完整保留", "已完成", True),
        ("11", "容量边界与恢复", "9 次正确性请求另列 / 超界明确报错 / 同服务恢复 / 启动与释放闭合", "已通过", True),
    ]
    for i, (num, title, detail, status, active) in enumerate(tasks):
        y = 366 + i * 92
        current = status == "下一项"
        e += [rect(40, y, 1040, 78, "#f0f8f5" if active and not current else "#ffffff", "#80c4b8" if active else "#dce3eb"),
              rect(56, y + 15, 48, 48, TEAL if current else "#dff3ed" if active else "#edf1f5", radius=12),
              text(65, y + 47, num, 22, "#ffffff" if current else TEAL if active else MUTED, 700),
              text(124, y + 33, title, 21, INK, 600),
              text(124, y + 60, detail, 17, MUTED),
              badge(972, y + 24, status, active)]
    e += [rect(40, 1030, 1040, 70, "#e9edf7", radius=14),
          text(60, 1070, "G0", 23, "#4c5885", 700),
          text(126, 1058, "项目决定：用户 2026.10.24 确认通过；P1 已继续开发与校准核对。", 17, INK),
          text(126, 1084, "导师 / 学院正式意见另记；复杂方法的增量价值仍待后续公平对照。", 16, MUTED)]
    e += [text(40, 1142, "P1 进行中  /  需求与实验方案 · 2026.10.24 启动", 22, TEAL, 600)]
    p1_tasks = [
        ("01", "需求与实验规则草案", "主场景、强对照、数据划分、指标与矩阵已列；方案尚未冻结", "已编制", True),
        ("02", "开发负载验证", "9 轮 / 2010 条测量；另 24 校准、20 预热，全部完成并留证", "已完成", True),
        ("03", "扩大校准与长短分类", "120 新校准 + 120 新验证；加余量后 100/120 仍按最大输出计算", "已核对", True),
        ("04", "输出上限检查", "8 条提示各测两种上限，共 16 次；放宽上限仍有抄录和重复", "已完成", True),
        ("05", "提示 v2 与任务核读", "40 条测量 + 2 预热；仍有格式和无依据结论，未达任务完成条件", "已核读", True),
        ("06", "资料提取、核对与整理 v3", "24 条中 9 条完整正确：提取 6/8、核对 0/8、整理 3/8；失败全文保留", "已验证", True),
        ("07", "输出约束 v4 / 明确指令 v5", "v4 格式已全合格；v5 同题完整正确 15/24 → 14/24，改善 2 题、退步 3 题", "已测完", True),
        ("08", "模型配置与适配检查", "全部 24 题同批对照：旧模型 14/24、新 3B 模型 20/24；不改提示或挑题", "已测完", True),
        ("09", "本地 3B 真实负载 v7", "96 条新单条 + 9 轮 216 条排队对照；语义正确率与速度单列，原失败保留", "已测完", True),
        ("10", "目标流量与预测复核 v8", "200/30 九轮完整复跑；2094 测量 + 20 预热，分类重合与编号长度误差留证", "已测完", True),
        ("11", "G1 方案与预测验证", "已有 144 条测量 + 离线机制核对；小作用空间与负面交付路线明确，等待用户评审", "已备齐", True),
        ("12", "统一预检与真实反馈补强", "3134 有效测量 + 28 预热；含复用资料，在线方法未见增益；固定量表 84 分", "已核对", True),
    ]
    for i, (num, title, detail, status, completed) in enumerate(p1_tasks):
        y = 1176 + i * 92
        current = status == "下一项"
        e += [rect(40, y, 1040, 78, "#f0f8f5" if completed else "#ffffff", "#80c4b8"),
              rect(56, y + 15, 48, 48, TEAL if current else "#dff3ed", radius=12),
              text(65, y + 47, num, 22, "#ffffff" if current else TEAL, 700),
              text(124, y + 33, title, 21, INK, 600),
              text(124, y + 60, detail, 16, MUTED),
              badge(972, y + 24, status, True)]
    e += [rect(40, 2294, 1040, 98, "#fff3dd", radius=14),
          text(60, 2342, "G1", 23, "#8b641e", 700),
          text(126, 2322, "统一质量评分 84 / 100：较 76 分改善，仍低于 90 分线；G1 待用户评审。", 17, INK),
          text(126, 2349, "已运行客户端 M/B3 原型；正式 C++ 与完整异常合同尚未验收。", 16, MUTED),
          text(126, 2375, "下一项：收窄研究范围，明确软估计角色，重锁正式校准与对照矩阵。", 16, MUTED)]
    save("p0-progress.svg", e)


if __name__ == "__main__":
    roadmap()
    stage_progress()
