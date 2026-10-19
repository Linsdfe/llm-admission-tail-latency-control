# 固定引擎源码定位

引擎：llama.cpp，版本 tag **v0.7.0**，完整提交 SHA **`620d2225cc436ab0b001e3200e590e3b286ef9ab`**（提交日期 2026-10-15）。

- Windows 副本（源码检索用）：`third_party/llama.cpp/`
- WSL 副本（编译与实验用）：`~/projects/llm-admission-tail-latency-control/third_party/llama.cpp/`
- 版本记录：`results/raw/prestudy/2026-10-19-build/clone-record.md`

本表路径（`.cpp/.h`）相对 `third_party/llama.cpp/`；行号基于上述 SHA 的快照。

行号与符号均基于上述 SHA 的 `v0.7.0` 快照；升级引擎版本必须重新核对本表。`v0.7.0` 的 `tools/server/` 已被上游拆分为多文件模块（`server.cpp` / `server-http.cpp` / `server-queue.cpp` / `server-task.cpp` / `server-context.cpp` 等），`server-context.cpp` 共 6313 行。

**检索与核对方式**：2026-10-19 只读检索（grep 定位 + 局部阅读），未修改任何引擎文件。已抽查复核的条目见 §7。

## 1. 七条链路总表

| 链路 | 文件 / 符号 | 已有行为与证据 | 拟修改部分 / 待验证假设 |
|---|---|---|---|
| 请求接入与身份关联 | `tools/server/server.cpp:273-274`（路由）；`tools/server/server-context.cpp:5557`（lambda）、`:4883`（`handle_completions_impl`）、`:4932-4941`（建 task） | 路由→lambda→`handle_completions_impl`；先渲染 chat 模板再 tokenize，每个 prompt 建一个 `server_task`；身份为队列自增 `int id` 与服务端生成的 `chatcmpl-…` | 需新增类别与语义参数；到达时刻无记录，须在此插桩 |
| 等待队列与候选选择 | `tools/server/server-queue.cpp:28`（post）、`:76`（defer）、`:138`（process_new_tasks）、`:278`（start_loop）、`:90-113`（pop_deferred_task） | 两个**无上限** `std::deque`（`server-queue.h:24-25`），单线程主循环 FIFO 取队头；无 slot 即 defer；**无优先级、无分组、无容量上限** | 唯一入队/出队收口，准入与老化策略应在此实现；deferred 的 slot 亲和重排是最易饿死处 |
| slot 与容量约束 | `common/arg.cpp:2548-2556`（`-np`）；`src/llama-context.cpp:377-393`（`n_ctx_seq` 切分）；`server-context.cpp:4652-4660`（`n_ctx_slot`）、`:1429-1437`（slot 初始化）、`:1392`（启动日志） | slot 数=`--parallel`；每 slot 上下文=`n_ctx/n_seq_max`（`kv_unified` 时共享 `n_ctx`），256 对齐后受 `kv_unified_per_slot`、`n_ctx_train` 夹逼；启动期 KV 分配失败直接退出 | 硬账本需在此之后自行实现；逻辑容量与实际显存占用须分别观测（§5 风险） |
| prefill / decode 迭代 | `server-context.cpp:3256`（`update_slots`）、`:3370`（`pre_decode`）、`:4250` 与 `:4285`（`decode`→`llama_process`）、`:4056-4124`（prompt 填充 while） | 主循环组 batch → 按 `n_batch` 分片 decode → `post_decode` 采样；同一 batch 混合“已生成 slot 的下一 token”与“待 prefill slot 的前缀”；长 prompt 隐式分块 | R08 动态 prefill 预算的控制点即 `:3580-3584` 的批预算与 `:4056` 的填充循环；服务机会以 decode 步为计数单位 |
| 取消 / 超时 / 完成 | `tools/server/server-http.cpp:685`、`:736`（`is_connection_closed`）；`server-queue.cpp:550`（polling）、`:602-617`（`stop()`→CANCEL）；`server-context.cpp:2901-2910`（CANCEL 处理）、`:2085-2090`（`t_max_predict_ms`） | 断开靠 httplib 连接状态，1 s 轮询；`~server_response_reader()` 自动 stop 投 CANCEL；**无排队/预填超时**，唯一时间限制是生成期 `t_max_predict_ms` 且要求已出现换行 | 超时、拒绝与外部闸门等待必须自行实现；取消与完成的竞争需专项测试 |
| 序列清理 / slot 复用 | `server-context.cpp:586-608`（`release`）、`:343-349`（`prompt_clear`）、`:1444-1446`（`callback_on_release`）；`common/common.cpp:1638-1643`；`src/llama-context.cpp:4393-4403` | 结束后 `state=IDLE`；**仅子任务或无状态任务清 KV**，普通任务保留 KV 以复用 prompt cache（`get_available_slot` 的 LCP 相似度） | 一次性归还账本必须以 `release` 为唯一收尾口；`prompt_clear` 与 KV 保留语义影响“逻辑释放≠显存返还”的论证 |
| 流式输出 / 事件观测 | `server-context.cpp:4468`（采样首 token）、`:2187`（`send_partial_response`）、`:5036`（`set_next`）；`server-common.cpp:84-105`（timings JSON） | 首 token 在 `post_decode` 采样点产出并写 `queue_results`；SSE 逐块下发；`timings.prompt_ms` 计时起点是 slot **拿到任务后**开始 prefill | 端到端 TTFT 与排队时长不可观测，须在到达、出队、首 token 三处插桩；响应可扩展 `queue_ms`/`ttft_ms` |

