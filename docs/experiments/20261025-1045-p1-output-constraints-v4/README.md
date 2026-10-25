# 输出语法约束v4：24道新题的同期配对验证

**U无约束完整正确10/24，G有约束15/24；格式合法13/24→24/24，剩余9题为实际值或比较结论错误。未达采前G24/24条件，G1待审，不扩大调度负载。**

## 固定设计和执行

2026-10-25，Asia/Shanghai。[协议](../../p1/output-constraints-pilot-protocol-v4.md)、[量规](../../p1/output-constraints-evaluation-rubric-v4.md)、[配置](../../../configs/p1-output-constraints-v4.json)在真实输出前固定。新开发seed24001，24道不同题目、每题U/G各一次，48测量＋2预热。每类任务8题；各类别×任务及类别×输入桶分别2对UG、2对GU，总12对UG、12对GU。历史v3的9/24只作背景，不是本轮效果对照。

两条件prompt和实际输入token IDs、目标数量、模型/引擎、温度0、自然EOS及预算64/384相同，缓存关闭。G额外传GBNF grammar，按公开任务指定ID、行数、顺序和完整字段域，数字10—99或两个标签均可，不读取正确答案。格式变化是解码约束的机制结果，不冒称模型自主遵循能力提高。

实际10:41:27—10:46:01，采集单调耗时273.960秒；外层监督275.033秒。1045是run_id标签，实际时间以environment/summary与监督为准。串行执行，不是并发性能对照。

## 结果

| 指标 | U无约束 | G有约束 |
|---|---:|---:|
| 全部完成 | 24/24 | 24/24 |
| 完整业务正确 | 10/24 | 15/24 |
| 格式/目标/顺序/域合法 | 13/24 | 24/24 |
| 固定目标字段正确 | 56/88 | 70/88 |
| 提取 / 核对 / 整理完整正确 | 6/8、0/8、4/8 | 6/8、2/8、7/8 |
| 短请求 / 后台完整正确 | 5/12、5/12 | 10/12、5/12 |
| 自然EOS / limit | 21 / 3 | 24 / 0 |
| 输入token范围 | 39—653 | 39—653 |
| 输出token范围 / 总量 | 6—64 / 811 | 5—36 / 500 |

配对四格：都对10、都错9、U错G对5、U对G错0。新增5道完整正确均来自合法标签、表头或非目标行修正，不证明取数/比较能力提升。每臂88目标分母固定，字段辅助计数允许题目存在额外无关输出，因此不替代完整业务主判；缺失/重复/错误值逐项列明，不只统计可成功解析部分。

G的9失败是2提取、6核对、1整理，共18错误字段。8道G核对的28个标签全部为“一致”，资料实际14相同、14不同，不能把标签合法当成真的完成了比较。i004的54/55错判一致，b002的R6应88写31，b003的R1应47写22，b009两个目标值错。[白话报告](../../reports/20261025/P1输出约束与事实正确性验证.md)解释这些例子。

输出少311 token只是本轮内容长度变化，没有单独测语法编译或采样成本，不作吞吐/时延增益结论。24题共享一次服务，不能视为48个独立性能重复或总体可靠率估计。

## 完整材料和来源

| 文件 | 核什么 |
|---|---|
| [逐对表](metrics.md) | 全部24题、执行先后、两种模式正确与字段计数 |
| [summary](summary.json) | 每臂/任务/类别/输入桶、配对四格及48条逐字段差异 |
| [全文](fulltexts.json) | 24个prompt、48份原答、源事实和独立标准答案 |
| [助手核读](assistant-review.md)、[结构化核读](assistant-review.json) | 全部48原答的助手知晓版本复核，不冒充学生或导师盲评 |
| [证据SHA](evidence-sha256.json)、[分析源快照](analysis-source.py)、[派生SHA](report-sha256.json)、[新增说明/核读SHA](review-sha256.json) | 区分采前评价版本、原始证据与采后说明，不覆盖原清单 |

本机原始批：`results/raw/development/20261025-1045-p1-output-constraints-v4/`。保留48条推理计划和离线评价计划、实际tokenization、逐请求原生SSE/全文/计时/metrics/slots、100CAP、GPU、58份项目来源、8份引擎来源、23条运行文件指纹和模型SHA。48条原生生成设置逐条确认U grammar为空、G等于公开任务生成的语法，`grammar_lazy=false`。逐题payload文件是从冻结构造器重建，真实HTTP字段隔离由采前本地假服务捕获验证，不冒称真实GPU网络抓包。

独立审计：`results/raw/maintenance/20261025-p1-output-constraints-independent-audit-v4/summary.json`，**2836项通过**，逐条主判、格式与字段计数、四格与96一致；最大迟发10.086ms。50请求100CAP逐ID配对，最终活跃0，服务退出0、缓存0、来源/模型/运行库前后相同，最终GPU无本批计算服务残留。

采前评价3文件于10:40:22另存快照，18项新单测及36组独立反例通过。新客户端保留原SSE、六终态、截止、未知token和异步持久化逻辑，仅增加可选grammar白名单；完整受控原生门禁含200请求发送校准、40请求慢盘等，均非GPU测量。门禁旧框架标签保留，实际客户端以`p1_structured_replay_v4.py`及SHA为准。

首次门禁准备因`/home/...`与`/mnt/c/...`路径别名混用失败，未运行GPU；原始失败和包装脚本保留在`...preflight-v4/`和`results/raw/client-verification/20261025-p1-structured-client-v4/`。另建`...preflight-v4-r2/`及`...structured-client-v4-r2/`，仅规范化包装器路径后完整通过。正式采集明确采用r2，未覆盖旧失败。

## 重算和后续

从根目录执行，输出目录或文件须不存在：

```bash
python3 -B scripts/analysis/96_analyze_p1_output_constraints_v4.py \
  --run-dir results/raw/development/20261025-1045-p1-output-constraints-v4 \
  --out-dir docs/experiments/独立重算新目录
python3 -B tests/integration/audit_p1_output_constraints_v4.py \
  --root /home/aiinfra/proj-win \
  --run-dir results/raw/development/20261025-1045-p1-output-constraints-v4 \
  --analysis-dir docs/experiments/20261025-1045-p1-output-constraints-v4 \
  --out results/raw/maintenance/独立重算新目录/summary.json
```

本批G15/24，未达到G24/24与技术完整性联合进入条件。不筛成功题、不改同批提示/语法续筛。下一项保留格式约束，另登记更明确任务指令的单一对照，核查取数和比较；若仍失败，再讨论模型适配，避免无限修表面格式。G1待审，M/B3及正式硬KV账本尚未实现或比较；新校准、分界和负载均未执行，旧性能不直接沿用。

固定字段任务可由普通程序完成，本轮只是模型资料遵循底线检查，不是LLM应用创新或复杂调度价值证据。[10-25索引](../../../daily/20261025/实验结果索引.md)记录原始数据、失败、日期包和GitHub发布，本地归档未建立异地副本。
