$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonRuntime = 'C:/Users/1/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
$documentRenderer = 'C:/Users/1/.codex/plugins/cache/openai-primary-runtime/documents/26.1015.12040/skills/documents/render_docx.py'
$libreOfficeBin = 'D:/DevTools/LibreOffice-26.8.1/program'
$popplerBin = 'C:/Users/1/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin'
$previousPath = $env:PATH
$previousUtf8 = $env:PYTHONUTF8
$previousEncoding = $env:PYTHONIOENCODING
try {
    # Process-local tool paths; no changes to system PATH or file associations.
    $env:PATH = "$libreOfficeBin;$popplerBin;$previousPath"
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $documents = @(
        @{ File = '开题报告_面向混合长短请求的LLM推理准入与尾延迟控制系统.docx'; Output = 'render-proposal-v12' },
        @{ File = '开发流程计划_面向混合长短请求的LLM推理准入与尾延迟控制系统.docx'; Output = 'render-plan-v12' }
    )
    foreach ($document in $documents) {
        & $pythonRuntime $documentRenderer (Join-Path $projectRoot $document.File) --output_dir (Join-Path (Join-Path $projectRoot '.document-work') $document.Output) --emit_pdf
        if ($LASTEXITCODE -ne 0) { throw "Render failed: $($document.File)" }
    }
} finally {
    $env:PATH = $previousPath
    $env:PYTHONUTF8 = $previousUtf8
    $env:PYTHONIOENCODING = $previousEncoding
}
