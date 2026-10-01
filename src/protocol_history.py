"""历史参数按该次测试的协议筛选，兼容旧记录中的跨协议字段。"""
from __future__ import annotations

import json


_PARAM_KEYS = {
    'tcp_client': frozenset(('proto', 'ip', 'port', 'encoding', 'recv_encoding',
                             'head_length', 'timeout', 'send_message', 'message_format')),
    'ws_client': frozenset(('proto', 'ws_url', 'ws_timeout', 'ws_ssl',
                            'recv_encoding', 'send_message', 'message_format')),
    'http_client': frozenset(('proto', 'method', 'url', 'headers', 'params',
                              'body_type', 'body', 'auth_type', 'auth', 'cookies', 'settings')),
}


def protocol_history_params(params: dict, protocol_type: str = '') -> dict:
    """记录的协议优先；返回独立快照，不改动用于切换协议的预设配置。"""
    if not isinstance(params, dict) or not params:
        return {}
    proto = protocol_type or params.get('proto', '')
    keys = _PARAM_KEYS.get(proto)
    selected = {key: value for key, value in params.items() if keys is None or key in keys}
    if proto:
        selected['proto'] = proto
    return json.loads(json.dumps(selected, ensure_ascii=False))


def load_protocol_history_params(serialized: str, protocol_type: str) -> dict:
    try:
        params = json.loads(serialized or '{}')
    except (ValueError, TypeError):
        return {}
    return protocol_history_params(params, protocol_type)


def history_client_config(serialized: str, protocol_type: str, request: str) -> dict:
    """恢复该次请求的客户端配置，发送报文使用历史原文。"""
    if protocol_type not in _PARAM_KEYS:
        raise ValueError('该记录的测试协议不受支持。')
    config = load_protocol_history_params(serialized, protocol_type)
    if not config:
        raise ValueError('该历史记录未保存测试参数，无法导入为配置。')
    if protocol_type != 'http_client':
        config['send_message'] = request
        config.setdefault('message_format', 'text')
    return config