## 2. 逐链路说明（关键细节）

### 2.1 请求接入与身份/参数解析

- 路由：`tools/server/server.cpp:263-304`；`/v1/chat/completions` 与 `/chat/completions` 共用 `routes.post_chat_completions`（`:273-274`）。
- 处理：lambda `server-context.cpp:5557` 先 `oaicompat_chat_params_parse()`（`server-common.cpp:1270`）把 messages 渲染成 `prompt`，再调 `handle_completions_impl()`（`:4883`）。
- 参数解析：`handle_completions_impl` 取 `prompt`（`:4904`）→ `tokenize_input_prompts()`（`:4926`，实现 `server-common.cpp:1061`）→ 每个 prompt 建 `server_task`（`:4932-4941`），`task.id = rd.get_new_id()`（`:4934`）。
- 参数表驱动填充：`server_schema::eval_llama_cmpl_schema()`（`server-schema.cpp:467`）；`n_predict`/`max_tokens`/`max_completion_tokens` 在 `server-schema.cpp:44-48`，`temperature` `:116`，`seed` `:176`，`n_cmpl`/`n` `:62-65`。
- 身份两套：内部 `int server_task::id`（`server-task.h:138`，`server-queue.cpp:84` 自增）；对外 `chatcmpl-<32 随机字符>`（`server-common.cpp:126`）。客户端传入的 `"id"` 不参与身份；`n_cmpl>1` 的子任务共享 `chatcmpl` id，用 `index`（`server-task.h:141`）区分。
- **类别字段不存在**：`category|priority|class|group|tenant|weight|deadline|sla` 在 `tools/server/` 全目录零命中；`task_params`（`server-task.h:51`）只有 `id_slot` 与 `n_cmpl`。

### 2.2 等待队列与候选选择

- 入队仅两个入口：`post()`（`server-queue.cpp:28`，`front=true` 可插队首）与 `defer()`（`:76`）；容器为裸 `std::deque`（`server-queue.h:24-25`），**无容量上限**。
- 真正的并发上限在 HTTP 线程池：`server-http.cpp:528`、`:150-152`，默认 `max(n_parallel+4, hw-1)`。因此过载表现为 **deferred 队列无限增长**，而不是请求被拒绝——实现准入控制前必须先补队列边界与背压。
- 调度：单线程 `server_queue::start_loop()`（`server-queue.cpp:278`，由 `server.cpp:547` 拉起）；`process_new_tasks()`（`:138`）取 `front()` 交 `process_single_task`（`server-context.cpp:2821`），随后 `callback_update_slots()`→`update_slots()`。
- 取不到 slot 或指定 slot 忙即 `defer`（`:2851-2862`，日志 `:2853`），故队头长请求不阻塞后续任务。
- slot 选择 `get_available_slot()`（`:1696`）：显式 `id_slot` → prompt 前缀相似度（`slot_prompt_similarity`，默认 0.1，`common/common.h:706`）→ 空 slot 优先再按 `t_last_used` 升序（`get_free_slots`，`:2473-2486`）。
- **无真正优先级队列**：`post(..., front=true)` 仅用于 `/metrics`、`/slots` 与 CANCEL。deferred 的 slot 亲和重排（`pop_deferred_task`，`server-queue.cpp:90-113`）会让后到任务先于 main 队列等待者被服务。

