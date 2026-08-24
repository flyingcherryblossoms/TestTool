# TestTool 启动脚本（Windows）
# 使用 .venv_win 虚拟环境启动 GUI，可选参数透传给 main.py
# 用法: .\start.ps1 [--db <path>]
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

& "$PSScriptRoot\.venv_win\Scripts\python.exe" main.py @args
