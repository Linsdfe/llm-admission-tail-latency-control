# 最近邻参考来源固定记录v1

2026-10-25补充G1来源核对。参考论文为[arXiv:2610.17795v1](https://arxiv.org/abs/2610.17795v1)，摘要页截至核查日标注已被IPCCC2026接收；本记录不把它写成会议已举行或正式会议版已核验。

同名[参考仓库固定提交](https://github.com/Julianjiuxi/qba-llm-serving/tree/e51873c85cb62a0095567577fc11bf4e1b7c7cde)为`e51873c85cb62a0095567577fc11bf4e1b7c7cde`。已保存该提交的MIT许可、README、`schedulers/qba.py`、`state.py`、`types.py`，校验清单见[来源清单](../references/qba-source-v1/manifest.json)，[许可证副本](../references/qba-source-v1/LICENSE.txt)保留原版权声明。源码快照在[本机记录](../../results/raw/maintenance/20261025-qba-source-pin-v1/)及本轮日期包，不安装、不执行上游代码，没有复现其仿真曲线。

此次固定发生在v8采集启动后；它是后续实现的来源证据，不是v8采集/评分的新依赖。v8四个无模型排序见证按采前已冻结的本项目请求级适配规则计算，不能称为上游代码运行或原方法的完整复现。原代码按完整prefill准入计短计数，当前方案拟按首次实际启动计数，该差异与真实启动/回滚边界仍须G2/G3审查。

最初未认证GitHub元数据请求受限、HTTPS Git连接重置；随后通过SSH只读解析固定提交，再读取该提交的原始文件。保留失败说明与成功来源，未使用未固定的活动分支文件替代快照。
