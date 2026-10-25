# 固定模型适配 v6

旧模型14/24，新3B模型20/24；正确字段73/88→77/88。新模型仍未达到采前24/24的入口条件；暂停这条模型适配扩展，不启动新校准或调度负载。

本轮复用全部24道v5明确指令题，48测量＋8预热，四个ABBA独立服务；不是新题泛化测试。

- [白话报告](../../reports/20261025/P1模型配置核查与固定模型对照.md)
- [逐题指标](metrics.md)、[完整机器结果](summary.json)、[全部提示、资料和原答](fulltexts.json)
- [采前协议](../../p1/model-adaptation-pilot-protocol-v6.md)、[评分标准](../../p1/model-adaptation-evaluation-rubric-v6.md)
- [独立审计](independent-audit.json)、[助手逐条对照](assistant-review.md)
- [模型获取校验](model-acquisition.json)、[旧模型配置核查](configuration-audit.json)、[采前来源冻结](source-freeze.json)
- [本机原始记录](../../../results/raw/development/20261025-2210-p1-model-adaptation-v6/)、[独立监督](../../../results/raw/maintenance/20261025-p1-model-adaptation-supervisor-v6/)

48测量全部完成，340.540单调秒，112CAP闭合、四次服务退出0；独立审计1998项通过。9单测在采集前通过，最终测试隔离，G1未通过。语法强制格式，完整正确仍需事实全部相符。原始pending评价不回填；本目录是采后派生材料，完整原始数据不进Git。
