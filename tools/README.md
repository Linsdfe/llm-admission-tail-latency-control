# 文档维护

Word 文件不提交到 GitHub，以下工具仅用于维护本地提交材料。

使用前需要在项目根目录准备两份与 Markdown 同名的 Word 文件；`sync_word.py` 读取这些文件以保留样式，无法在全新克隆中直接生成模板。

用安装了 `python-docx` 的 Python 执行 `sync_word.py`，将两份 Markdown 同步到同名 Word。运行 `render_documents.ps1` 生成 `.document-work/` 下的 PDF 与页面图片，逐页核验后可删除该临时目录。Word 已被 `.gitignore` 排除，生成的 PDF 和页面图片也不应提交到仓库。

渲染脚本当前使用本机的 Codex 文档渲染脚本、Python、LibreOffice 与 Poppler 路径，其他设备使用前需要调整。`word_sync_checks.json` 记录最近一次文档核对结果；它不表示修改后的文档已自动通过视觉检查。
