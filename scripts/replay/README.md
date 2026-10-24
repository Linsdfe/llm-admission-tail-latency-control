# 流式请求回放与计时

## 2026-10-24 P1开发验证入口

本轮使用新原生`/completion`采集器和显式ChatML，旧冻结工具不改。新[执行前协议](../../docs/p1/development-validation-protocol-v1.md)与[九轮报告](../../docs/experiments/20261024-025138-p1-development-validation-v1/README.md)区分开发验证和正式M/B3对照。

- `p1_workload.py`：只生成校准/开发提示，拒绝最终测试；实际`/tokenize`核长。
- `p1_development_replay.py`：计划到达期限、传输兜底、结构化容量拒绝、原生最终计数、未知token及异步写盘。
- `tests/integration/verify_p1_development_replay.py`：先在新目录执行无模型门禁，检查源码SHA后才能采集。
- `79_run_p1_development.py --client-gate-dir <通过门禁的新目录> --engine-bin <固定引擎> --out-dir <全新目录>`：准备/校准后执行9轮单策略开发；保存源码快照、逐请求、GPU与真实释放。
- `scripts/analysis/90_analyze_p1_development.py --run-dir <原始批次> --out-dir <全新分析目录>`：从逐请求重算，速率分母至少100秒。
- `scripts/analysis/91_check_p1_prediction_headroom.py --run-dir <原始批次> --out-dir <全新诊断目录>`：临时校准空间和长短分界诊断，不模拟或证明调度收益。

实际批次为`20261024-025138-p1-development-validation-v1`；2010测量、24校准、20预热全部完成，最终数据未触碰。下文保留旧日期工具及其历史口径。

P0 首先实现最小流式客户端与自动化冒烟，再扩展 prestudy-E0/E1 的开放到达回放。按计划到达发送，分别记录计划、释放、实际发送、首个生成内容和客户端终态；每条请求具有唯一 ID 和明确类别。

首内容排除心跳、元数据和空块。记录实际生成长度、结束原因、发送滞后、测量窗口与排空截止。有闸门时，尚未释放请求也跟踪到截止。2026-10-21 已将根目录旧《预实验方案》融合到本文、[复审设计](../../docs/p0-experiment-review-minimal-supplement-20261021.md)和版本化执行协议，避免继续维护重复规划。原文保存在 Git 历史及当日维护证据包。

## 当前入口（2026-10-21）

- 客户端核对：`tests/integration/verify_open_loop.py --out-dir <新目录>`。
- 低强度补充：`75_run_lowload.py --gate-dir <本次通过的核对目录> --out-dir <新批次>`，配置 `configs/prestudy-lowload-v1.json`，协议见 [低强度执行协议 v1](../../docs/prestudy-lowload-protocol-v1.md)。
- 低强度审计：`scripts/analysis/85_analyze_lowload.py --batch <完整批次> --output <新报告目录>`。
- 原 E0/E1 复现入口仍为 `70_run_prestudy.py` 与 `80_analyze_prestudy.py`，按 [原冻结协议](../../docs/prestudy-protocol.md)使用；不修改旧配置适配新实验。

## 简单 C++ 策略对照（2026-10-23 增补）

- v2 客户端门禁：`tests/integration/verify_simple_policy_client_v2.py --out-dir <新目录>`，含人工慢盘、六终态、取消竞争和总等待核对。
- v2 真实引擎门禁：`tests/integration/verify_simple_policy_engine_v2.py --out-dir <新目录> --engine-bin <独立修改后llama-server>`。
- 批次入口：`77_run_simple_policy_v2.py --config configs/prestudy-simple-policy-v2.json --gate-dir <通过的v2客户端门禁> --engine-gate-dir <通过的真实门禁> --out-dir <新批次> --engine-bin <同一修改后llama-server>`。在 WSL 中从仓库根目录调用 `python3 scripts/replay/77_run_simple_policy_v2.py ...`，保持调用会话直到退出；本次后台 launcher 未持续运行的尝试另有[维护留痕](../../results/raw/maintenance/20261023-v2-launch-transition/README.md)，不能只凭 PID 宣告采集成功。
- 只读分析：`scripts/analysis/87_analyze_simple_policy_v2.py --batch <完整12轮> --output <新输出目录>`；不覆盖历史报告。

