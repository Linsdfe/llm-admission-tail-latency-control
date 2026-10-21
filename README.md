# 面向混合长短请求的 LLM 推理准入与尾延迟控制系统

本科毕业设计项目，研究同一 LLM 推理实例混合处理交互请求与后台任务时的准入与尾延迟控制。计划基于 llama.cpp 实现 C++ 调度扩展，结合输出长度估计、在线误差校准、软工作量预算和后台老化，评估交互请求 P95 首 token 延迟、吞吐及后台完成代价。

当前处于 **P0 可行性验证阶段**：原 E0/E1 三组共 1500 条测量已审计；2026-10-21 又完成低强度 C/L 三组共 1230 条测量，全部完成且审计通过。低强度下三对交互 P95 增加 14.478–23.176 ms，均未达预设 100 ms 探索筛选幅度，三轮 L 均未见明显持续积压。G0 仍待复审，C++ 调度策略尚未实现；这些是合成负载预实验，未识别长度独立效应。

2026-10-19 已完成 P0 第一步（环境冻结、引擎固定与 CUDA 构建、模型加载、脚本化流式冒烟、源码定位），详见[每日记录 20261019](daily/20261019/README.md)与[实施计划](docs/implementation-plan.md)。冒烟仅用于核对链路与计时口径，不构成性能结论。

## 任务流程与当前进度

项目分 **P0—P7 八个阶段**，设 **G0—G6 七个决策门**。以下为 **2026-10-21 进度快照**：第七、八步及低强度补充已完成，下一项为主场景范围与 G0 可行性复审；P0 仍进行中，P1 尚未开始。日期为计划窗口，跨年阶段延续至 2027 年。

![项目路线图：P0 进行中，P1 至 P7 待开始；包含各阶段日期、交付与决策门](docs/images/project-roadmap.svg)

![P0 进度：原 E0/E1 与六轮低强度补充均已完成并审计；G0 待复审](docs/images/p0-progress.svg)

图中绿色表示进行中或已完成的工作（以状态文字为准），灰色表示待开始。图为静态进度快照，不使用随日期移动的“今日线”。决策门未通过时，按《开发流程计划》§4.3 调整并复验。

SVG 源文件位于 [`docs/images/`](docs/images/)，可直接编辑；也可修改 [`tools/render_readme_diagrams.py`](tools/render_readme_diagrams.py) 后运行 `python tools/render_readme_diagrams.py` 重新生成。阶段推进时同步更新图、下表和实施计划，并在 `daily/` 留存依据。

| 阶段 | 时间 | 状态 | 关键交付 / 门槛 |
|---|---|---|---|
| P0 第一步 | 10-19 | ✅ 已完成 | 环境记录、引擎与模型固定、冒烟核对、源码定位表 |
| **P0 第二步** | 至 11-08 | **▶ 进行中** | 原 1500 条及低强度补充 1230 条审计已完成；下一项 **主场景范围与 G0 复审** |
| P1 | 11-09 → 11-29 | 未开始 | 需求与实验冻结、正式矩阵与机时 → G1 |
| P2 | 11-30 → 12-20 | 未开始 | 状态机/容量/接口设计、R08 去留 → G2 |
| P3 | 12-21 → 01-17 | 未开始 | B0/B1 基线、事件插桩、小轨迹对账 → G3 |
| P4 | 01-18 → 02-28 | 未开始 | 校准、软准入、老化、对照与组合策略 M → G4 |
| P5 | 03-01 → 03-28 | 未开始 | 验收 E1—E7、正式对照、消融、统计 → G5（03-31 冻结） |
| P6 | 04-01 → 04-25 | 未开始 | 论文整合、结果与图表、修改格式 |
| P7 | 04-26 → 05-16 | 未开始 | 归档复核、演示、答辩 → G6 |

### 下一阶段先做什么

先结合[低强度补充报告](docs/experiments/20261021-093300-p0-lowload-v1/README.md)与[原强负载报告](docs/experiments/20261020-234210-p0-e0e1/README.md)收窄研究范围，再完成 [G0 复审](docs/p0-g0-review.md)的文献、预算和评审记录。当前低强度场景只有十几至二十几毫秒的 P95 配对增加；若需要中间强度证据，应另立协议，不能为得到预期结果反复加压。全部运行及归档见[当日索引](daily/20261021/实验结果索引.md)。

P0 通过后，再进入 **P1 需求与实验预注册（11-09—11-29）**，依据预实验波动冻结主场景、SLO、数据划分、重复规模、排除规则和完整机时矩阵。

## 记录分工（单一来源，避免重复）

同一事实只在一处维护，其他文档只引用不复制：

| 内容 | 唯一权威位置 |
|---|---|
| 环境实测值、版本与 SHA、构建/启动命令、问题处置 | [docs/environment.md](docs/environment.md) |
| 分步任务、完成判据、执行记录、后续启动清单 | [docs/implementation-plan.md](docs/implementation-plan.md) |
| 固定引擎的源码定位（文件:行号、接入点、可观测口径） | [docs/source-map.md](docs/source-map.md) |
| 引擎版本与克隆方式 | `third_party/README.md` + `results/raw/prestudy/<date>-build/clone-record.md` |
| 单次实验的原始数据与分析结论 | `results/raw/<stage>/<run_id>/README.md` + `manifest.json` |
| 运行状态索引（哪个是正式证据、哪个被取代） | [results/README.md](results/README.md) |
| 当天做了什么、人时、问题与下一步 | `daily/<YYYYMMDD>/README.md`（索引见 [daily/README.md](daily/README.md)） |
| 工具用法与计时口径 | [scripts/replay/README.md](scripts/replay/README.md) |
| 同步、一致性检查、每日收尾 | [scripts/ops/README.md](scripts/ops/README.md) |

