# 文档维护

用 Codex 提供的 Python 执行 sync_word.py，将两份 Markdown 同步到同名 Word，沿用现有样式。运行 render_documents.ps1 生成 .document-work 下的 PDF 与页面图片，逐页核验后可删除该临时目录。

工具路径配置在渲染脚本中，LibreOffice 位于 D:/DevTools/LibreOffice-26.8.1。word_sync_checks.json 记录最近一次内容一致性核对，归档中的清理记录保存此次视觉核验结果。
