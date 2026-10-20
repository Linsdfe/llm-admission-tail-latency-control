# 流式请求回放与计时

P0 首先实现最小流式客户端与自动化冒烟，再扩展 prestudy-E0/E1 的开放到达回放。按计划到达发送，分别记录计划、释放、实际发送、首个生成内容和客户端终态；每条请求具有唯一 ID 和明确类别。

首内容排除心跳、元数据和空块（实测首位内容在第 2 个 SSE JSON 块）。记录实际生成长度、结束原因、发送滞后、测量窗口与排空截止。有闸门时，尚未释放请求也跟踪到截止。字段口径见根目录《预实验方案》。

## 当前实现（2026-10-19）

| 文件 | 作用 |
|---|---|
| `stream_client.py` | 单条流式请求客户端：逐块 `time.monotonic()` 时间戳，输出 TTFT / ttfb / 总时长 / 逐 token 间隔 / 服务端 timings / 生成内容；仅用标准库 |
| `20_run_smoke.py` | 冒烟驱动：固定 5 条请求（2 预热 + 交互 + 缓存探针 + 后台），每条独立 JSON + `client.jsonl` |
| `50_run_all.sh` | 编排：调用 `scripts/ops/sync.sh` 同步 → 起 `llama-server` → 健康检查 → `/props`/GPU 快照与 0.25 s 采样 → 跑请求 → `/metrics` → 停服务 → 写 `run-meta.txt` |
| `00_env_check.sh` | 运行前环境核对（仓库、二进制、模型 SHA256、Python、内存、显存基线） |

> 仓库同步、一致性检查、每日收尾属于仓库运维，已独立到 `scripts/ops/`（`sync.sh`、`compare.sh`、`eod.sh`），见 `scripts/ops/README.md`。

用法（在 WSL 内，证据直接写入 Windows 工作区仓库）：

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

## 下一步（prestudy-E0/E1）

### 2026-10-20 小轨迹重跑入口

已有合成轨迹时，在 WSL 运行 `bash "$HOME/proj-win/scripts/replay/60_run_sanity.sh"`。它在独立的 `results/raw/prestudy/<时间>-sanity-rerun-<PID>/` 中运行调度干跑、重启原始引擎、执行 2 条预热与 10 条混合请求，再调用 `scripts/analysis/10_check_sanity.py` 生成逐请求 Markdown 报告、`summary.json` 和 `manifest.json`。清理只停止本次启动的服务，不删除历史运行目录。

报告入口为运行目录的 `README.md`；`dry-run/` 是模拟记录，`http/` 是真实流式请求、日志与配置。真实回放脚本仍限定最多 10 条，异常终态分类和统一排空截止尚未验收，不能直接作为 200 条正式实验入口。`length` 表示达到输出上限；并发下 `server_overhead_ms` 含排队等时间，不能解释成纯交付开销。

### 正式预实验前仍需完成

1. 计划到达表 + 开放到达循环（目标发送时刻用单调时钟累加，记录发送滞后）。
2. 每重复前重置服务端状态（重启 `llama-server` 或显式清理 slot），固定预热条数（≥2）。
3. 逐请求记录：计划/释放/实际发送/首内容/终态、`cache_n`/`prompt_n`、实际输出长度与结束原因。
4. 终态互斥分类（完成/拒绝/超时/取消/失败/未完成）与统一排空截止。
5. 先 10 条小轨迹核对，再跑 E0（200 交互 × 3 重复）与 E1（同轨迹 + 约 100 后台）。
