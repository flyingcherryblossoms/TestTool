#!/usr/bin/env bash
# TestTool 启动脚本（Linux）
# 使用 .venv_linux 虚拟环境启动 GUI，可选参数透传给 main.py
set -euo pipefail
cd "$(dirname "$0")"
exec .venv_linux/bin/python main.py "$@"
