# 文档维护

## README 图示

运行 `python tools/render_readme_diagrams.py`，使用 Python 标准库生成 `docs/images/project-roadmap.svg` 与 `docs/images/p0-progress.svg`。修改阶段日期、状态或任务时，更新生成脚本并重跑；同时同步 README 进度表和实施计划。SVG 使用本地中文字体回退，不依赖外部图片、字体服务或 Mermaid 渲染。

## 本地 Word 材料

开题报告和开发计划等生成的 Word 默认不提交到 GitHub。应本次发布要求，`docs/reports/20261021/P0阶段总结与下一阶段安排.docx` 单独纳入版本管理，方便直接下载；同目录 Markdown 是内容源码。

`build_p0_report.py` 使用 `python-docx` 将该 Markdown 生成为同名 Word。`export_p0_report_samples.py` 从本机原始实验导出固定编号的9份样本，先核对既有证据 SHA256，再保留原文件字节，生成 CSV、来源清单，并刷新 Markdown 样本表。全新克隆已有公开样本，生成 Word 不需要重新运行实验；重新导出样本需要本机完整原始目录。

使用前需要在项目根目录准备两份与 Markdown 同名的 Word 文件；`sync_word.py` 读取这些文件以保留样式，无法在全新克隆中直接生成模板。

用安装了 `python-docx` 的 Python 执行 `sync_word.py`，将两份 Markdown 同步到同名 Word。运行 `render_documents.ps1` 生成 `.document-work/` 下的 PDF 与页面图片，逐页核验后可删除该临时目录。Word 已被 `.gitignore` 排除，生成的 PDF 和页面图片也不应提交到仓库。

渲染脚本当前使用本机的 Codex 文档渲染脚本、Python、LibreOffice 与 Poppler 路径，其他设备使用前需要调整。`word_sync_checks.json` 记录最近一次文档核对结果；它不表示修改后的文档已自动通过视觉检查。