### 2.3 slot 分配与容量约束

- slot 数：`-np/--parallel`（`common/arg.cpp:2548-2556`，默认 1）；传 `-1` 时 `server.cpp:168-173` 改为 4 并强制 `kv_unified=true`。
- 每 slot 上下文：`src/llama-context.cpp:377-393`——`n_ctx` 先 256 对齐；`kv_unified` 真则 `n_ctx_seq=n_ctx`，否则 `n_ctx_seq=pad256(n_ctx/n_seq_max)` 并回写 `n_ctx`。
- 服务端夹逼：`n_ctx_slot()`（`server-context.cpp:4652-4660`）= `min(llama_n_ctx_seq, kv_unified_per_slot, n_ctx_train)`；slot 初始化 `:1429-1437`；启动日志 `:1392` 打印 `n_slots/n_ctx_slot/kv_unified`。
- KV 分配：`src/llama-kv-cache.cpp:277-295`，失败抛 `failed to allocate buffer for kv cache`（`:288`）→ `server-context.cpp:1227-1230` → `server.cpp:488-493` 退出码 1。
- 运行期 KV 不足：`decode()` 中先 `try_clear_idle_slots()`（`:1810`，仅 `kv_unified`），否则 `n_batch/=2` 重试（`:4331-4338`）；`n_batch==1` 仍失败则报 `Context size has been exceeded.`（`:4295-4298`）并全 slot 报错释放（`:4312-4327`）。该重试是容量压力的可观测信号（日志 `:4336` WRN）。

### 2.4 prefill / decode 迭代

- 主循环 `update_slots()`（`server-context.cpp:3256`）：全空闲直接返回（`:3271-3286`）→ `pre_decode()`（`:3370`）→ batch 非空则按 `n_batch` 切片循环 `decode()` + `post_decode()`（`:3329-3367`）。
- batch 构造（`:3436` 起）：先收 `SLOT_STATE_GENERATING` 的 slot（`:3445-3510`，`can_batch_with()` `:477`）并追加其下一 token（`:3568-3570`）；再遍历 `PROCESSING_PROMPT/STARTED` 填 prompt 前缀（`:3583-3620`，主填充循环 `:4056-4124`）。**同一批次同时含其他任务的 decode 单 token 与本任务的 prefill 多 token**，即混合负载干扰点。
- 批大小双约束：`llama_n_batch` / `llama_n_ubatch`（默认 2048 / 512，`common/common.h:458-459`）；逻辑批满即停（`:3584`），物理批由 `llama_process` 内部再切。
- chunked prefill 隐式存在：while 每轮只吃 `n_batch` token，余下保持 `PROCESSING_PROMPT` 下轮继续；吃完切 `DONE_PROMPT`（`:4137-4148`），随即在 `post_decode` 采首 token（`:4449`、`:4468`）。
- 真正的 llama 调用点：`decode()`（`:4250`）内 `llama_process(ctx_tgt, LLAMA_PROCESS_TYPE_DECODE, batch.view.get())`（`:4285`），包在 `queue_tasks.yield_to_queue(...)`（`server-queue.cpp:104`、`:222`）中，故 decode 期间 `/metrics` 仍可应答。

### 2.5 取消 / 超时 / 完成

