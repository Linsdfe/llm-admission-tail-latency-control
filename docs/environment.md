# 环境与版本记录

状态：P0 第一步已于 2026-10-19 完成（环境体检、引擎构建、模型加载、两条流式请求冒烟、源码定位）。硬件历史采集值见根目录开题报告 §6.1；本文件记录本轮实测值，原始输出归档于 `results/raw/prestudy/`。

| 项目 | 本轮值 |
|---|---|
| 记录日期 / 操作者 | 2026-10-19 17:22—17:36 / aiinfra（学生本人） |
| WSL 发行版 / 内核 / 仓库路径 | Ubuntu 24.04.5 LTS (noble)；内核 6.18.54.1-microsoft-standard-WSL2；Windows 工作区 `C:\Users\1\Desktop\面向混合长短请求的 LLM 推理准入与尾延迟控制系统设计与实现`（版本库与提交以此为准）；WSL 工作副本 `~/projects/llm-admission-tail-latency-control`（**无 `.git`，纯构建/运行工作副本**，由 `scripts/ops/sync.sh` 单向同步） |
| CPU / 内存 / swap / 可用磁盘 | AMD Ryzen 7 5800H，`nproc=16`；内存 7.7 GiB，swap 2.0 GiB；根分区 `/dev/sdd` 1007 GiB，可用 933 GiB |
| GPU / 驱动 / CUDA / 可用显存 | RTX 3050 Ti Laptop 4096 MiB；Windows 驱动 561.17，WSL 内 nvidia-smi 报 NVIDIA-SMI 560.35.04 / CUDA 12.6；`nvcc` release 12.6, V12.6.85；`/usr/lib/wsl/lib/libcuda.so` 由 WSL 注入。**占用基线：17:26 空闲 1143 MiB（可用≈2.9 GiB）；17:41 关闭浏览器与 VMware 虚拟机后 652 MiB（可用≈3.4 GiB）** |
| C++ 编译器 / CMake / Python | g++ 13.3.0（Ubuntu 13.3.0-6ubuntu2~24.04.1）；CMake 3.28.3；GNU Make 4.3；Python 3.12.3，pip 24.0 |
| 版本控制 / 下载工具 | git 2.43.0；curl、wget 可用 |
| llama.cpp 来源 / 完整 SHA | `https://github.com/ggml-org/llama.cpp`，tag **v0.7.0**，SHA **`620d2225cc436ab0b001e3200e590e3b286ef9ab`**；`git clone --depth 1 --branch v0.7.0` 浅克隆（提交日期 2026-10-15）；Windows 侧克隆后拷入 WSL，记录见 `results/raw/prestudy/2026-10-19-build/clone-record.md` |
| 模型来源 / 许可证 / 量化 | `Qwen/Qwen2.5-1.5B-Instruct-GGUF`（HuggingFace，经 `hf-mirror.com` 镜像下载），许可证 **Apache-2.0**；量化 **Q4_K_M**，GGUF 格式，qwen2 架构，模型自带上限 ctx 8192；文件 1.1 GiB（1,117,320,736 字节）。下载 2026-10-19 19:46:53—19:47:47，平均 19.4 MB/s。候选模型，G0 冻结前待定 |
| 模型文件 SHA256 / 分词器摘要 | 文件 SHA256：**`6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e`**（与 HF 发布的 LFS 指针值一致；证据 `results/raw/prestudy/2026-10-19-build/model-sha256.txt`）；分词器随 GGUF 内嵌（Qwen2 BPE），**由模型文件 SHA256 唯一确定**；`/props` 已留档模型类型 `Q4_K - Medium`、chat 模板与引擎默认采样参数（证据 `results/raw/prestudy/2026-10-19-smoke-v2/props.json`） |
| ctx / parallel / GPU 卸载 / 缓存 / 采样 | 冒烟 v2 配置：`-ngl 99`（全部卸载）、`-c 4096`、`-np 2` → 日志核实 `n_slots = 2 / n_ctx_slot = 2048 / kv_unified = false`；`-b 2048 / -ub 512`；`n_threads = 8`；请求显式 `temperature = 0`（贪心，未设 seed；引擎默认 0.8 见 `props.json`）；prompt cache 默认开启（前缀相似度阈值 0.1），chat 模板 system 前缀约 24 token 恒定命中。显存：空闲基线 1031 MiB，运行期 2285—2292 MiB，`llama-server` 峰值约 1.25 GiB |
| 宿主竞争 / 供电 / 温度 | 2026-10-19 17:41 检查：宿主可用内存约 7.9 GiB；GPU 常驻占用 652 MiB，来源为 DSH Desktop（含 msedgewebview2）与 msedge 后台进程；VMware 虚拟机已关闭。**20:15 冒烟 v2 前基线：GPU 占用 1031 MiB、利用率 0%，WSL 内无残留 `llama-server`；运行期 2285—2292 MiB、GPU 利用率 85—90%。** 正式实验须在每次运行记录当时的宿主进程、显存基线、供电模式与温度 |