## 文档导航

原 E0/E1 批次的历史通俗总结提供 [Markdown 源码](docs/reports/20261021/P0阶段总结与下一阶段安排.md)、[Word 下载](docs/reports/20261021/P0阶段总结与下一阶段安排.docx) 和 [原始样本及校验清单](docs/reports/20261021/data/)。内含逐请求数据、计算示例、名词解释及下一阶段安排；9份公开样本不替代本机完整实验档案。

| 文档 | 用途 |
|---|---|
| [开题报告](开题报告_面向混合长短请求的LLM推理准入与尾延迟控制系统.md) | 研究问题、系统设计、实验目标和参考文献 |
| [开发流程计划](开发流程计划_面向混合长短请求的LLM推理准入与尾延迟控制系统.md) | 进度、人时、决策门、验收和需求追踪 |
| [P0 实验复审与补充设计](docs/p0-experiment-review-minimal-supplement-20261021.md) | 已知证据、解释缺口、最小补充与可选探针；旧通用方案已融合 |
| [低强度补充执行协议](docs/prestudy-lowload-protocol-v1.md) | C/L 六轮配对、冻结阈值、完整终态与停止规则 |
| [实施计划](docs/implementation-plan.md) | 分步执行清单、第一步执行记录、后续启动清单 |
| [环境记录](docs/environment.md) | 环境实测值、版本与 SHA、构建与启动命令 |
| [源码定位](docs/source-map.md) | 固定引擎的接入/队列/slot/prefill/取消/回收定位 |
| [每日记录](daily/) | **按日期的工作日志与成果**（`daily/<YYYYMMDD>/README.md` + 自动生成的 `产出清单.md`），索引见 `daily/README.md` |
| [运行索引](results/README.md) | prestudy 各次运行的性质、状态与取代关系 |
| [运维脚本](scripts/ops/README.md) | 单向同步、双副本一致性检查、每日收尾（`eod.sh`） |

建议按“开题报告 → 开发流程计划 → P0 复审 → 对应批次协议和报告”的顺序阅读。通用回放字段及可选探针统一见 [回放说明](scripts/replay/README.md)。

## 开始实施

结果按日期留存：`daily/YYYYMMDD/` 保存工作记录与结果索引；`results/raw/prestudy/<含日期的run_id>/` 保存唯一运行证据；`results/archives/YYYYMMDD/` 保存含校验值的本地归档包。原始数据默认不入 Git，详见 [保留规则](results/README.md#论文证据保留与按日期归档)。

执行入口为 [分步骤实施计划](docs/implementation-plan.md)，其中包含第一步检查清单、后续阶段交付与目录用途。

第一步先在 WSL 中完成固定版本原始引擎的构建、模型加载和两类流式请求核对，填写 [环境记录](docs/environment.md) 和 [源码定位](docs/source-map.md)；随后的小轨迹、E0/E1 与低强度补充现已完成；历史入口仅供复现，新运行必须新建目录。

已建立 C++ 模块、配置、回放/分析、测试和数据产物目录。当前 `CMakeLists.txt` 仅为目录配置骨架，不生成推理程序；配置清单是待填写模板，已具备异步开放到达、六类终态和统一排空工具，并完成三组配对预实验；尚无可运行的 C++ 调度系统或最终测试结论。目录说明见实施计划及各目录 README。

## 实验方法技能

项目安装了 [`scientific-critical-thinking`](.agents/skills/scientific-critical-thinking/SKILL.md)，来源为 [K-Dense Scientific Agent Skills](https://github.com/K-Dense-AI/scientific-agent-skills)，采用 MIT 许可证并固定上游提交。它用于实验设计、变量与对照、统计解释，以及论文方法和结论审查；[项目中文工作流](.agents/skills/scientific-critical-thinking/references/llm-project-workflow.md) 区分负载强度、长度分布和调度策略三个问题。[来源清单](.agents/skills/scientific-critical-thinking/SOURCE.json) 保留版本、原始校验值和适配说明。

可直接说：“使用 scientific-critical-thinking 技能，审查 P0 E0/E1 实验，给出最小补充对照方案，先写协议。”项目 `AGENTS.md` 已加入相关研究任务的读取入口；安装技能本身不会启动实验或推进阶段。

## 文档维护

项目每日收尾使用 [`$llm-project-daily`](skills/llm-project-daily/SKILL.md)：核对当天实际工作，更新日志与结果索引，按日期打包成果并校验，同步 README 进度表和两张进度图。可以直接说“按项目技能做今天收尾”。技能源文件随项目保存，个人技能目录中已安装可发现的副本；它不创建定时任务，也不自动提交或推送。

GitHub 仓库以 Markdown 为内容源。Word（`.docx`）默认在本地保留；P0 阶段总结按发布要求单独纳入版本管理，可从上方文档入口下载，其他材料仍按 `.gitignore` 排除。

[`tools/`](tools/README.md) 提供 Markdown 到 Word 的同步与排版检查工具，依赖本地已有的同名 Word 模板和配置好的 Python、LibreOffice 等环境。修改 Markdown 后，如需更新本地 Word，可按工具说明同步、渲染并逐页检查。当前工具配置包含本机路径，其他设备使用前需要调整。
