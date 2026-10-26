# v13：异常回收与同服务恢复

本目录是开发工程验证，G1仍待审。18条计划正确性请求、15条实际发送、14条进入模型，另2预热。终态11完成、2取消、2超时、1拒绝、1失败、1未完成；异常终态是事先设定的目标，不能作性能成绩。8次同服务恢复全部完成，原生16启动/16释放（含预热），338项独立核对通过。

[白话分析与88分复评](../../reports/20261026/P1异常回收与算法作用边界补强.md)、[采前协议](../../p1/lifecycle-and-boundary-protocol-v13.md)、[原生汇总](summary.json)、[全部记录与独立审计](independent-audit.json)、[审计源码](analysis-source.py)。

[15种合成故障](synthetic-gate.json)、[旧版两个问题复现](legacy-regression.json)、[Windows错误号夹具失败与保留](fixture-deviation.json)另列，不算GPU请求。缺释放负例正确隔离额度，预期不恢复同一控制器；此负例不是原生GPU崩溃实验。HTTP拒绝的空闲回收依赖独占服务。

运行时源码在本机原始目录source内，逐文件SHA保留；GitHub派生报告不替代完整本机原始数据。旧v11/v12原始数据与代码保持不变。
