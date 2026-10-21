---
name: scientific-critical-thinking
description: "为本 LLM 推理控制项目审查实验设计、对照和混杂因素、重复与统计解释，以及论文方法和结论的证据边界。用于设计或复核实验、解释数据、撰写研究结果；不用于无关代码修改或排版。"
license: MIT license
metadata:
  version: "1.5"
  last-reviewed: "2026-10-01"
  skill-author: K-Dense Inc.
---

# Scientific Critical Thinking

## 项目适配

来源为 K-Dense 的 Scientific Critical Thinking，按 MIT 许可证下载并适配。先读
[本项目工作流](references/llm-project-workflow.md)，明确研究问题、干预、对照、指标、独立实验单位与允许的结论，再按需要加载下列方法参考。

本项目是单 GPU LLM 服务系统实验。通用参考中的人群、受试者和干预，按实际语境解释为负载条件、独立运行和策略；不机械套用临床 GRADE、Cochrane 等工具。单一变量并非有效实验的唯一设计，多因素研究也要说明目标效应如何识别。

当前 E0/E1 同时改变了额外任务及其工作量，不能只据此把差异归因于后台业务标签或长度异质性。区分整体干预效应、作用路径与外部混杂，不重复放大或否定已有证据。论文表达采用通俗中文，保留必要名词与定义。

下文保留上游的审查流程和参考索引。实验前形成简明协议，实验后核对完整终态和来源，写作时分清观察、推断和限制。安装本技能本身不意味着启动实验或批准 G0。

## When to Use This Skill

This skill should be used when:
- Evaluating research methodology and experimental design
- Assessing statistical validity and evidence quality
- Identifying biases and confounding in studies
- Reviewing scientific claims and conclusions
- Conducting systematic reviews or meta-analyses
- Applying GRADE or Cochrane risk of bias assessments
- Providing critical analysis of research papers

## Appraisal Workflow

1. **Fix the question and unit.** Record population, intervention/exposure, comparator,
   outcome, time point, effect measure, and target setting. Distinguish descriptive,
   predictive, and causal claims. Identify the independent experimental/sampling unit.
2. **Extract evidence before judging.** Locate the numerical result, uncertainty,
   denominators, protocol/registration, analysis plan, and relevant supplementary material.
   Keep missing reporting separate from evidence that a procedure was not performed.
3. **Assess design and analysis.** Check selection, confounding, measurement, attrition,
   multiplicity, dependence, and model assumptions. Good fit or optimizer convergence does
   not establish a uniquely identified parameter or a causal effect.
4. **Choose the right appraisal framework.** Reporting completeness, risk of bias, and
   certainty of a body of evidence answer different questions. Record the exact tool
   version and the result being assessed; do not turn checklist counts into a quality score.
5. **Synthesize with scope intact.** Examine independent replication, overlapping samples,
   missing evidence, and applicability. Apply GRADE per outcome/comparison when appropriate,
   with explicit domain reasons, rather than grading a whole paper by its design label.
6. **Write a traceable critique.** For each concern give the source location, observation,
   consequence for the claim, uncertainty, and a feasible remedy. Separate supported
   conclusions from assumptions and from clinical/policy recommendations.

Current framework versions, primary sources, and verification limits are in
[references/review_sources.md](references/review_sources.md). The examples in the references
are teaching examples, not empirical findings or validated patient-specific advice.

## 图示

需要图示时使用当前环境可用工具。此项目适配版不依赖额外图示技能、外部付费 API 或密钥。

## Core Capabilities

Seven capability areas, each with the questions to ask and what the answers imply, are in
[references/core_capabilities.md](references/core_capabilities.md):

1. **Methodology critique** — design, controls, confounding, and whether the method can
   answer the question asked.
2. **Bias detection** — selection, measurement, publication, and cognitive biases.
3. **Statistical analysis evaluation** — power, multiplicity, p-value misuse, effect sizes.
4. **Evidence quality assessment** — study hierarchy, replication, and strength of inference.
5. **Logical fallacy identification** — the fallacies that recur in scientific argument.
6. **Research design guidance** — how to strengthen a design before data collection.
7. **Claim evaluation** — separating what was shown from what is being asserted.

Per-topic detail is in [references/scientific_method.md](references/scientific_method.md),
[references/common_biases.md](references/common_biases.md),
[references/statistical_pitfalls.md](references/statistical_pitfalls.md),
[references/evidence_hierarchy.md](references/evidence_hierarchy.md),
[references/logical_fallacies.md](references/logical_fallacies.md), and
[references/experimental_design.md](references/experimental_design.md).

## Application Guidelines

### General Approach

1. **Be Constructive**
   - Identify strengths as well as weaknesses
   - Suggest improvements rather than just criticizing
   - Distinguish between fatal flaws and minor limitations
   - Recognize that all research has limitations

2. **Be Specific**
   - Point to specific instances (e.g., "Table 2 shows..." or "In the Methods section...")
   - Quote problematic statements
   - Provide concrete examples of issues
   - Reference specific principles or standards violated

