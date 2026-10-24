# 实验产物

原始运行保存为 `raw/<stage>/<run_id>/`，至少包含填写后的 manifest、请求日志和服务端日志。阶段区分 prestudy、development、final；run_id 应唯一。原始文件只追加，不覆盖。

`processed/` 保存带输入 run_id 和分析版本的指标；`figures/` 保存可由脚本再生的图表。运行数据默认不提交，交付时另做归档、摘要与数据访问清单。

## G0通过与P1启动（2026-10-24）

用户已明确确认[G0项目评审通过](../docs/reviews/20261024-g0-decision.md)，P0结束，现进入[P1需求与实验方案](../docs/p1/README.md)。本日已完成9轮2010条真实开发测量，另24校准、20预热；G1未审未冻结。工时按用户指示成果后补，自动执行时间据实记录。[当天索引](../daily/20261024/实验结果索引.md)区分证据核对、开发数据和方案。

| 本日开发证据 | 状态与用途 |
|---|---|
| `raw/client-verification/20261024-p1-development-client-v1/`、`v2/` | 两版均保留，最终v2与冻结客户端SHA匹配；原生SSE、计划期限、结构化400、未知token、竞态与慢盘检查通过 |
| `raw/development/20261024-025138-p1-development-validation-v1/` | 9轮2010条开发测量，另24校准/20预热，全部完成；38源、8引擎源、运行库/模型不变，未触碰最终测试 |
| `raw/maintenance/20261024-p1-development-independent-audit-v1/` | 保留前两轮增量审计；最终113819项程序核对0错，2054启动/2054释放逐ID匹配 |
| [本批报告](../docs/experiments/20261024-025138-p1-development-validation-v1/README.md)与[诊断](../docs/experiments/20261024-025138-p1-development-validation-v1/diagnostics-v1/README.md) | 逐轮重算，不跨批混性能；预测达到输出上限及NN分类退化仅作开发诊断，不声称M/B3收益 |

晚间追加的[P1补测报告](../docs/reports/20261024/P1扩大校准与输出预算检查.md)已完成，所有旧数据保留，以下各批不混算性能或训练。

| 校准与预算补测 | 状态与用途 |
|---|---|
| `raw/development/20261024-104708-p1-calibration-refinement-v1/` | 96条完成后协议纯换行字节变化，来源检查中止；未进入拟合 |
| `raw/development/20261024-1142-p1-calibration-refinement-v1-retry/` | 47条完成、第48条仅启动无完整终态；进程已不在，陈旧running不代表仍运行；未进入拟合 |
| `raw/development/20261024-1926-p1-calibration-refinement-v1-final/` | 120校准/120新开发验证/2预热全部完成，8.20分钟；后缀final仅为本轮重跑标签，不是最终测试 |
| [240条派生分析](../docs/experiments/20261024-1926-p1-calibration-refinement-v1-final/README.md) | 预定规则选中位数；60/120点预测、100/120总上界触顶；分界64与I/B重合 |
| `raw/maintenance/20261024-p1-calibration-final-independent-audit-v1/with-analysis/` | 独立12125项核对通过，242启动/释放对、42源/8引擎源/23运行项与模型核对；最大迟发1.955ms |
| `raw/development/20261024-1926-p1-output-budget-probe-v1/`及[探针报告](../docs/experiments/20261024-1926-p1-output-budget-probe-v1/README.md) | 8条后验选例、16次测量、2预热；独立441项核对通过，助手核读仍见任务未完成，不是总体质量率 |
| `raw/maintenance/20261024-p1-refinement-supervisor-v1/` | 独立WSL监督会话、两批控制台日志与退出码0，主批停服后再启动探针 |

## 提示任务v2（10-24采集，10-25核读收尾）

`raw/development/20261024-2000-p1-prompt-task-repair-v2/`保留40测量＋2预热、84CAP事件，服务退出0，实际缓存输入为0，源/运行库/模型一致。10-24机器耗时219.021秒，1764项原始数据核对通过。10-25来源复核389项与8项单元检查通过，未新增GPU。

[40条客观报告及核读](../docs/experiments/20261024-2000-p1-prompt-task-repair-v2/README.md)、[白话结果](../docs/reports/20261025/P1提示修订验证与后续任务边界.md)与[今日归档索引](../daily/20261025/实验结果索引.md)分别保留停因/长度、逐条语义和跨日归档。任务完成条件未达，不自动扩大负载或通过G1。旧两版模型外门禁、最终recheck和所有运行源码均保留，不覆盖旧数据；日期包按10-25收尾登记，原run_id不改。

