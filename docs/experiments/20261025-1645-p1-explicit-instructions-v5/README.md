# 明确取数/比较指令v5：24题同期配对

**旧指令O完整正确15/24，明确指令C14/24；格式均24/24，字段70/88→73/88。2题改善、3题退步，未达C24/24。按采前约定停止连续提示调参，转模型配置与适配评估，G1待审。**

## 设计和实际执行

2026-10-25，Asia/Shanghai。[协议](../../p1/explicit-instructions-pilot-protocol-v5.md)、[量规](../../p1/explicit-instructions-evaluation-rubric-v5.md)、[配置](../../../configs/p1-explicit-instructions-v5.json)采前固定。新开发seed25001、24道不同题目，每题O旧命令/C明确命令各一次，48测量＋2预热。12对OC、12对CO；每类别×任务和类别×输入桶各2正序2反序。历史v3/v4只作背景，不是本轮效果对照。

先按base pair_id生成一次源事实与目标，再复制给O/C。同pair的源记录数/顺序、目标、正确答案、输出预算及GBNF逐字相同。新指令不提供正确数值或标签。两臂都用相同语法限制公开ID、行数、顺序和全部90数字/2标签取值，实际native回显逐条核对。

O/C提示和token序列不同，分别真实分词、保存哈希，共同源行数确保两者均落在同一固定输入档。两者平均输入差8 token。本轮只描述这套指令包的整体变化，不识别纯语义独立效果或性能收益。

固定模型、原型引擎original路径、2slot各2048、温度0、自然EOS、cache=false及输出预算64/384。复用字节不变的v4 native客户端与已通过的r2门禁，没有新增客户端或引擎改动。实际16:38:08—16:42:40，采集278.767秒，外层监督279.373秒；1645是目录标签，实际时间以environment/summary和监督记录为准。

## 数值与解释

| 指标 | O | C |
|---|---:|---:|
| 测量完成 / 自然EOS | 24 / 24 | 24 / 24 |
| 完整正确 | 15/24 | 14/24 |
| schema合法 | 24/24 | 24/24 |
| 固定目标字段正确 | 70/88 | 73/88 |
| 提取 / 核对 / 整理完整正确 | 5/8、3/8、7/8 | 5/8、3/8、6/8 |
| 输入范围 / 均值token | 39—653 / 230.75 | 47—661 / 238.75 |
| 输出总量token | 502 | 505 |
| 核对输出标签“一致/不一致” | 26 / 2 | 23 / 5 |
| 核对28字段正确 | 16/28 | 19/28 |

四格：都对12、都错7、O错C对2、O对C错3。字段固定88分母，缺失/重复/非法字段不消失；字段改善可能分布在不同题里，不能替代完整主判。C仍10题、15字段不符，没有总体整题改善，不能说指令完全无作用或对所有模型均无效。

b001取R1从47修为正确49、b007六项比较由部分错变全对；但i003取R1从正确63变75，i006从正确“不一致”变“一致”，b009整理R6从正确43变73。[白话报告](../../reports/20261025/P1明确取数与比较指令验证.md)解释这些例子。C核对虽减少偏向“一致”，仍9字段错；资料真实14同14异，不以标签总体比例代替逐项正确。

## 完整材料与技术证据

| 文件 | 内容 |
|---|---|
| [metrics](metrics.md) | 24对、顺序、整题和字段/格式逐项比较 |
| [summary](summary.json) | 按臂/任务/类别/输入档、四格、28标签诊断和48条差异 |
| [fulltexts](fulltexts.json) | 完整O/C题目、资料、48原答、独立期望和字段差异 |
| [助手核读](assistant-review.md)、[结构化核读](assistant-review.json) | 助手知晓版本完整复核，不冒充学生或导师盲评 |
| [证据SHA](evidence-sha256.json)、[97源码快照](analysis-source.py)、[派生SHA](report-sha256.json)、[新增核读SHA](review-sha256.json) | 采前评价版本与采后说明分开，旧清单不覆盖 |

本机raw：`results/raw/development/20261025-1645-p1-explicit-instructions-v5/`，含两份48行计划、分别真实tokenization、完整原生请求/SSE/全文/计时、metrics/slots、100CAP、GPU、69份项目来源、8份引擎来源、23运行条目9实文件及模型指纹。离线结构化源/目标/expected不作为额外HTTP字段发送；prompt中的资料和公开目标属于输入。两臂真实HTTP均9字段。逐请求payload文件明确为从冻结构造器重建，本地假服务捕获证据继承冻结native门禁，不冒称真实GPU抓包。

独立审计`results/raw/maintenance/20261025-p1-explicit-instructions-independent-audit-v5/summary.json`，**3546项通过**：48条实际grammar回显非空、同pair相同，O/C真实token分别核同桶，69来源/模型/运行库一致，最终空闲和退出0。50请求100CAP闭合，最大迟发17.583ms；主判、字段与四格和97一致，原始pending评价不回填。

最终19项单测、99项独立反例与48无模型题目prompt/oracle/grammar交叉核对通过，16:37:28冻结评价版本，16:38:08才启动。早期model-free早于生成器/单测定稿，初次freeze据SHA拒绝，无GPU；保留早期记录、差异和r2重核。workload首轮单测的假分词器把ChatML竖线算成资料行，也保留修正前测试和工具错误转录；不冒称转录是完整原控制台日志。实际采用model-free-v5-r2及evaluation-freeze-v5-r2。

## 重算和后续

根目录执行，输出须为不存在的新目录/文件：

```bash
python3 -B scripts/analysis/97_analyze_p1_explicit_instructions_v5.py \
  --run-dir results/raw/development/20261025-1645-p1-explicit-instructions-v5 \
  --out-dir docs/experiments/独立重算新目录
python3 -B tests/integration/audit_p1_explicit_instructions_v5.py \
  --root /home/aiinfra/proj-win \
  --run-dir results/raw/development/20261025-1645-p1-explicit-instructions-v5 \
  --analysis-dir docs/experiments/20261025-1645-p1-explicit-instructions-v5 \
  --out results/raw/maintenance/独立重算新目录/summary.json
```

本轮C14/24未达进入条件。停止连续改提示筛成功；下一项核模型运行配置/模板与适配，再登记替代模型小样本对照，任务、语法及评分保持可比。尚未下载或运行替代模型，未扩大调度实验、校准或G1冻结；M/B3和正式硬KV账本仍未实现或比较，旧估计器/64分界/九轮性能不直接沿用。

单次串行合成题不估计总体可靠率、纯语义效果或调度价值。固定字段可由普通程序完成，这只是模型资料遵循底线，不是LLM应用创新。[当日归档与发布](../../../daily/20261025/实验结果索引.md)另记；原始与ZIP本地归档未建立异地副本。
