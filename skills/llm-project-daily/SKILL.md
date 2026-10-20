---
name: llm-project-daily
description: "为 llm-admission-tail-latency-control 项目做每日收尾：记录当天工作与证据、按日期打包成果、更新 README 进度图和实验索引。用于该项目的今日总结、成果归档或进度更新请求，不用于其他项目。"
---

# LLM 推理控制项目每日收尾

把当天实际完成的工作整理成可追溯的论文过程材料、日期成果包和真实进度。仅适用于“面向混合长短请求的 LLM 推理准入与尾延迟控制系统”项目。

## 项目定位

- 从工作区定位根目录，确认 README 标题、`docs/implementation-plan.md` 和 `tools/render_readme_diagrams.py`；有远端时核对仓库为 `Linsdfe/llm-admission-tail-latency-control`。路径迁移不改变项目身份，其他仓库不应用本技能。
- 本机主仓库为 `C:/Users/1/Desktop/面向混合长短请求的 LLM 推理准入与尾延迟控制系统设计与实现`。WSL `~/proj-win` 指向同一份主仓库；`~/projects/llm-admission-tail-latency-control` 是独立构建副本。
- 日期按用户的 Asia/Shanghai 日历；补记用指定日期。有效人时未知时记“待本人核实”，自动执行时间不直接计成人工投入。
- 本技能不定时唤醒、不自动开启新实验、不自动提交/推送；会话中的既有明确授权继续适用。原始数据备份与代码推送分别报告。
- 技能源文件随项目保存在 `skills/llm-project-daily/`，个人技能目录是安装副本；维护时同步两处，避免脚本和说明漂移。

## 核对事实并更新记录

读取本次对话、Git 工作区变化、`daily/README.md`、当天日志、`docs/implementation-plan.md`、`results/README.md`，检查相关运行的 manifest、原始请求、终态、日志、分析报告及退出状态。按需运行已有核对脚本。包含当天成功、失败、异常与复验，不能只挑成功结果；没做实验就写明“未运行实验”，不编造 run_id、指标或投入。

| 内容 | 唯一位置 |
|---|---|
| 当天任务、产出、指标、问题、决策、下一步、收尾 | `daily/YYYYMMDD/README.md`，参考 `daily/TEMPLATE.md` 七块结构 |
| 当天运行、结果入口、解释范围及归档链接 | `daily/YYYYMMDD/实验结果索引.md` |
| 自动生成的产出文件清单 | `daily/YYYYMMDD/产出清单.md` |
| 原始运行证据 | `results/raw/<stage>/<含日期时间的run_id>/` |
| 派生指标与论文图表 | `results/processed/`、`results/figures/`，注明 run_id 和分析版本 |
| 日期成果包、SHA256、回执 | `results/archives/YYYYMMDD/` |
| 总索引与阶段进度 | `daily/README.md`、`results/README.md`、`docs/implementation-plan.md` |

增量更新既有记录。失败与未完成明确标识；修订索引或派生报告时说明原因，不改写原始请求和日志。`daily/` 不散放重复的大批日志，日期 ZIP 是原始目录之外的便携成果归档。

## 更新 README 进度图

根据实际验收证据，同步 README 现状、进度表、下一项、实施计划和当天日志。修改 `tools/render_readme_diagrams.py` 的状态、当前日期快照、说明与布局，然后运行 `python tools/render_readme_diagrams.py`，同时维护 `docs/images/project-roadmap.svg` 和 `p0-progress.svg`；不能只改 SVG 导致重生成回退。

阶段日期仍以开发流程计划为准。使用可用的 SVG/浏览器渲染器预览并查看图像，检查中文、状态标签、边距、裁切、长句与日期；XML 校验不等于视觉校验。“10 条正常路径通过”不等于异常终态/统一排空已验收，也不等于 P0 或 G0 通过，阶段推进须有对应决策门依据。

## 日期打包与校验

先完成当天记录、结果索引、进度图和产出清单，再运行本技能的 `scripts/package_day.py`：

```text
python <本技能目录>/scripts/package_day.py --project <主仓库路径> --date YYYYMMDD --run-dir results/raw/prestudy/<run_id>
```

- `--run-dir` 可重复，显式选择当天所有相关运行，包含失败与异常。跨天运行按当天登记日期归包，保留原 run_id 并说明区别。无实验日不传运行目录。
- `--include` 可重复加入需保留的派生文件/目录，例如 `results/figures/<文件>`。输入限定在本项目中，避免打入私钥、凭据、模型权重、引擎构建目录、缓存或 `.git`。
- 成果包包含当天记录、项目代码/文档/图示的收尾快照、所选原始运行及明确选定的派生成果；不移动或删除原始文件，同日新包不覆盖旧包。
- 当前源码快照不能冒充运行时版本。以运行记录的 code-sha256、执行版本和已有 source 快照追踪；代码变化时保留旧快照并注明差异，不伪造匹配。
- 校验 ZIP 完整性、每个文件 SHA256、文件数量和外部 ZIP SHA256；检查生成的 `daily/YYYYMMDD/每日归档.json` 与 ZIP 旁回执。把链接、数量、校验值和状态补进当天结果索引。
- 回执是在打包后生成的，不包含在其所描述的包里；包内文件代表打包时刻，补做时生成新版本。已有完整且字节一致的单运行归档可复用；项目 `scripts/ops/archive_run.py` 目前仅适配有 manifest 和 code-sha256 的小轨迹，不能把其限制套到所有阶段。

## 收尾与保留

推荐顺序：证据核对 → 日志/索引/计划/图更新与预览 → 产出清单/同步 → 日期打包/校验 → 补归档链接和回执 → 同步最后的小幅文档变化。

用 `scripts/ops/eod.sh YYYYMMDD` 生成清单与同步前，确认清理规则排除 `results/raw/`、`results/archives/`；不扩大清理范围。它不是实验验收器，仍应检查退出码、实际文件一致性和本次服务是否停止。

有效原始数据、失败/异常、轨迹、配置、日志、版本及分析都保留，不能因结果“不好看”删除。只有会话中已有对应授权才进行删除或外部发送；本技能不扩大权限。

WSL `~/proj-win` 与 Windows 是同一份数据，不算双备份。ZIP 仍在本机时写“本地归档，未建立异地副本”；GitHub 代码更新不等于原始数据已备份。

最后简短给出：当天做了什么、日期记录入口、成果包与校验结果、图的进度和下一项；归档/同步/渲染未完成时明确说明，不声称完成。