3. **Be Proportionate**
   - Match criticism severity to issue importance
   - Distinguish between major threats to validity and minor concerns
   - Consider whether issues affect primary conclusions
   - Acknowledge uncertainty in your own assessments

4. **Apply Consistent Standards**
   - Use same criteria across all studies
   - Don't apply stricter standards to findings you dislike
   - Acknowledge your own potential biases
   - Base judgments on methodology, not results

5. **Consider Context**
   - Acknowledge practical and ethical constraints
   - Consider field-specific norms for effect sizes and methods
   - Recognize exploratory vs. confirmatory contexts
   - Account for resource limitations in evaluating studies

### Apply risk-of-bias tools to the right unit

For RoB 2, identify the specific result: outcome, time point, intervention comparison, numerical estimate, and effect of assignment versus adherence. Use the variant for individually randomized, cluster, or crossover trials; record signalling answers and justifications rather than assigning one blanket score to the whole paper. Different outcomes in the same trial can have different bias judgments. See the [Cochrane RoB 2 guidance](https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-08).

For non-randomized intervention effects, state whether using ROBINS-I 2016 or the
ROBINS-I V2 November 2025 **draft** for follow-up/cohort studies. Do not mix their domains
or algorithms. Diagnostic accuracy appraisal now uses QUADAS-3 (current tool v1.2), at
the accuracy-estimate level. See the tool-specific sources before a formal assessment.

### When Providing Critique

**Structure feedback as:**

1. **Summary:** Brief overview of what was evaluated
2. **Strengths:** What was done well (important for credibility and learning)
3. **Concerns:** Issues organized by severity
   - Critical issues (threaten validity of main conclusions)
   - Important issues (affect interpretation but not fatally)
   - Minor issues (worth noting but don't change conclusions)
4. **Specific Recommendations:** Actionable suggestions for improvement
5. **Overall Assessment:** Balanced conclusion about evidence quality and what can be concluded

**Use precise terminology:**
- Name specific biases, fallacies, and methodological issues
- Reference established standards and guidelines
- Cite principles from scientific methodology
- Use technical terms accurately

### When Uncertain

- **Acknowledge uncertainty:** "This could be X or Y; additional information needed is Z"
- **Ask clarifying questions:** "Was [methodological detail] done? This affects interpretation."
- **Provide conditional assessments:** "If X was done, then Y follows; if not, then Z is concern"
- **Note what additional information would resolve uncertainty**

## Reference Materials

This skill includes comprehensive reference materials that provide detailed frameworks for critical evaluation:

- **`references/scientific_method.md`** - Core principles of scientific methodology, the scientific process, critical evaluation criteria, red flags in scientific claims, causal inference standards, peer review, and open science principles

- **`references/common_biases.md`** - Comprehensive taxonomy of cognitive, experimental, methodological, statistical, and analysis biases with detection and mitigation strategies

- **`references/statistical_pitfalls.md`** - Common statistical errors and misinterpretations including p-value misunderstandings, multiple comparisons problems, sample size issues, effect size mistakes, correlation/causation confusion, regression pitfalls, and meta-analysis issues

- **`references/evidence_hierarchy.md`** - Traditional evidence hierarchy, GRADE system, study quality assessment criteria, domain-specific considerations, evidence synthesis principles, and practical decision frameworks

- **`references/logical_fallacies.md`** - Logical fallacies common in scientific discourse organized by type (causation, generalization, authority, relevance, structure, statistical) with examples and detection strategies

- **`references/experimental_design.md`** - Comprehensive experimental design checklist covering research questions, hypotheses, study design selection, variables, sampling, blinding, randomization, control groups, procedures, measurement, bias minimization, data management, statistical planning, ethical considerations, validity threats, and reporting standards

**When to consult references:**
- Load references into context when detailed frameworks are needed
- Search references for specific topics with your available text-search tool.
- References provide depth; SKILL.md provides procedural guidance
- Consult references for comprehensive lists, detailed criteria, and specific examples

## Remember

**Scientific critical thinking is about:**
- Systematic evaluation using established principles
- Constructive critique that improves science
- Proportional confidence to evidence strength
- Transparency about uncertainty and limitations
- Consistent application of standards
- Recognition that all research has limitations
- Balance between skepticism and openness to evidence

**Always distinguish between:**
- Data (what was observed) and interpretation (what it means)
- Correlation and causation
- Statistical significance and practical importance
- Exploratory and confirmatory findings
- What is known and what is uncertain
- Evidence against a claim and evidence for the null

**Goals of critical thinking:**
1. Identify strengths and weaknesses accurately
2. Determine what conclusions are supported
3. Recognize limitations and uncertainties
4. Suggest improvements for future work
5. Advance scientific understanding

## 来源与许可

上游仓库和固定提交、原文件校验值及本地改动见 [SOURCE.json](SOURCE.json)，MIT 版权和许可见 [LICENSE.md](LICENSE.md)。八份上游参考文件原样保留。

引用本技能来源可在工具说明中列出仓库和固定版本；是否在论文中引用关联论文，依据实际贡献与学校规范决定，文献信息必须核验。本地适配版不自动向论文插入上游自引文献。
