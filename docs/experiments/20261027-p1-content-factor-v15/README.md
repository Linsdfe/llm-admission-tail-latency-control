# v15：四条件故障定位，筛选支线仍不可用

32次测量＋2预热全部完成，8组已见事实、16种提示，不是32份新资料。原时间＋约束(TG)0/8、原时间＋无约束(TU)0/8、当前状态＋约束(SG)1/8、当前状态＋无约束(SU)1/8。后台四臂均0/4，无上限截断，34对原生启动/释放闭合。TG旧输出8/8逐字复现；不替换旧v14的2/48。工作质量保持88，G1待审。

[白话分析与负载收敛](../../reports/20261027/P1筛选任务四条件诊断与负载收敛.md)、[32份完整输出](逐题结果.md)、[采前协议](../../p1/content-factor-diagnostic-protocol-v15.md)。

[独立审计和全部数据](independent-audit.json)、[固定执行计划](public-plan.json)、[已见事实](fixtures-evaluation-only.json)、[原生汇总](summary.json)。事实和标准答案仅供评估，没有进入HTTP载荷。514项证据核对通过；8项单元测试在测量前通过。

[采后只读模板核查](template-audit.json)检查16种提示与ChatML标记，无新GPU调用，不排除所有潜在模型或运行问题。[采后错法描述](posthoc-error-description.json)中的忽略顺序集合匹配只作说明，不替代预定严格评分。

[主审计源码](analysis-source.py)、[模板核查源码](template-audit-source.py)、[错法汇总源码](posthoc-analysis-source.py)。当前状态资料同时变短并移除历史，不能单独归因于时间推理。原始请求、版本与日志本机保留，GitHub报告不等于原始数据已异地备份。