- 断开：httplib `req.is_connection_closed` 存入 `server_http_req::should_stop`（`server-http.cpp:685`、`:736`、`:757`；字段 `server-http.h:56`）；`server_response_reader::next()`（`server-queue.cpp:550`）每 `HTTP_POLLING_SECONDS=1`（`server-context.cpp:40`）轮询一次。
- 取消：`stop()`（`server-queue.cpp:602-617`）构造 `SERVER_TASK_TYPE_CANCEL` 以 `front=true` 插队首；`process_single_task` 的 CANCEL 分支（`server-context.cpp:2901-2910`）找到 slot 后 `release()`。触发点：`~server_response_reader(){stop();}`（`server-queue.h:218-220`）与 `next()` 收到 error（`server-queue.cpp:559-562`）。
- 超时：**无请求级排队/预填超时**。`t_max_prompt_ms` 标注 `// TODO: implement`（`server-task.h:68`），解析被注释（`server-schema.cpp:71-72`）。唯一生效的 `t_max_predict_ms`（`server-context.cpp:2085-2090`）要求已出现换行。HTTP 层另有 `--timeout` 读写超时（`server-http.cpp:216-217`）。
- 终态互斥出口在 `process_token()`（`:1976-2103`）：EOS `STOP_TYPE_EOS`（`:2093-2098`）、停止词 `STOP_TYPE_WORD`（`:630-632`）、各类上限 `STOP_TYPE_LIMIT`（`:2029`、`:2038`、`:2059`、`:2086`）、默认 `STOP_TYPE_NONE`。`has_next_token=false` 后由 `post_decode` 执行 `send_final_response` + `release`（`:4497-4503`）仅一次。枚举见 `server-task.h:44-49`。

### 2.6 序列清理与 slot 复用

- 唯一收尾口 `server_slot::release()`（`server-context.cpp:586-608`）：`state=IDLE`、记 `t_last_used`、**仅 `is_child()||is_stateless()` 才 `prompt_clear()`**（`:598-600`）、`reset()`（`:378-416`）、最后 `callback_on_release(id)`（`:606`）触发 `pop_deferred_task`（`:1444-1446`）。
- `prompt_clear()`（`:343-349`）= `mem.seq_rm(id,-1,-1)` + `prompt.clear()`；落到 `common_memory::seq_rm`（`common/common.cpp:1638-1643`）→ `llama_memory_seq_rm`（`src/llama-context.cpp:4393-4403`）。
- 设计上刻意**不**在正常结束时清 KV：普通任务 KV 留在 slot，下一请求命中前缀即省 prefill（`get_available_slot` 的 LCP 选择 `:1714-1762` + prompt cache `:1785-1799`）。
- 其余回收形态：上下文滑动 `seq_rm(id,n_keep,n_keep+n_discard)`（`:3412-3413`）；缓存未命中删尾（`:3949`）；投机回滚（`:3534`、`:4576`）；idle 清理 `try_clear_idle_slots()`（`:1810-1830`）。
- slot 与 seq_id 为 1:1（`slot.id=i`，`:1432`），slot 复用即同一 `seq_id` 换 KV 内容。

### 2.7 流式输出与事件观测

- 首 token：`post_decode()` 内 `common_sampler_sample()`（`server-context.cpp:4468`）→ 首次 `update_prompt_last()`（`:4478-4484`）→ `common_token_to_piece`（`:4490`）→ 流式时 `send_partial_response()`（`:2017-2019`）。
- SSE：`handle_completions_impl` 同步取首条结果作首 chunk（`:5006-5035`），再 `res->set_next(lambda)`（`:5036`）；无更多数据发 `data: [DONE]`（`:5077`）；`sse_ping_interval` 超时发心跳 `":\n\n"`（`:5087-5102`）。**心跳与元数据块不构成首个生成内容**，TTFT 口径须排除。
- 结束原因：非 OAI 侧 `stop_type`（`server-task.cpp:344-349`）；OAI 折算 `finish_reason`（`:374-377`）。
- timings：`server_slot_stats`（`server-common.h:400-479`）用 `t_start/t_prompt_last/t_gen_last` 做差；`t_start` 在 slot 真正开始 prefill 时置位（`server-context.cpp:3612`），`t_prompt_last` 首 token 采样时置位（`:4481`）。JSON 字段 `cache_n/prompt_n/prompt_ms/prompt_per_token_ms/prompt_per_second/predicted_n/predicted_ms/...`（`server-common.cpp:84-105`）。**`prompt_ms` 起点是拿到 slot 之后，不含排队，也不是端到端 TTFT。**

## 3. 现有可观测数据清单

