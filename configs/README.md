# 运行配置

`run-manifest.template.json` 是元数据模板，不是可执行实验配置。复制到 `results/raw/<stage>/<run_id>/manifest.json`，使用实际值填写。时间参数统一记录秒，事件字段还须明确时钟域与单位。

后续新增引擎、策略和回放配置时，固定版本、单位、上下界及默认行为，并在 manifest 引用配置与摘要。B0 的原始引擎配置独立保存；B1—B4 与 M 共用硬守卫和可比较执行参数。
