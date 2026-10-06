"""将客户端当前参数转换为本地 Mock 服务端配置。"""
from __future__ import annotations

from urllib.parse import urlencode, urlsplit


def client_to_mock_config(params: dict) -> tuple[dict, str]:
    """返回服务端字段和需要展示的协议限制说明，不连接远程地址。"""
    proto = params.get('proto')
    if proto not in ('tcp_client', 'ws_client', 'http_client'):
        raise ValueError('请选择 TCP、WebSocket 或 HTTP 客户端协议')
    config = dict(server_type=proto.replace('_client', '_server'), ip='127.0.0.1',
                  encoding='UTF-8', recv_encoding='UTF-8', head_length=0,
                  ws_path='/', response_mode='fixed', response_delay=0)
    body = params.get('send_message', '')
    response_format = params.get('message_format', 'text')
    headers = []
    notes = []
    if proto == 'tcp_client':
        port = params.get('port', 0)
        # 客户端发送编码对应服务端接收编码，反方向同理。
        config.update(encoding=params.get('recv_encoding') or 'UTF-8',
                      recv_encoding=params.get('encoding') or 'UTF-8',
                      head_length=params.get('head_length', 0))
    else:
        url = (params.get('ws_url' if proto == 'ws_client' else 'url') or '').strip()
        if '://' not in url:
            url = ('ws://' if proto == 'ws_client' else 'http://') + url
        try:
            parsed = urlsplit(url)
            schemes = ('ws', 'wss') if proto == 'ws_client' else ('http', 'https')
            if (parsed.scheme not in schemes or not parsed.hostname
                    or any(character.isspace() for character in parsed.hostname)):
                raise ValueError()
            port = parsed.port if parsed.port is not None else (443 if parsed.scheme in ('wss', 'https') else 80)
        except ValueError:
            raise ValueError('客户端 URL 无效，请填写完整的地址和有效端口')
        if parsed.scheme in ('https', 'wss') or (proto == 'ws_client' and params.get('ws_ssl')):
            notes.append('Mock 服务端暂不支持 TLS；已复用端口生成明文配置，请使用 http:// 或 ws:// 地址测试。')
        if proto == 'ws_client':
            config['ws_path'] = parsed.path or '/'
        else:
            request_body = params.get('body') or {}
            body_type = request_body.get('type', 'none')
            response_format = body_type if body_type in ('json', 'xml') else 'text'
            if body_type in ('json', 'xml', 'text'):
                body = request_body.get('text') or ''
            elif body_type == 'x-www-form-urlencoded':
                body = urlencode([(key, value) for key, value in request_body.get('data', []) if key.strip()])
            else:
                body = ''
                if body_type in ('binary', 'form-data'):
                    notes.append('文件和 multipart 正文未复制；请在返回报文中填写 Mock 响应。')
            # 请求专用头、认证信息和 Content-Length 不应成为响应头。
            headers = [[key, value] for key, value in (params.get('headers') or [])
                       if key.strip().lower() == 'content-type' and body_type not in ('binary', 'form-data')]
    try:
        port = int(port)
    except (ValueError, TypeError):
        raise ValueError('客户端端口必须为 1 到 65535')
    if not 1 <= port <= 65535:
        raise ValueError('客户端端口必须为 1 到 65535')
    if response_format not in ('text', 'json', 'xml'):
        response_format = 'text'
    config.update(port=port, response_message=body,
                  response_messages=[dict(name='默认响应', active=True, status_code=200,
                                          headers=headers, body=body, format=response_format)])
    return config, '\n'.join(notes)