| 类别 | 位置 | 内容 | 可用于 TTFT | 可用于排队时长 |
|---|---|---|---|---|
| 响应 `timings` | `server-common.cpp:84-105`；填充 `server-context.cpp:2222-2225`、`:2251` | `prompt_n/prompt_ms/prompt_per_token_ms/prompt_per_second`、`predicted_n/predicted_ms/...`、`cache_n` | 部分（仅引擎内 prefill） | 否 |
| 非 OAI 终态 | `server-task.cpp:344-349` | `stop_type`、`stopping_word`、`truncated`、`tokens_evaluated/predicted/cached`、`id_slot`、`generation_settings` | 辅助 | 否 |
| OAI 终态 | `server-task.cpp:374-401` | `finish_reason`、`usage`（含 cached tokens）、`timings` | 辅助 | 否 |
| `/metrics`（需 `--metrics`） | 生成 `server-task.cpp:1526-1619`；采集 `server-context.cpp:2948-2968` | `requests_processing`、`requests_deferred`（仅长度）、`n_busy_slots_per_decode`、`prompt_seconds_total`、`tokens_predicted_total` 等 | 间接（均值，无分位） | 否 |
| `/slots`（默认开） | `server-context.cpp:733-767`、`:5356-5396` | 每 slot `n_ctx / is_processing / id_task / n_prompt_tokens(_processed/_cache) / next_token.n_remain` 等 | 间接 | 否（队列不可见） |
| `/props` | `server-context.cpp:5431-5441` | 模型元信息与全局并行参数 | 否 | 否 |
| 日志 | 宏 `server-common.h:27-35` | 见下表关键行 | 需插桩时间戳 | 需插桩时间戳 |
| 调试开关 | `LLAMA_TRACE`（`:1459`）、`LLAMA_SERVER_SLOTS_DEBUG`（`:1468`）、`LLAMA_SERVER_SLOTS_N_DIFF`（`:1477`） | 提升 slot 级 TRC/DBG 日志 | 否 | 否 |

可直接采集的日志行（除注明外均在 `tools/server/server-context.cpp`）：

| 行 | 级别 | 内容 |
|---|---|---|
| `:1392` | INF | `initializing, n_slots = %d, n_ctx_slot = %d, kv_unified = …`（启动即知容量） |
| `:2853` | DBG | `no slot is available, defer task, id_task = %d`（排队发生点） |
| `:3616` | TRC | `new prompt, n_ctx_slot = %d, n_keep = %d, task.n_tokens = %d`（prefill 起点） |
| `:590` | INF | `stop processing: n_tokens = %d, truncated = %d`（slot 释放点） |
| `:2032`、`:2041`、`:2089`、`:2097` | DBG | 上下文耗尽 / 触限 / 时间上限 / EOS 停止 |
| `:658`、`:661-675` | INF | 生成速度与 timings 打印 |
| `:4336` | WRN | KV 不足，缩小 batch 重试（容量压力信号） |
| `server-queue.cpp:37`、`:152` | QUE_DBG | 入队 / 出队（`new task` / `processing task`） |
| `server-queue.cpp:610` | WRN | `cancel task, id_task = %d`（取消发生点） |

## 4. 关键缺口（结论性）

1. **类别字段不存在**：`task_params`（`server-task.h:51`）无任何语义分类成员，全目录检索零命中。首轮冒烟由客户端记录类别（符合第一步第 4 项）；策略实现时必须新增类别字段（候选 C）。
2. **无请求级超时**：`t_max_prompt_ms` 为 `// TODO`；排队与 prefill 阶段没有时间上限。超时、拒绝与“截止未完成”终态必须自行实现，否则终态无法互斥闭合。
3. **排队时长与端到端 TTFT 不可观测**：`timings.prompt_ms` 起点是拿到 slot 之后（`:3612`）；`requests_deferred` 只有长度没有时间；`t_arriv|wait_ms|queue_ms|ttft|time_to_first` 全仓零命中。这是尾延迟测评最容易踩空的点，必须自行插桩。
4. **队列无界**：两个 `std::deque` 无上限，过载表现为 deferred 队列无限增长而非拒绝；准入控制需先补边界与背压，否则“拒绝率”等终态指标不成立。
5. **API 变更**：该版本服务端已无 `llama_decode()` 字面量，调用点为 `llama_process(ctx, LLAMA_PROCESS_TYPE_DECODE, batch)`（`:4285`）。论文与实现文档引用 API 时须按此表述。

## 5. 拟修改 / 接入点候选