v2 仅将每请求 JSON/JSONL 写盘移出异步事件循环，终态与结束时间先记，最后等持久化排空；所有写入数必须闭合。完整性能协议见 [v2](../../docs/prestudy-simple-policy-protocol-v2.md)，数据见[本批分析](../../docs/experiments/20261023-100000-p0-simple-policy-v2/README.md)。v1 的 76/replay/86 与两批中断或暂停数据保留；负面结果不删，不与 v2 拼配对。原始引擎历史实验仍使用各自冻结源码与快照复核，不能把新客户端替换进去重写旧结果。

## 后台并发上限对照（2026-10-23 下午增补）

- 独立构建与 28 项模型无关断言：[background-cap-v1](../../patches/background-cap-v1/README.md)。使用独立引擎目录，保留原始与简单原型，不覆盖历史 binary。
- 客户端门禁仍为 `tests/integration/verify_simple_policy_client_v2.py --out-dir <新目录>`；新真实引擎门禁为 `tests/integration/verify_background_cap_engine.py --out-dir <新目录> --engine-bin <新独立引擎>`，须与主批次绑定同一模型、源码和运行库指纹。
- 批次入口：`python3 scripts/replay/78_run_background_cap.py --config configs/prestudy-background-cap-v1.json --gate-dir <本次通过的客户端门禁> --engine-gate-dir <本次通过的真实门禁> --out-dir <新批次> --engine-bin <同一新独立引擎>`。在 WSL 仓库根目录执行，保持调用会话到退出。
- 只读分析：`python3 scripts/analysis/88_analyze_background_cap.py --batch <完整18轮> --output <新报告目录>`；不覆盖旧输出。

本批用同一新 binary 比较 original/simple/capped，主对比是加后台上限相对 simple 的增量；两种到达形状各三个三策略块，18 轮 4140 条测量、36 条预热另计。全部重新采集，不混上午 12 轮；当前进展见[工作记录](../../docs/reports/20261023/后台并发上限验证与复杂度选择.md)，判定与停止规则见[冻结协议](../../docs/prestudy-background-cap-protocol-v1.md)。original 包含三策略共用的取消正确性修复，不冒称完全未改的官方引擎。该矩阵不含复杂算法。

### 通用字段与可选探针（由旧方案合并）

计划到达 `t_planned`、闸门释放 `t_queue_release`、实际发送 `t_actual`、首非空内容 `t_client_first_content`、终态 `t_end_client` 均注明时钟和单位。TTFT=首内容−实际发送；迟发=实际发送−释放；策略等待=释放−计划。后台完成代价从计划到达算，尚未释放也跟踪至统一截止。无闸门按窗口内实际发送核对分母，有闸门按计划请求并包含未释放者。

本次异步客户端 actual-send 边界在 TCP 建连后、HTTP 写入前；旧 urllib 冒烟边界不同，不作严格性能对照。服务端 prompt 开始、首 token 产生及 slot/KV 字段尚未完整记录，不能跨未验证时钟相减；字段未知就标未知。各段 P95 不能相加。

E2 等输入/输出总量的更多短请求仍改变请求数与每请求开销；E3 保持数量、到达及预设总预算而改变长度离散度；E4 需要同请求阶段观测；E5 可试外部在途上限或推迟释放，但必须报告后台等待和全部终态。只按信息缺口选探针，等 token 不等于等计算量，外部闸门不证明内部软预算收益。业务类别预先指定，不按事后实际长度重分类。

## 历史冒烟实现（2026-10-19，仅复现旧链路）

| 文件 | 作用 |
|---|---|
| `stream_client.py` | 单条流式请求客户端：逐块 `time.monotonic()` 时间戳，输出 TTFT / ttfb / 总时长 / 逐 token 间隔 / 服务端 timings / 生成内容；仅用标准库 |
| `20_run_smoke.py` | 冒烟驱动：固定 5 条请求（2 预热 + 交互 + 缓存探针 + 后台），每条独立 JSON + `client.jsonl` |
| `50_run_all.sh` | 编排：调用 `scripts/ops/sync.sh` 同步 → 起 `llama-server` → 健康检查 → `/props`/GPU 快照与 0.25 s 采样 → 跑请求 → `/metrics` → 停服务 → 写 `run-meta.txt` |
| `00_env_check.sh` | 运行前环境核对（仓库、二进制、模型 SHA256、Python、内存、显存基线） |

