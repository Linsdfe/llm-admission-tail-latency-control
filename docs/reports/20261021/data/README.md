# P0 报告的原始实验样本

来自批次 `20261020-234210-p0-e0e1`，不是新跑的实验。

- [CSV 提取表](request-samples.csv)：原精度的计时字段、实际输出量、终态、来源及 SHA256。
- [原始 JSON](raw/)：9份逐请求文件，按原字节复制，包含提示词、输出内容和流式块序列。
- [来源和校验清单](samples-manifest.json)：每份公开样本的原路径及 SHA256。
- [原实验的完整证据摘要](../../../experiments/20261020-234210-p0-e0e1/evidence-sha256.json)：用于核对样本来源。

固定选择第1组 E0/E1 的 I000、I100、I199，以及第1组 E1 的 B000、B050、B099。选择规则是计划编号的开头、中间、末尾，不按延迟筛选。这些样本只用于讲解，P50/P95 等总体结论仍使用每轮完整请求。完整1500条原始记录及日期 ZIP 仍在本机，9份公开样本不表示完整档案已异地备份。

计时单位为毫秒，offset 相对本轮测量起点。首字等待 = first_content_offset_ms − actual_send_offset_ms；发送滞后 = actual_send_offset_ms − arrival_offset_ms；从计划到达到完成 = end_offset_ms − arrival_offset_ms。`output_tokens` 提取自 `timings.predicted_n`，并非 `max_tokens` 上限。

复现提取：在原始数据完整的工作区运行 `python tools/export_p0_report_samples.py`，脚本先校验旧证据清单，再写公开副本和表格，不修改原始目录。

本地复核 SHA256：读取 `samples-manifest.json` 的每项 `copy`，相对于报告日期目录定位文件，计算 SHA256，与对应的 `sha256` 比较。Git 已对公开原始 JSON 关闭换行转换，避免跨平台检出改变原字节。
