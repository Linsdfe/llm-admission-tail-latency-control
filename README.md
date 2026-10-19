# 面向混合长短请求的 LLM 推理准入与尾延迟控制系统

本科毕业设计项目，研究同一 LLM 推理实例混合处理交互请求与后台任务时的准入与尾延迟控制。计划基于 llama.cpp 实现 C++ 调度扩展，结合输出长度估计、在线误差校准、软工作量预算和后台老化，评估交互请求 P95 首 token 延迟、吞吐及后台完成代价。

当前仓库处于方案与预实验准备阶段，包含研究和开发文档及文档维护工具，尚未包含推理系统实现或尾延迟实测结果。

2026-10-19 已完成 P0 第一步（环境冻结、引擎固定与 CUDA 构建、模型加载、脚本化流式冒烟、源码定位），详见[每日记录 20261019](daily/20261019/README.md)与[实施计划](docs/implementation-plan.md)。冒烟仅用于核对链路与计时口径，不构成性能结论。

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

| 文档 | 用途 |
|---|---|
| [开题报告](开题报告_面向混合长短请求的LLM推理准入与尾延迟控制系统.md) | 研究问题、系统设计、实验目标和参考文献 |
| [开发流程计划](开发流程计划_面向混合长短请求的LLM推理准入与尾延迟控制系统.md) | 进度、人时、决策门、验收和需求追踪 |
| [预实验方案](预实验方案_干扰复现与干预探针.md) | 先执行 E0/E1，再按数据选择补测 |
| [实施计划](docs/implementation-plan.md) | 分步执行清单、第一步执行记录、后续启动清单 |
| [环境记录](docs/environment.md) | 环境实测值、版本与 SHA、构建与启动命令 |
| [源码定位](docs/source-map.md) | 固定引擎的接入/队列/slot/prefill/取消/回收定位 |
| [每日记录](daily/) | **按日期的工作日志与成果**（`daily/<YYYYMMDD>/README.md` + 自动生成的 `产出清单.md`），索引见 `daily/README.md` |
| [运行索引](results/README.md) | prestudy 各次运行的性质、状态与取代关系 |
| [运维脚本](scripts/ops/README.md) | 单向同步、双副本一致性检查、每日收尾（`eod.sh`） |

建议按“开题报告 → 开发流程计划 → 预实验方案”的顺序阅读。

## 开始实施

执行入口为 [分步骤实施计划](docs/implementation-plan.md)，其中包含第一步检查清单、后续阶段交付与目录用途。

第一步先在 WSL 中完成固定版本原始引擎的构建、模型加载和两类流式请求核对，填写 [环境记录](docs/environment.md) 和 [源码定位](docs/source-map.md)；随后开展小轨迹核对及 E0/E1 预实验。

已建立 C++ 模块、配置、回放/分析、测试和数据产物目录。当前 `CMakeLists.txt` 仅为目录配置骨架，不生成推理程序；配置清单是待填写模板，尚无可运行的调度系统、回放器或实测结果。目录说明见实施计划及各目录 README。

## 文档维护

GitHub 仓库以 Markdown 为内容源。Word（`.docx`）仅在本地保留，用于提交材料，已通过 `.gitignore` 排除；克隆当前版本不会获得 Word 文件。

[`tools/`](tools/README.md) 提供 Markdown 到 Word 的同步与排版检查工具，依赖本地已有的同名 Word 模板和配置好的 Python、LibreOffice 等环境。修改 Markdown 后，如需更新本地 Word，可按工具说明同步、渲染并逐页检查。当前工具配置包含本机路径，其他设备使用前需要调整。
