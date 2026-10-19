# 2026-10-19 工作记录

**主题**：P0 第一步——环境冻结、llama.cpp 固定版构建、模型加载、脚本化流式冒烟、源码定位
**阶段**：P0（文献与可行性，至 2026-11-08）
**投入**：墙钟 17:15—22:15，约 5 h；**有效投入待本人核实后记入 P0 的 30 人时预算**（《开发流程计划》规定第一步 6—8 人时，本次含环境搭建与两次返工）

## 1. 任务与完成情况（对应实施计划第一步 6 项）

| 编号 | 任务 | 结果 | 证据 |
|---|---|---|---|
| T1 | WSL 环境核对（GPU/编译器/CMake/CUDA/内存/磁盘） | 完成，10 项实测 | `docs/environment.md`、`results/raw/prestudy/2026-10-19-env-check/` |
| T2 | 获取并固定 llama.cpp 版本，按自带文档构建 | 完成：v0.7.0 / SHA `620d2225`；CUDA 构建 12.8 min | `results/raw/prestudy/2026-10-19-build/`、`third_party/README.md` |
| T3 | 选择并加载设备可容纳的 GGUF 模型 | 完成：Qwen2.5-1.5B-Instruct Q4_K_M，1,117,320,736 B，SHA256 校验一致 | `docs/environment.md`、同目录 `model-sha256.txt` |
| T4 | 发一条交互 + 一条后台流式请求，核对首内容/终态/长度 | 完成（脚本化 v2，另加 2 条预热与 1 条缓存探针） | `results/raw/prestudy/2026-10-19-smoke-v2/` |
| T5 | 按固定提交填写源码定位表 | 完成：7 条链路到文件:行号，15 个接入点 | `docs/source-map.md` |
| T6 | 保存环境、启动命令、服务端日志与请求记录，填写 manifest | 完成：4 个运行目录共 31 个文件 | `results/raw/prestudy/`、`results/README.md`（索引） |

## 2. 产出

| 编号 | 产出 | 位置 |
|---|---|---|
| O1 | 环境与版本记录（含构建记录、启动命令、9 条问题处置） | `docs/environment.md` |
| O2 | 源码定位表（7 链路 + 可观测清单 + 15 接入点 + 5 条待验证假设） | `docs/source-map.md` |
| O3 | 分步骤实施计划更新（第一步执行记录 + 第二步启动清单） | `docs/implementation-plan.md` |
| O4 | 流式计时客户端与冒烟驱动（逐块时间戳） | `scripts/replay/stream_client.py`、`20_run_smoke.py` |
| O5 | 一键编排与环境基线（replay）；同步、一致性检查、每日收尾（ops） | `scripts/replay/50_run_all.sh`、`00_env_check.sh`；`scripts/ops/{sync,compare,eod}.sh` |
| O6 | 冒烟 v2 正式报告 + 运行清单 + 18 份原始数据 | `results/raw/prestudy/2026-10-19-smoke-v2/` |
| O7 | 运行索引（4 个 prestudy 目录状态与取代关系） | `results/README.md` |
| O8 | 引擎固定版本记录（含回退预案） | `results/raw/prestudy/2026-10-19-build/clone-record.md` |
| O9 | 每日记录体系（本目录 + 索引 + 模板） | `daily/` |
| O10 | 一键推送脚本（WSL 借用 Windows SSH key，用完即删） | `scripts/ops/push.sh` |
| O11 | 项目任务流程图（Mermaid 三张：甘特时间线 / **单行**阶段推进（决策门标在箭头上）/ P0 当前阶段展开；横向紧凑布局并标注当前位置） | `README.md`「任务流程与当前进度」 |

## 3. 关键数据

> 权威值见 [环境记录](../../docs/environment.md) 与 [冒烟报告](../../results/raw/prestudy/2026-10-19-smoke-v2/README.md)；此处只摘当天最关键的数字，不复制完整表格。

- 环境：Ubuntu 24.04.5 / 内核 6.18.54.1；Ryzen 7 5800H 16 线程；7.7 GiB 内存；RTX 3050 Ti 4096 MiB；CUDA 12.6
- 显存：空闲 1031 MiB → 运行期 2285—2292 MiB（`llama-server` 峰值约 1.25 GiB）
- 引擎/模型：llama.cpp v0.7.0（SHA `620d2225…`，CUDA 构建 12.8 min）/ Qwen2.5-1.5B-Instruct Q4_K_M（SHA256 `6a1a2eb6…`）
- 冒烟 TTFT：交互 **26.48 ms**、同 prompt 命中缓存 **11.87 ms**、后台 **30.36 ms**（后台 312 token / 3.22 s，97.48 t/s）
- 其它：TTFT ≈ `prompt_ms` + 1.5—5.4 ms；逐 token 间隔 p50≈10 ms、p95 12—15 ms；`requests_deferred=0`（全程无并发）

## 4. 问题与处置

