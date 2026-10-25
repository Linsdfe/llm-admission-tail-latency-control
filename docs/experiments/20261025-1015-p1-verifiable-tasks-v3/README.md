# 可核对资料任务v3：24条开发验证

**24条技术执行完整，9条业务答案完整正确；未达到采前24/24的进入条件。**提取6/8、核对0/8、整理3/8。全部结果保留，不扩大调度负载，不自动通过G1。

## 任务与执行

2026-10-25，Asia/Shanghai。用户要求先限定为可直接核对的资料提取、核对、整理。本轮是新业务任务版本，不是对v2同任务的单纯提示修订。

- [采前协议](../../p1/verifiable-task-pilot-protocol-v3.md)、[固定判分规则](../../p1/verifiable-task-evaluation-rubric-v3.md)、[配置](../../../configs/p1-verifiable-workload-v3.json)。单一候选、开发seed23001，不访问校准/最终测试。
- 24条测量＋2条预热。三类任务各8条，每类4短请求、4后台；六个类别×输入档各4条。目标数、资料和顺序在输出前固定。
- 自然EOS、温度0、关闭提示缓存、最大输出64/384；固定模型和原型引擎original路径，两执行位置。串行逐条测，不是并发策略实验。
- 实际10:14:18—10:15:59，采集单调耗时100.803秒。目录1015是批次标签，真实时间以environment/summary/监督记录为准。

## 主要数据

| 类别 | 请求 | 完整正确 | 实际输入token | 实际输出token | 自然结束/触顶 |
|---|---:|---:|---|---|---|
| 短请求 | 12 | 3 | 39—153 | 3—64 | 10/2 |
| 后台 | 12 | 6 | 132—653 | 25—60 | 12/0 |
| 全部 | 24 | 9 | 39—653 | 3—64，总计722 | 22/2 |

| 输入档 | 完整正确 |
|---|---:|
| 短32—79 / 80—143 / 144—256 | 各1/4 |
| 后台128—319 / 320—639 / 640—1024 | 各2/4 |

这批规范化正确与原始文本完全一致都是9/24；允许空白并未改变结果。不能把15个未完整完成都叫成事实全错。逐项统计中的“字段未通过”还包含缺字段、非法结构和额外ID；具体数值/标签错误见`value_mismatches`及助手核读。输出结构无效5例、漏ID4例、多ID7例、重复ID1例等可以重叠，不能相加。

两道触顶整理题已正确输出目标两行，之后继续抄无关记录。核对题也有真正误判数值相同与否，单纯修格式不解决事实错误。[白话报告](../../reports/20261025/P1可核对资料任务验证.md)列出具体例子。

## 可逐条核对的材料

| 材料 | 内容 |
|---|---|
| [逐条表](metrics.md) | 全部24条、任务/类别、输入/输出、停因、差异 |
| [summary](summary.json) | 分种类、类别、输入档和逐请求各维度结果 |
| [全部题目与回答](fulltexts.json) | 原始prompt、源事实、原回答、独立答案和逐项差异 |
| [助手复核](assistant-review.md)及[结构化记录](assistant-review.json) | 知晓版本的助手全文核读，不冒充学生或导师盲评 |
| [原始证据SHA](evidence-sha256.json) | 输入计划、离线评价计划、原始全文及逐请求记录 |
| [95分析器快照](analysis-source.py)、[分析产物SHA](report-sha256.json)、[新增复核SHA](review-sha256.json) | 保留生成时版本；后续说明不覆盖原派生清单 |

原始批次在本机`results/raw/development/20261025-1015-p1-verifiable-tasks-v3/`。包含实际tokenization、两个分离计划、逐请求原生计时与全文、metrics/slots、CAP、GPU、46份项目来源、8份引擎来源、23条运行文件指纹及模型指纹。期望答案只在离线评价计划；逐请求payload文件明确标为从冻结客户端重建，真实HTTP隔离证据来自采前本地假服务捕获单测，不冒充真实GPU网络抓包。

独立审计在本机`results/raw/maintenance/20261025-p1-verifiable-independent-audit-v3/summary.json`，**1472项通过**，逐条答案结果与95一致。最大迟发8.880ms，52条CAP逐ID配对闭合，缓存输入0，服务退出0；采后GPU记录无本批计算服务残留。评价器与独立审计均不导入造题程序，独立核对完整实际prompt、资料和目标顺序。

采前检查与监督分别在本机`results/raw/maintenance/20261025-p1-verifiable-{prelaunch-unit,runner-model-free,evaluation-freeze,supervisor}-v3/`，15项无模型测试全部通过。评价3文件于10:14:17冻结，采集10:14:18启动；来源SHA已另存，未在看到回答后修改量规或评价器。

## 重算方式与结论范围

从项目根目录执行，输出必须选不存在的新目录，保留旧派生结果：

```bash
python3 -B scripts/analysis/95_analyze_p1_verifiable_tasks_v3.py \
  --run-dir results/raw/development/20261025-1015-p1-verifiable-tasks-v3 \
  --out-dir docs/experiments/独立重算新目录
python3 -B tests/integration/audit_p1_verifiable_tasks_v3.py \
  --root /home/aiinfra/proj-win \
  --run-dir results/raw/development/20261025-1015-p1-verifiable-tasks-v3 \
  --analysis-dir docs/experiments/20261025-1015-p1-verifiable-tasks-v3 \
  --out results/raw/maintenance/独立重算新目录/summary.json
```

本批为单模型、合成资料、单次串行开发pilot，不估计总体业务正确率，不比较调度算法。固定字段任务能用普通程序完成，不能据此声称LLM应用创新。下一项先另登记输出约束验证并单独核实事实，不能只挑成功题，也不靠加长无关输出制造调度空间。新任务尚未重新校准或做并发负载；旧估计器、64分界及旧九轮性能不直接沿用。G1待审，M/B3未实现或比较。

[10-25归档与发布索引](../../../daily/20261025/实验结果索引.md)记录本地ZIP与GitHub交付。GitHub派生报告不替代完整原始数据，本地包未建立异地副本。