## 网络与运行前提

| 现象 | 证据（2026-10-19） | 处置 |
|---|---|---|
| WSL 启动提示“检测到 localhost 代理配置，但未镜像到 WSL” | 终端启动输出 | WSL 为 NAT 模式，不使用 Windows localhost 代理；当前不影响构建与推理 |
| `https://huggingface.co` 直连失败 | `curl` 返回 HTTP 000 | 模型改用 `https://hf-mirror.com` 镜像下载 |
| `https://github.com` 直连可用 | `curl` 返回 HTTP 200 | 引擎源码直接从 GitHub 克隆 |
| Windows 侧 `git` 直连 GitHub 失败；经系统代理 127.0.0.1:7897 且需 `http.sslBackend=openssl` 才成功 | 克隆日志 | 引擎改为 Windows 克隆后拷入 WSL；代理属本机环境，不写入仓库配置 |
| `https://hf-mirror.com`、`https://pypi.org` 可用 | HTTP 200 | — |
| 宿主同时运行 VMware 虚拟机 | 桌面/服务检查 | 正式实验前关闭，避免与 WSL 抢 CPU 与内存（宿主仅 16 GiB） |

## 复现命令

记录约定：WSL 侧原始输出**直接写入本仓库的 `results/raw/`**，不在桌面或其他临时目录留副本。`~/proj-win` 是指向 Windows 工作区仓库的软链接，用于缩短跨系统路径：

```bash
ln -sfn "/mnt/c/Users/1/Desktop/面向混合长短请求的 LLM 推理准入与尾延迟控制系统设计与实现" ~/proj-win
```

环境核对（原始输出见 `results/raw/prestudy/2026-10-19-env-check/`）：

```bash
set +H
nvidia-smi 2>&1 | tee ~/proj-win/results/raw/prestudy/2026-10-19-env-check/gpu-check.txt
nvcc --version 2>&1 | tee -a ~/proj-win/results/raw/prestudy/2026-10-19-env-check/gpu-check.txt
git ls-remote --tags --refs https://github.com/ggml-org/llama.cpp.git | awk -F/ '{print $NF}' | sort -V | tail -12
```

### 构建记录（2026-10-19，llama.cpp v0.7.0）

源码：`~/projects/llm-admission-tail-latency-control/third_party/llama.cpp`（SHA `620d2225cc436ab0b001e3200e590e3b286ef9ab`）

```bash
cd ~/projects/llm-admission-tail-latency-control/third_party/llama.cpp
sudo apt update && sudo apt install -y libcurl4-openssl-dev
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86 -DCMAKE_BUILD_TYPE=Release
cmake --build build -j4 --target llama-server llama-cli llama-bench
```

| 项目 | 结果 |
|---|---|
| CMake 生成 | 10.1 s；`CMAKE_CUDA_ARCHITECTURES=86`（8.6 算力）；CUDA Toolkit 12.6.85；host compiler GNU 13.3.0；OpenMP 4.5；OpenSSL 3.0.13 |
| 构建耗时 | **12.8 min**（19:27:06 → 19:39:55），`-j4` |
| 产物 | `build/bin/llama-server`、`build/bin/llama-cli`、`build/bin/llama-bench`；共享库 `libllama.so`、`libggml.so`、`libggml-cuda.so`、`libggml-cpu.so`、`libllama-server-impl.so` 等 |
| 编译错误 | 无。日志中 2 条 `error` 均为内置 Web UI 资产下载失败，非编译错误 |
| 已知限制 | ① **内置 Web UI 未嵌入**：`huggingface.co` 不可达导致 UI 资产下载失败，`llama-server` 无内置前端页面；实验只用 HTTP API，不影响；② NCCL 未找到：单 GPU 无影响 |
| 日志 | `results/raw/prestudy/2026-10-19-build/cmake.log`、`build.log`（441 行，20 个 target 全部 built） |

启动与请求命令（由脚本编排，证据见 `results/raw/prestudy/2026-10-19-smoke-v2/`）：