| 编号 | 现象 | 处置 | 教训/遗留 |
|---|---|---|---|
| B1 | 用户最初开的是 VMware 虚拟机，无 NVIDIA 直通 | 改用 WSL2（与开题报告 §6.1 一致） | 环境选择先看能否用 GPU |
| B2 | 体检脚本中 `!!!` 触发 bash 历史展开，整行未执行 | `set +H` 后补测 GPU/CUDA | 交互命令不用 `!` |
| B3 | 为跨系统读取把原始输出写桌面，桌面散乱 | 建 `~/proj-win` 软链接，日志直写仓库 `results/raw/` | 证据只进仓库 |
| B4 | HuggingFace 直连被阻断 | 模型改走 `hf-mirror.com` | 下载源需记录 |
| B5 | Windows git 直连 GitHub 失败，schannel 报 `SEC_E_NO_CREDENTIALS` | 用系统代理 `127.0.0.1:7897` + `http.sslBackend=openssl` | 克隆方式写入记录 |
| B6 | 内置 Web UI 资产因 HF 不可达未嵌入 | 只用 HTTP API，不影响实验 | 已登记为已知限制 |
| B7 | `curl ttfb` 被误当 TTFT（1.5 ms vs 实际 26—30 ms） | 写逐块时间戳客户端，TTFT = 首个非空内容 | 计时口径必须客户端实现 |
| B8 | v1 两条交互请求写同一 SSE 文件，第 1 条被覆盖 | v2 每条请求独立 JSON + `client.jsonl` | 运行目录按 run_id 隔离 |
| B9 | 首请求受冷启动影响（运行内 82.06 → 26.87 ms） | 协议固定 ≥2 条预热且不计入样本 | 受控测量留到 P3 |
| B10 | WSL 副本 `git status` 一片"假脏" | 诊断确认是 CRLF 假象；WSL 改为纯工作副本，新增 `.gitattributes` 强制脚本 LF | 双副本不给两套 git |
| B11 | 本次自查发现 2 处双副本不一致（`.gitattributes`、新脚本未同步） | 扩充同步脚本范围（现 `scripts/ops/sync.sh`），补齐后复查（现 `scripts/ops/compare.sh`） | 改完文档/脚本必须同步 |
| B12 | 我自己写的客户端 `ttfb` 单位 bug（秒当毫秒） | 修正后丢弃该轮数据重跑 | 自动化跑的数据也要自检量纲 |

## 5. 决策与变更

| 决策 | 理由 | 影响 |
|---|---|---|
| 实验环境定为 WSL2（ABI Ubuntu 24.04） | VMware 无 GPU 直通；与开题报告 §6.1 冻结值一致 | 放弃 VMware 方案，不改论文环境章节 |
| 引擎固定 llama.cpp **v0.7.0**（tag + 完整 SHA） | 可复现；新版本发布仅 4 天，记录回退 v0.6.0 预案 | 升级须重建基线 |
| 模型选 Qwen2.5-1.5B-Instruct **Q4_K_M** | 4 GiB 显存（可用 3.4 GiB）内留足 KV 余量；Apache-2.0；中文 prompt 友好 | 候选模型，**G0 冻结前仍可评估 3B** |
| 双副本分工：Windows=主仓库（文档/脚本/证据/git），WSL=纯工作副本（源码/构建/模型） | Linux 原生盘构建才可靠；文档与证据需可进 git、可被 Windows 侧编辑 | 单向同步 + 一致性检查脚本化 |
| 实验协议前置约定：≥2 条预热、逐块时间戳、每条请求独立文件、记录 `cache_n/prompt_n` | 首轮实测证明这四项会显著改变 TTFT 与可比性 | 写入第二步回放器需求 |
| v1 降级为"冷态对照"，v2 为正式证据 | v1 有口径缺陷与文件覆盖 | v1 仅保留 `server.log` |

## 6. 下一步

- [x] 用户复查后提交 git —— 已提交 `da39428`（`feat(P0): 完成第一步——环境冻结、引擎构建、脚本化冒烟与源码定位`，45 个文件；证据、引擎检出、模型与 docx 按 `.gitignore` 不入库），并已推送到 GitHub `origin/main`（`e28f696..da39428`）
- [x] 每日收尾走 `scripts/ops/eod.sh`（自动清理 + 生成 `daily/<日期>/产出清单.md` + 同步 + 一致性检查）
- [ ] 第二步：给 `stream_client.py` 加**开放到达**（计划到达表 + 单调时钟 + 发送滞后记录）
- [ ] 先跑 10 条小轨迹核对（ID、发送滞后、首内容、终态、排空），再跑 E0（200 交互 × 3）与 E1（+约 100 后台）
- [ ] 人时核实并填回《开发流程计划》P0 预算
- [ ] （P3 时）预热条数的受控测量

## 7. 收尾检查（`scripts/ops/eod.sh` 已跑）

- [x] `daily/20261019/产出清单.md` 已生成（当天全部新增/修改的受管文件，自动扫描）
- [x] 10 个受管目录 Windows↔WSL 逐字节一致；同步脚本同时清除了 WSL 中已删除的旧脚本（`98_compare.sh`、`99_sync.sh`）
- [x] 临时文件与空目录：无（eod 自动扫描）
- [x] 无残留 `llama-server`、显存已释放
- [x] 本文件六块内容已填，`daily/README.md` 索引已有当天行
- [ ] **git 提交待做**（见 §6 第一项）

## 附：环境与运行

- 工作区：`C:\Users\1\Desktop\面向混合长短请求的 LLM 推理准入与尾延迟控制系统设计与实现`（主仓库）
- WSL 副本：`~/projects/llm-admission-tail-latency-control`（无 `.git`）；`~/proj-win` 指向主仓库
- 启动：`scripts/replay/50_run_all.sh`（服务 `-ngl 99 -c 4096 -np 2 -b 2048 -ub 512 --metrics`，监听 127.0.0.1:8080）
- 客户端：Python 3.12.3，`time.monotonic()` 逐块计时
- 已知限制：内置 Web UI 未嵌入；模型加载有 `control-looking token` 告警（Qwen2.5 已知现象）；无并发实验
