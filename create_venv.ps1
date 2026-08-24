# 创建 Windows 虚拟环境 .venv_win 并安装依赖
# 用法: .\create_venv.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

uv venv --python 3.8 .venv_win
uv pip install --python "$PSScriptRoot\.venv_win\Scripts\python.exe" -e .

Write-Host "环境就绪: .venv_win （启动: .\start.ps1）"