```bash
# 在 Windows 侧执行；脚本会把 docs/ 与 scripts/ 同步到 WSL 副本，然后起服务、预热、测量、停服务
wsl.exe -d Ubuntu-24.04 -e bash "<Windows 仓库路径>/scripts/replay/50_run_all.sh"
```

等价的单次手工步骤（供核对）：先以 `-ngl 99 -c 4096 -np 2 -b 2048 -ub 512 --metrics` 启动 `llama-server`，`/health` 返回 `{"status":"ok"}` 后，用 `scripts/replay/20_run_smoke.py --out-dir <run_dir>` 发请求（逐块计时、每条请求独立 JSON），最后 Ctrl+C 停止。

**预热要求（v2 实测）**：无预热时首次请求的 prompt 处理是同配置热态的 4.8 倍（277.79 ms vs 58.09 ms，同为 36 token）；TTFT 从 82.06 ms 降到 26.87 ms 需要 2 条预热。正式运行固定 ≥2 条预热请求且不计入样本。

## 问题与处置

1. 2026-10-19 首次体检脚本中 `!!!` 触发 bash 历史展开（`-bash: !!: event not found`），`nvidia-smi` 与 `nvcc --version` 两行整行未执行。处置：先 `set +H` 关闭历史展开后补测，结果见 `gpu-check.txt`；后续交互命令不使用 `!`。原 `env-check.txt` 保留不覆盖。
2. 长命令块粘贴到 Windows Terminal 时终端回显错乱（实际执行结果正确）。处置：后续分块执行，必要时写成脚本再运行。
3. 首轮体检为便于 Windows 侧读取，原始输出曾 `tee` 到桌面（`env-check.txt`、`gpu-check.txt`、`llama-tags.txt`）。已归档到 `results/raw/prestudy/2026-10-19-env-check/` 并删除桌面副本；后续统一通过 `~/proj-win` 软链接直接写入仓库 `results/raw/`。桌面散落文件不作为证据来源。
4. 首轮冒烟结果中，`curl` 的 `ttfb` 不可用作 TTFT：实测 1.5—8.3 ms，而服务端 `prompt_ms` 为 164—278 ms；`time_starttransfer` 只标记 SSE 首字节（响应头）。端到端 TTFT 必须在客户端对首个非空内容块打单调时钟时间戳，列入第二步回放器需求。
5. 首轮冒烟第 1 条交互请求的 SSE 被第 2 条（相同 prompt）覆盖：`interactive.sse` 只剩第 2 条，第 1 条数据仅存于 `server.log`（task 0）。后续运行按 run_id 分目录、每条请求单独文件。v1 的 SSE 等派生文件已于 20:30 清理（保留 `server.log` 与说明，见 `results/raw/prestudy/2026-10-19-smoke/README.md`）。
6. 模型加载日志出现 `control-looking token: 128247 '</s>' was not control-type` 告警，属 Qwen2.5 GGUF 已知现象，输出正常，记录备查。内置 Web UI 资产因 `huggingface.co` 不可达未嵌入，不影响 HTTP API。
7. 冷启动代价：未预热的首次请求 prompt eval 277.79 ms（36 token，7.72 ms/token），v2 首次请求 58.09 ms（同 36 token，4.8 倍）。该**跨运行差异为混合因素**（CUDA 图/算子首次调用、GPU 时钟爬升、模型页缓存状态），未做受控分离；运行内受控证据是预热第 1 次 TTFT 82.06 ms → 第 2 次 26.87 ms 后进入稳态（26—30 ms）。处置：实验协议固定 ≥2 条预热且不计入样本；预热效果在 P3 基线阶段做受控测量。证据 `results/raw/prestudy/2026-10-19-smoke-v2/README.md` §3.1。
8. 第 4 条的 `ttfb` 口径问题已在 v2 解决：客户端 `scripts/replay/stream_client.py` 用 `time.monotonic()` 对首个非空内容块打时间戳；实测首个内容位于**第 2 个 SSE JSON 块**（index=1，第 1 块为 role 元数据），`ttfb` 与 TTFT 相差一个数量级。
9. 第 5 条的覆盖问题已解决：v2 起每条请求独立 JSON（`requests/R0*.json`）+ `client.jsonl` 汇总，并由 `50_run_all.sh` 统一编排（环境核对、预热、GPU 0.25 s 采样、`/props`、`/metrics`、停服务）。v1 仅保留 `server.log` 作“无预热冷态”对照，其余派生文件已清理并记录原因；同时新增 `.gitattributes` 统一脚本为 LF，避免 Windows 检出（CRLF）与 Linux 运行混用。
