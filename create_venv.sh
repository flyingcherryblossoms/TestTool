#!/usr/bin/env bash
# 创建 Linux 虚拟环境 .venv_linux 并安装依赖
# 用法: ./create_venv.sh
set -euo pipefail
cd "$(dirname "$0")"

uv venv --python 3.8 .venv_linux
uv pip install --python .venv_linux/bin/python -e .

echo "环境就绪: .venv_linux （启动: ./start.sh）"
