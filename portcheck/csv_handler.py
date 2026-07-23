"""CSV 导入/导出处理。

支持格式：
  导入目标: ip, port, description, batch_name
  导出目标: ip, port, description, batch_name, created_at
  导出结果: ip, port, description, batch_name, status, latency_ms, error_msg, tested_at
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ── 导入数据模型 ──────────────────────────────────────────


@dataclass
class CsvTarget:
    """从 CSV 解析出的目标记录。"""
    ip: str
    port: int
    description: str = ""
    batch_name: str = ""

    def validate(self) -> Optional[str]:
        """校验数据合法性，返回错误信息或 None。"""
        if not self.ip or not self.ip.strip():
            return "IP 地址不能为空"
        if not (1 <= self.port <= 65535):
            return f"端口号无效: {self.port}（需在 1-65535 之间）"
        return None


# ── 解析与导入 ────────────────────────────────────────────


def parse_targets_csv(filepath: str | Path) -> tuple[list[CsvTarget], list[str]]:
    """解析目标 CSV 文件。

    CSV 列：ip, port, description, batch_name（batch_name 可选，其他必填）
    第一行如果是中文/英文标题头则自动跳过。

    Returns:
        (targets, errors) — targets 为成功解析的记录，errors 为逐行错误信息。
    """
    filepath = Path(filepath)
    targets: list[CsvTarget] = []
    errors: list[str] = []

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader, start=1):
            # 跳过空行
            if not row or all(c.strip() == "" for c in row):
                continue

            # 推测标题行并跳过
            if i == 1 and _is_header_row(row):
                continue

            # 至少需要 ip, port 两列
            if len(row) < 2:
                errors.append(f"第 {i} 行: 列数不足（至少需要 IP 和 Port）")
                continue

            ip = row[0].strip() if len(row) > 0 else ""
            port_str = row[1].strip() if len(row) > 1 else "0"
            desc = row[2].strip() if len(row) > 2 else ""
            batch = row[3].strip() if len(row) > 3 else ""

            try:
                port = int(port_str)
            except ValueError:
                errors.append(f"第 {i} 行: 端口 '{port_str}' 不是有效数字")
                continue

            target = CsvTarget(ip=ip, port=port, description=desc, batch_name=batch)
            err = target.validate()
            if err:
                errors.append(f"第 {i} 行: {err}")
            else:
                targets.append(target)

    return targets, errors


def _is_header_row(row: list[str]) -> bool:
    """判断是否是标题行。"""
    first = row[0].strip().lower() if row else ""
    header_keywords = {"ip", "地址", "address", "host", "主机", "端口", "port"}
    return first in header_keywords or any(
        kw in first for kw in ["ip", "地址", "port", "端口", "描述", "description", "集合", "batch"]
    )


# ── 导出 ───────────────────────────────────────────────────


def export_targets_to_csv(filepath: str | Path,
                           targets: list[dict]) -> tuple[bool, str]:
    """导出目标列表到 CSV。

    targets 每项需含: ip, port, description, batch_name, created_at
    """
    try:
        with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["IP地址", "端口", "描述", "集合", "创建时间"])
            for t in targets:
                writer.writerow([
                    t.get("ip", ""),
                    t.get("port", ""),
                    t.get("description", ""),
                    t.get("batch_name", ""),
                    t.get("created_at", ""),
                ])
        return True, ""
    except Exception as e:
        return False, str(e)


def export_results_to_csv(filepath: str | Path,
                           results: list[dict]) -> tuple[bool, str]:
    """导出测试结果到 CSV。

    results 每项需含: ip, port, description, batch_name,
                     status, latency_ms, error_msg, tested_at
    """
    try:
        with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["IP地址", "端口", "描述", "集合",
                              "状态", "延迟(ms)", "错误信息", "检测时间"])
            for r in results:
                writer.writerow([
                    r.get("ip", ""),
                    r.get("port", ""),
                    r.get("description", ""),
                    r.get("batch_name", ""),
                    "连通" if r.get("success") else "未连通",
                    f"{r.get('latency_ms', 0):.1f}" if r.get("success") else "",
                    r.get("error_msg", ""),
                    r.get("tested_at", ""),
                ])
        return True, ""
    except Exception as e:
        return False, str(e)


def generate_csv_preview(filepath: str | Path, max_rows: int = 20) -> str:
    """生成 CSV 文件的前几行预览文本。"""
    try:
        with open(filepath, "r", encoding="utf-8-sig") as f:
            lines = f.readlines()[:max_rows + 1]
        return "".join(lines)
    except Exception as e:
        return f"无法读取文件: {e}"