以下是决定前的G0材料入口，空白表不代替随后明确通过的项目决定。

[G0白话实验与评审表](../docs/reports/20261023/G0评审材料_实验完成版.md)已整理，按实验日期归档：四个完整批次42轮9630条测量、84预热；10-23新增9次容量边界/恢复请求另列，结果通过。技术证据具备评审条件，用户/导师结论留空，原题M/B3主比较保留，P1未启动。

| 本次新增证据 | 状态与用途 |
|---|---|
| `raw/maintenance/20261023-g0-consolidation-existing-audit-v1/` | 只读汇总42轮、核对旧报告及小文件指纹；不重复采集，不跨批合并性能 |
| `raw/maintenance/20261023-g0-nearest-neighbor-check/` | 六篇原文方法与出处记录；MAPS在线误差余量已有直接先例 |
| `raw/development/20261023-2256-g0-context-boundary-v1/` | 实际22:54:31—40完成；2预热、1边缘200且容量停止、2预期400、4恢复；9启动/9释放闭合。目录时间标签与协议页首误写见其README勘误 |

历史条目中的“收窄后复审”等意见按当时语境保留；本次以原题范围及当前空白评审表为准。正确性探针不计入9630条性能测量，当前通过的检查不等于G0已获批准。

## prestudy 运行索引（P0）

| 目录 | 日期 | 性质 | 状态 | 说明 |
|---|---|---|---|---|
| `raw/prestudy/2026-10-19-env-check/` | 2026-10-19 17:22—17:36 | 环境体检 | 有效（首轮） | 发行版/内核/CPU/内存/磁盘/构建工具/Python/网络连通性；GPU 与 CUDA 两项见 `gpu-check.txt` |
| `raw/prestudy/2026-10-19-build/` | 2026-10-19 19:16—19:47 | 引擎固定与构建 | 有效 | llama.cpp v0.7.0 克隆记录与 SHA、CUDA 构建日志（12.8 min）、模型 SHA256 |
| `raw/prestudy/2026-10-19-smoke/` | 2026-10-19 19:57 | 手工 curl 冒烟（冷态对照） | **已被 v2 取代** | 仅保留 `server.log`（唯一不可再生的冷态首次请求日志）；其余被 v2 完整取代的派生文件已清理，见该目录 README |
| `raw/prestudy/2026-10-19-smoke-v2/` | 2026-10-19 20:17 | 脚本化冒烟 | **正式证据** | 2 条预热 + 交互/缓存/后台 3 条测量请求；客户端逐块时间戳；每条请求独立 JSON；GPU 0.25 s 采样；含 `manifest.json` 与完整分析 README |
| `raw/prestudy/20261020-224436-dispatch-dryrun-304/` | 2026-10-20 | 原手工调度干跑 | 已按用户授权清理 | 用户在 Windows 手工删除；不作为论文引用依据 |
| `raw/prestudy/20261020-225855-http-sanity-304/` | 2026-10-20 | 原手工真实小轨迹 | 已按用户授权清理 | 已核实目录不存在；后续小轨迹引用新运行 |
| `raw/prestudy/20261020-231546-sanity-rerun-395/` | 2026-10-20 23:15—23:16 | 自动化干跑 + 真实 10 条小轨迹 | **当前小轨迹核对依据** | 2 预热 + 6 交互/4 后台；计时、文件、轨迹一致性通过；含 README、summary、manifest；35 份证据已校验归档，入口见 [当日结果索引](../daily/20261020/实验结果索引.md)；非正式性能结论 |

| `raw/prestudy/20261020-233937-p0-step7/` | 2026-10-20 23:39 | 初次受控终态/500 rps 校准 | 校准失败，保留 | 高于能力的压力测试迟发超限，未进入性能数据筛选 |
| `raw/prestudy/20261020-234009-p0-step7/` | 2026-10-20 23:40 | 受控终态/20 rps 校准 | 通过 | 六类终态、未释放、截止、完成/取消及连接拒绝；50 ms 门槛未放宽 |
| `raw/prestudy/20261020-234210-p0-e0e1/` | 2026-10-20 23:42—23:58 | 原始引擎探针 + 三组 E0/E1 | **当前配对预实验依据** | 1500 条完成、无迟发超限；[可发布报告](../docs/experiments/20261020-234210-p0-e0e1/README.md)，G0 待复审 |
| `raw/prestudy/20261020-235916-p0-step7-extended/` | 2026-10-20 23:59 | 补充总超时与近同时完成/取消 | 通过 | 持续输出仍总超时、30 条竞争互斥闭合；采集结束后执行 |

