# Build the standalone Windows executable (run from v2-python\ in PowerShell).
# Output goes to v2-python\dist-v2\ and v2-python\build-v2\ - never V1's dist\.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) { py -3.11 -m venv .venv }
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m PyInstaller --noconfirm --distpath dist-v2 --workpath build-v2 markdown_companion.spec
Write-Host "Done: dist-v2\MarkdownCompanionV2\MarkdownCompanionV2.exe"