| 编号 | 位置 | 插入内容 | 为什么适合 |
|---|---|---|---|
| A | `server-context.cpp:4883` / `:4897` | 记 `t_arrival`；解析类别字段 | 所有 completion 请求唯一漏斗，且在排队之前 |
| B | `server-context.cpp:4964`（`rd.post_tasks` 前） | 准入裁决：按 token 数/类别决定拒绝、排队或放行 | 此时已 tokenize（`:4926`），有真实长度 |
| C | `server-task.h:51` + `server-schema.cpp:467` | 新增 `category/priority/deadline_ms` 字段与解析 | 参数表驱动，加字段成本最低、风格一致 |
| D | `server-queue.cpp:28` / `:39-41` | 入队打时间戳、按优先级插队、超限拒绝 | 唯一入队点，天然是准入与排队收口 |
| E | `server-queue.cpp:138-149` | FIFO 换优先级/老化/短作业优先 | 唯一出队点，策略集中一处 |
| F | `server-queue.cpp:90-113` | 老化：记等待时长，超阈值提权 | 现存唯一重排逻辑，最易饿死处 |
| G | `server-context.cpp:1696` / `:2473` | 类别感知 slot 选择 | slot 分配唯一决策入口，已有三档策略可扩展 |
| H | `server-context.cpp:2851-2862` | defer 分支加排队超时/降级/拒绝 | “排不上号”的确切位置，已有日志 |
| I | `server-context.cpp:3612` | 记 `t_prefill_start`，与 A 的 `t_arrival` 相减得排队时长 | prefill 计时起点，也是排队结束的准确时刻 |
| J | `server-context.cpp:4056-4124` | 限制单轮注入 token 数（R08 预算） | chunked prefill 的实际粒度所在，不必动 llama 层 |
| K | `server-context.cpp:3580-3584` | prefill 与 decode 的批预算分配 | 现有“生成优先、剩余给 prompt”，是最直接的尾延迟旋钮 |
| L | `server-context.cpp:4468-4486` | 记首 token 时刻，产出 TTFT/排队指标 | 首 token 唯一产出点 |
| M | `server-context.cpp:2187` / `:2230`；`server-common.h:400` | 向响应补 `queue_ms`/`ttft_ms`，或扩展 `server_slot_stats` | 对外可观测面唯一出口，评测无需改客户端 |
| N | `server-queue.h:24-25` | 给两个 deque 加上限与背压 | 目前完全无界 |
| O | `server-context.cpp:1468` | 复用 `LLAMA_SERVER_SLOTS_DEBUG` 输出决策日志 | 已有现成调试开关模式 |

## 6. 待验证假设（G0 / G2 门）

| 假设 | 验证方式 | 门 |
|---|---|---|
| 混合批次中 prefill 会拉高同批 decode 请求的逐 token 延迟 | 预实验 E0/E1 + 阶段观测（插桩 I/L） | G0 |
| 无界的 deferred 队列确实导致过载下交互请求等待无上限 | 过载场景观测 `requests_deferred` 与排队时长 | G0 |
| 新增类别/优先级字段后原有 OpenAI 兼容接口与 `/slots`、`/metrics` 不受影响 | 回归测试（原接口字段不变） | G2 |
| 在 `:3580-3584` 调整批预算可在不显著降吞吐下降低交互尾延迟 | R08 控制点验证（上下界与服务机会） | G2 |
| `prompt_clear` 保留 KV 的策略会与自研硬账本的“逻辑释放”冲突 | 容量边界与取消/失败测试 | G3 |

## 7. 核对状态

- 2026-10-19 抽样复核（与源码逐条对照一致）：`llama_process`（`server-context.cpp:4285`）、`get_available_slot`（`:1696`）、defer 日志（`:2853`）、`t_max_prompt_ms` 的 `// TODO`（`server-task.h:68`）、`requests_deferred` 指标名（`server-task.cpp:1585`）、`t_max_predict_ms` 判定（`:2085`）。
- 其余条目来自同一次只读检索；在 P2/G2 设计评审前须由学生逐条复核并在本表标注复核日期。
- `v0.7.0` 的 `llama_decode` → `llama_process` 变更已核实；现有项目文档未出现这两个 API 名称，无需回改。
