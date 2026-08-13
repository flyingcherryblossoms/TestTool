"""协议测试 JSON 导入导出 —— 支持集合、目标、服务端的完整配置序列化。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def export_collection_to_json(filepath: str | Path, collection: dict) -> tuple[bool, str]:
    """将单个集合（含目标和服务端）导出为 JSON 文件。

    collection 格式:
        {
            "name": str, "protocol_type": str,
            "targets": [
                {
                    "ip": str, "port": int, "name": str,
                    "encoding": str, "head_length": int, "timeout": float,
                    "ws_path": str, "ws_use_ssl": bool, "send_message": str,
                    "servers": [
                        {
                            "name": str, "server_type": str,
                            "ip": str, "port": int,
                            "encoding": str, "head_length": int,
                            "ws_path": str,
                            "response_mode": str, "response_message": str,
                            "response_delay": int,  # 响应延迟(毫秒)
                        }, ...
                    ]
                }, ...
            ]
        }
    返回 (ok, message)。
    """
    data = {
        "version": 1,
        "type": "protocol_collection",
        "name": collection.get("name", ""),
        "protocol_type": collection.get("protocol_type", "tcp_client"),
        "targets": collection.get("targets", []),
    }
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True, ""
    except OSError as e:
        return False, str(e)


def export_collections_to_json(filepath: str | Path, collections: list[dict]) -> tuple[bool, str]:
    """将多个集合导出为一个 JSON 文件。
    返回 (ok, message)。
    """
    data = {
        "version": 1,
        "type": "protocol_collections",
        "collections": [
            {
                "name": c.get("name", ""),
                "protocol_type": c.get("protocol_type", "tcp_client"),
                "targets": c.get("targets", []),
            }
            for c in collections
        ],
    }
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True, ""
    except OSError as e:
        return False, str(e)


def import_collection_from_json(filepath: str | Path) -> tuple:
    """从 JSON 文件导入集合配置。

    返回 (list_of_collection_dicts, error_message) 或 (None, error_message)。
    成功时 error_message 为空，返回集合列表（单文件可能含多个集合）。
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return None, f"文件读取失败: {e}"

    if not isinstance(data, dict):
        return None, "JSON 格式错误：根节点应为对象"

    version = data.get("version", 0)
    if version != 1:
        return None, f"不支持的版本: {version}"

    data_type = data.get("type", "")
    if data_type == "protocol_collection":
        result, err = _parse_collection(data)
        return ([result], "") if result else (None, err)
    elif data_type == "protocol_collections":
        # 多集合格式
        raw_collections = data.get("collections", [])
        if not isinstance(raw_collections, list) or not raw_collections:
            return None, "collections 为空或格式错误"
        results = []
        for i, c in enumerate(raw_collections):
            result, err = _parse_collection(c)
            if err:
                return None, f"collections[{i}]: {err}"
            results.append(result)
        return results, ""
    elif data_type == "protocol_client_config":
        result, err = _parse_client_config(data)
        return ([result], "") if result else (None, err)
    elif data_type == "protocol_server_config":
        result, err = _parse_server_config(data)
        return ([result], "") if result else (None, err)
    else:
        return None, f"不支持的类型: {data_type}"


def _target_effective_proto(t: dict, collection_proto: str) -> str:
    """推断目标实际使用的协议：优先 send_presets 的 _active_proto 或已含预设的协议。"""
    raw = t.get("send_presets")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    if isinstance(raw, dict):
        active = raw.get("_active_proto", "")
        if active:
            return active
        for proto in ("http_client", "ws_client", "tcp_client"):
            if raw.get(proto):
                return proto
    return collection_proto