> 仓库同步、一致性检查、每日收尾属于仓库运维，已独立到 `scripts/ops/`（`sync.sh`、`compare.sh`、`eod.sh`），见 `scripts/ops/README.md`。

以下旧脚本固定使用历史目录；这些目录已有证据，**不要再次直接运行覆盖旧记录**。新实验使用上方新建目录的入口。历史命令仅供理解：

```bash
wsl.exe -d Ubuntu-24.04 -e bash "<Windows 仓库>/scripts/replay/00_env_check.sh"   # 环境基线
wsl.exe -d Ubuntu-24.04 -e bash "<Windows 仓库>/scripts/replay/50_run_all.sh"     # 一键冒烟（内部调用 ops/sync.sh）
```

产物目录：`results/raw/prestudy/2026-10-19-smoke-v2/`（协议、结果与分析见该目录 `README.md`）。

## 计时口径（已在本机实测校验）

| 字段 | 定义 | 实测对照 |
|---|---|---|
| `ttfb_ms` | 收到响应头/首字节（≈ `curl -w time_starttransfer`） | 1.6—5.5 ms，**不可当作 TTFT** |
| `ttft_ms` | 收到首个**非空生成内容** | 交互热态 26.5 ms；同 prompt 命中缓存 11.9 ms |
| `prompt_ms` | 服务端 prefill 计时，起点是 slot 拿到任务之后 | 10.4—58.1 ms；**不含排队** |
| `total_ms` | 收到 `[DONE]` 的时间 | 交互 0.32 s，后台 3.22 s |
| `inter_token_ms` | 相邻内容块到达间隔（不含首块） | p50 ≈ 10 ms，p95 11—15 ms |

## 原 E0/E1 与小轨迹工具

### 第七、八步入口

`open_loop.py` 提供标准库 asyncio HTTP/SSE 开放到达回放，支持请求总超时、显式取消、统一排空截止、未释放计数与六类互斥终态；通过异步连接独立发送，不使用阻塞线程来实现截止。

1. `tests/integration/verify_open_loop.py --out-dir <新目录>`：可控 HTTP/SSE 终态测试、完成/取消竞争和发送能力校准。
2. `70_run_prestudy.py --step7-dir <通过的测试目录> --out-dir <新批次目录>`：先做真实引擎取消/超时/截止/恢复，再按 `configs/prestudy-e0e1.json` 执行三组配对。每轮新引擎、两条预热；记录所有状态，不因结果不好而删数据。
3. `scripts/analysis/80_analyze_prestudy.py --batch <完成的批次> --output <新分析目录>`：审计轨迹、请求文件、终态、计时及版本，输出逐轮表、配对差异、后台代价和可追溯摘要。

完整协议见 [docs/prestudy-protocol.md](../../docs/prestudy-protocol.md)。第七步只验证客户端生命周期与服务恢复，不能据此宣称已实现引擎内部容量硬账本或 KV 幂等回收。迟发上限在采集前固定为 50 ms，超限保留并标记；第八步是探索性预实验，三次重复不承担正式显著性判断。

### 2026-10-20 小轨迹重跑入口

已有合成轨迹时，在 WSL 运行 `bash "$HOME/proj-win/scripts/replay/60_run_sanity.sh"`。它在独立的 `results/raw/prestudy/<时间>-sanity-rerun-<PID>/` 中运行调度干跑、重启原始引擎、执行 2 条预热与 10 条混合请求，再调用 `scripts/analysis/10_check_sanity.py` 生成逐请求 Markdown 报告、`summary.json` 和 `manifest.json`。清理只停止本次启动的服务，不删除历史运行目录。

报告入口为运行目录的 `README.md`；`dry-run/` 是模拟记录，`http/` 是真实流式请求、日志与配置。旧小轨迹工具仍限最多 10 条，不适配完整终态矩阵。完整终态与统一排空已经由 `open_loop.py` 和受控测试验收，200 条实验应使用上面的版本化入口。`length` 表示达到输出上限；并发下 `server_overhead_ms` 含排队等时间，不能解释成纯交付开销。
