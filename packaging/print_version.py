"""给 CI/安装器输出项目版本，使用 Python 3.8 兼容解析。"""
from __future__ import annotations

from pathlib import Path
import re

project = Path(__file__).resolve().parent.parent / 'pyproject.toml'
match = re.search(r'^version\s*=\s*"([^"]+)"', project.read_text(), re.MULTILINE)
if not match:
    raise SystemExit('项目版本未定义')
print(match.group(1))