约定：

- 正式结论只引用标注“正式证据”的运行；`prestudy` 数据与最终测试集分开，不用于事后调参。
- 后续 E0/E1 运行按新 `run_id` 新建目录，并在此表追加一行。
- 原始日志不编辑；度量口径修正时保留旧文件与原因（见 v1 → v2）。
- `raw/` 内容不进入 git（见 `.gitignore`），交付时另做归档与数据访问清单。

## 论文证据保留与按日期归档

- `daily/YYYYMMDD/README.md`：当天任务、投入、结论与下一步；同目录的 `实验结果索引.md` 链接到具体运行和归档，不复制日志。
- `results/raw/<stage>/<run_id>/`：原始轨迹、客户端/服务端日志、逐请求时间戳与内容、终态、预热、配置、引擎/模型摘要、manifest、分析报告。run_id 包含运行日期与时间；新运行新建目录，不覆盖旧证据。
- `results/processed/` 与 `results/figures/`：派生指标与论文图表，注明输入 run_id、分析版本和生成命令；修订保留版本。
- `results/archives/YYYYMMDD/`：运行证据与脚本快照的 ZIP、归档 SHA256。使用 `python scripts/ops/archive_run.py --run-dir results/raw/prestudy/<run_id>` 创建；内部逐文件校验，执行脚本须与运行时记录的 SHA256 一致。该目录按实验运行日期组织。
- 原始数据与归档默认被 Git 忽略，保存在 Windows 主仓库磁盘；WSL 通过 `~/proj-win` 访问同一份。GitHub 中的文档或图更新不等于实验数据已备份。本地 ZIP 仍是同盘归档，异地副本尚未建立。
- 已经留作依据的成功、失败、异常与复验结果都保留并标明状态；仅在明确授权后清理无效演练或可再生缓存，留下清理记录。缺少的历史数据不能靠重跑恢复为同一份原始证据。
- 论文中区分冒烟、小轨迹正确性核对、探索性预实验和正式测试结果；10-20 小轨迹支撑回放与计时正确性，后续 E0/E1 和低强度补充提供探索性的条件差异，不支持自研策略性能收益结论。

## 2026-10-21 低强度补充与维护

| 运行 | 状态与使用范围 |
|---|---|
| `raw/prestudy/20261021-092933-lowload-client-gate/` | 客户端断言通过，但测试服务对空请求有异常提示；保留未修复源码 |
| `raw/prestudy/20261021-093210-lowload-client-gate-v2/` | 修复测试服务后复验通过，20 rps 最大迟发 8.444 ms |
| `raw/prestudy/20261021-093300-p0-lowload-v1/` | 六轮 1230 条全部完成，12 条预热另计；[审计报告](../docs/experiments/20261021-093300-p0-lowload-v1/README.md)通过；三对 Δ 均低于 100 ms 筛选门槛，不是正式策略结论 |
| `raw/maintenance/20261021-093400-document-consolidation/` | 被融合文档原文及 SHA、启动错误、旧批次 1623 文件 SHA 复查、收尾验证；无性能测量 |

当天新旧所有相关运行通过日期包一并保留；[当日索引](../daily/20261021/实验结果索引.md)提供最新回执。源码推送与原始数据备份分开判断，G0 仍待复审。

## 2026-10-22—23 简单策略验证

| 运行或证据 | 状态与使用范围 |
|---|---|
| `raw/development/20261022-simple-priority-aging-unit-v1/` | 9 项 C++ 队列检查通过，非性能数据 |
| `raw/prestudy/20261022-235950-p0-simple-policy-v1/` | 中断：首轮 230 条完整，次轮仅保存 33 条终态，后十轮未开；原因未确认，原状态不改，不补造丢失终态 |
| `raw/prestudy/20261023-094000-p0-simple-policy-v1/` | 两轮 460 条全部完成，简单轮 2 条迟发超过固定 50 ms 后停止；交互 P95 515.672→6961.208 ms 的负面观察保留，不归咎于单一测量因素 |
| `raw/maintenance/20261023-simple-policy-slowdisk-v1/` | 无模型的同步慢写反例，证明客户端持久化风险，不证明真实性能差异的全部原因 |
| `raw/client-verification/20261023-simple-policy-client-v2-async-persistence/` | v2 六终态/总截止/取消竞争及慢盘门禁通过；20 req/s 校准最大迟发 2.179 ms |
| `raw/development/20261023-0955-simple-policy-engine-v2/` | v2 测量链与同一原型的真实顺序、恢复探针通过，23 个运行文件条目指纹核验 |
| `raw/prestudy/20261023-100000-p0-simple-policy-v2/` | **本轮完整对照依据**：12 轮 2760 条全部完成，24 预热另计，最大迟发 28.991 ms；[完整分析](../docs/experiments/20261023-100000-p0-simple-policy-v2/README.md)通过 |
| `raw/maintenance/20261023-simple-policy-v2-independent-audit/` | 首四轮与全部十二轮独立重算，输入/终态/时刻/P95/工作量/吞吐/持久化/来源核对通过 |
| `raw/maintenance/20261023-simple-v2-engine-review/` | 真正启动顺序、slot 与老化机制独立核对通过；三对平稳同序，突发均有重排 |
| `raw/maintenance/20261023-v2-launch-transition/` | 后台启动尝试未形成样本，保持连接的前台命令实际执行；历史启动器源码不冒称已成功 |

