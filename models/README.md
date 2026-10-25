# 本地模型

存放本地 GGUF、分词器和模型元数据，模型文件默认不进入 Git。当前候选模型已在 WSL 工作副本加载并用于冒烟、小轨迹及预实验；模型身份、来源、许可证、量化、完整 SHA256 和实际路径以 [环境记录](../docs/environment.md) 为准。每个运行重新记录文件摘要。候选模型用于预实验不代表 G0 或最终实验配置已冻结。

## 2026-10-25 固定模型适配候选

P1模型适配v6另外选定[官方Qwen2.5-3B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF)，固定修订`7dabda4d13d513e3e842b20f0d435c732f172cbe`，文件`qwen2.5-3b-instruct-q4_k_m.gguf`。下载后实际2104932768字节、SHA256 `626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d`，与官方API保存的LFS记录一致。模型许可证按该固定修订的`LICENSE`保存，名称为qwen-research，不沿用旧模型的许可证描述。

权重位于WSL独立模型目录`/home/aiinfra/projects/llm-admission-tail-latency-control/models/qwen2.5-3b-instruct-q4_k_m-v6-20261025/`，不放入Git或日期ZIP。下载来源、官方API/模型卡/许可和校验记录见[本地获取证据](../results/raw/maintenance/20261025-p1-model-adaptation-acquisition-v6/)。采集前对该文件模板做24次离线渲染，均与复用的完整提示相同。实际加载与GPU卸载结果以[v6完整报告](../docs/experiments/20261025-2210-p1-model-adaptation-v6/README.md)为准。