def _parse_collection(data: dict) -> tuple[dict | None, str]:
    """解析集合 JSON。"""
    name = data.get("name", "").strip()
    if not name:
        return None, "集合名称为空"

    targets = []
    raw_targets = data.get("targets", [])
    if not isinstance(raw_targets, list):
        return None, "targets 应为数组"

    proto_type = data.get("protocol_type", "tcp_client")
    is_ws = proto_type == "ws_client"

    for i, t in enumerate(raw_targets):
        if not isinstance(t, dict):
            return None, f"target[{i}] 应为对象"
        t_proto = _target_effective_proto(t, proto_type)
        # 集合 WS、目标 WS 或目标有 ws_path 时允许端口 0（对目标本身及其 servers 生效）
        t_is_ws = is_ws or t_proto == "ws_client" or bool(t.get("ws_path", ""))
        ip = t.get("ip", "").strip()
        port = t.get("port", 0)
        # HTTP 目标以 URL 为连接端点，导出时冗余字段 ip/port 为空，无需校验。
        # 空 URL 的 HTTP 目标应用本身允许保留（例如经"保存到集合"创建），导入按原样还原，
        # 避免导出后无法回导（round-trip）整份文件被拒。
        if t_proto != "http_client":
            if not ip:
                return None, f"target[{i}] IP 为空"
            min_port = 0 if t_is_ws else 1
            if not isinstance(port, int) or port < min_port or port > 65535:
                return None, f"target[{i}] 端口无效: {port}"

        servers = []
        raw_servers = t.get("servers", [])
        if isinstance(raw_servers, list):
            for j, s in enumerate(raw_servers):
                sv = _validate_server_dict(s, f"target[{i}].servers[{j}]", t_is_ws)
                if isinstance(sv, str):
                    return None, sv
                servers.append(sv)

        targets.append({
            "ip": ip,
            "port": port,
            "name": t.get("name", ""),
            "encoding": t.get("encoding", "UTF-8"),
            "recv_encoding": t.get("recv_encoding", "UTF-8"),
            "head_length": t.get("head_length", 5),
            "timeout": t.get("timeout", 5.0),
            "ws_path": t.get("ws_path", ""),
            "ws_use_ssl": t.get("ws_use_ssl", False),
            "send_message": t.get("send_message", ""),
            "send_presets": t.get("send_presets", []),
            "stress_params": t.get("stress_params", {}),
            "created_at": t.get("created_at", ""),
            "modified_at": t.get("modified_at", ""),
            "servers": servers,
        })

    return {
        "name": name,
        "protocol_type": data.get("protocol_type", "tcp_client"),
        "targets": targets,
    }, ""


def _parse_client_config(data: dict) -> tuple[dict | None, str]:
    """解析独立客户端配置 JSON。"""
    cfg = {
        "protocol_type": data.get("protocol_type", "tcp_client"),
        "ip": data.get("ip", ""),
        "port": data.get("port", 0),
        "encoding": data.get("encoding", "UTF-8"),
        "recv_encoding": data.get("recv_encoding", "UTF-8"),
        "head_length": data.get("head_length", 5),
        "timeout": data.get("timeout", 5.0),
        "ws_url": data.get("ws_url", ""),
        "ws_use_ssl": data.get("ws_use_ssl", False),
        "send_message": data.get("send_message", ""),
        "send_presets": data.get("send_presets", []),
        "stress_params": data.get("stress_params", {}),
    }
    return cfg, ""


def _parse_server_config(data: dict) -> tuple[dict | None, str]:
    """解析独立服务端配置 JSON。"""
    err = _validate_server_dict(data, "root")
    if isinstance(err, str):
        return None, err
    return err, ""


def _validate_server_dict(s: dict, path: str, is_ws: bool = False) -> dict | str:
    """验证服务端配置字典，返回 dict 或错误字符串。"""
    if not isinstance(s, dict):
        return f"{path} 应为对象"
    name = s.get("name", "").strip()
    if not name:
        return f"{path} 名称为空"
    port = s.get("port", 0)
    min_port = 0 if is_ws else 1
    if not isinstance(port, int) or port < min_port or port > 65535:
        return f"{path} 端口无效: {port}"
    return {
        "name": name,
        "server_type": s.get("server_type", "tcp_server"),
        "ip": s.get("ip", "0.0.0.0"),
        "port": port,
        "encoding": s.get("encoding", "UTF-8"),
        "head_length": s.get("head_length", 0),
        "ws_path": s.get("ws_path", "/"),
        "response_mode": s.get("response_mode", "fixed"),
        "response_message": s.get("response_message", ""),
        # None 时由 add_protocol_server 按 response_message 回填默认列表
        "response_messages": s.get("response_messages") or None,
        "response_delay": s.get("response_delay", 0),
    }


def export_client_config(filepath: str | Path, config: dict) -> tuple[bool, str]:
    """导出独立客户端配置为 JSON。"""
    data = {
        "version": 1,
        "type": "protocol_client_config",
        **config,
    }
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True, ""
    except OSError as e:
        return False, str(e)


def export_server_config(filepath: str | Path, config: dict) -> tuple[bool, str]:
    """导出独立服务端配置为 JSON。"""
    data = {
        "version": 1,
        "type": "protocol_server_config",
        **config,
    }
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True, ""
    except OSError as e:
        return False, str(e)