截至上午，v1/v2 与历史两批不混成同一配对总体；三个完整批次累计 5490 条、48 条预热，不包含正确性门禁或不完整 v1。上午结论和评审条目见 [P0 简单调度验证与 G0 复审](../docs/reports/20261023/P0简单调度验证与G0复审报告.md)：工程可行性认可，原完整 G0 收窄后复审；复杂算法尚未比较。原始数据仍在本机，不代表已做异地备份。

## 2026-10-23 下午：后台并发上限

| 运行或证据 | 状态与使用范围 |
|---|---|
| `raw/development/20261023-1506-background-cap-build-v4/` | 最终独立 CUDA 构建；旧构建版本保留，非性能测量 |
| `raw/development/20261023-1508-background-cap-unit-v4/` | 原 9 项加新 19 项断言全部通过，非性能测量 |
| `raw/maintenance/20261023-background-cap-yield-cancel-regression-v4/` | 确定性取消竞争反例修复后，三种模式均通过；旧反例与修复前版本保留 |
| `raw/client-verification/20261023-1440-background-cap-client-v2/` | 新鲜客户端门禁通过，慢写 40 条全部完成、0 迟发 |
| `raw/development/20261023-1510-background-cap-engine-v1/` | 门禁失败，保留：检查脚本错误假设客户端超时等同服务端同步取消；不作为主性能结果 |
| `raw/development/20261023-1512-background-cap-engine-v2/` | 门禁失败，保留：检查脚本用固定等待推断入队顺序，改成真实启动日志屏障后重验；不作为主性能结果 |
| `raw/development/20261023-1515-background-cap-engine-v3/` | 同一最终新引擎的 8 项真实模型顺序、cap/老化、释放恢复检查全部通过 |
| `raw/prestudy/20261023-151822-p0-background-cap-v1/` | **完整采集且审计通过**：18 轮 4140 条全部完成，36 预热另计；单调时钟批次耗时 52.87 min，最大迟发 33.057 ms；[完整分析](../docs/experiments/20261023-151822-p0-background-cap-v1/README.md) |
| `raw/maintenance/20261023-background-cap-frozen-protocol-review-v1/` | 冻结协议/配置/分析器与采前快照、登记指纹一致；记录结论边界，不含性能结论 |
| `raw/maintenance/20261023-background-cap-independent-audit/all-eighteen-runs/` | 全 18 轮独立重算，4140 条、终态/逐记录/计时/工作量/速率/来源与生命周期全部通过；前三轮快照另存 |
| `raw/maintenance/20261023-background-cap-statistical-review-v1/complete-check-v1/` | 第二套独立统计核对，18 轮与全部 18 个对比均与冻结分析器一致 |
| `raw/maintenance/20261023-background-cap-mechanism-all18-v1/` | 全量机制与指纹独立终审，4176 次含预热启动/释放匹配；6 轮 cap 峰值均为 1、结束活跃数 0 |
| `raw/maintenance/20261023-background-cap-results-interpretation-review-v1/` | 研究解释审查：六个新增 cap 主对照均达聊天目标，也均超速率/后台代价线；不替代原始数据重算 |

[本轮过程与结论](../docs/reports/20261023/后台并发上限验证与复杂度选择.md) · [采前冻结协议](../docs/prestudy-background-cap-protocol-v1.md)。四个完整批次累计 **9630 条测量、84 条预热**，只用于产物计数，不合并性能总体。本批三种策略同期重新采集；原路径含三组共有取消正确性修复。1 秒目标和收益/代价线是探索假设，不是业务 SLO、统计证明或 G0 自动通过条件。复杂算法未运行。
