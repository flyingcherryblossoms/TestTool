"""Postman Collection v2/v2.1 与 TestTool HTTP 预设互转。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.database import DEFAULT_PRESET_NAME

SCHEMA = 'https://schema.getpostman.com/json/collection/v2.1.0/collection.json'


def is_postman(data: dict) -> bool:
    return isinstance(data.get('info'), dict) and 'item' in data


def _pairs(items):
    if not isinstance(items, list):
        raise ValueError('Postman 参数应为数组')
    return [[str(item.get('key', '')), str(item.get('value', ''))]
            for item in items if isinstance(item, dict) and not item.get('disabled')]


def _resolve(value, variables):
    if isinstance(value, str):
        for _ in range(10):
            result = re.sub(r'\{\{([^{}]+)\}\}',
                            lambda m: str(variables.get(m.group(1), m.group(0))), value)
            if result == value:
                break
            value = result
        return value
    if isinstance(value, list):
        return [_resolve(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, variables) for key, item in value.items()}
    return value


def _import_auth(auth):
    if not auth or auth.get('type') == 'noauth':
        return None
    kind = auth.get('type')
    values = dict(_pairs(auth.get(kind, [])))
    if kind == 'bearer':
        return {'type': 'Bearer Token', 'token': values.get('token', '')}
    if kind in ('basic', 'digest'):
        return {'type': 'Basic Auth' if kind == 'basic' else 'Digest Auth',
                'username': values.get('username', ''), 'password': values.get('password', '')}
    if kind == 'apikey':
        return {'type': 'API Key', 'key': values.get('key', ''),
                'value': values.get('value', ''),
                'location': 'Query Param' if values.get('in') == 'query' else 'Header'}
    raise ValueError('暂不支持 Postman 鉴权类型: ' + str(kind))


def _import_request(request):
    if isinstance(request, str):
        request = {'url': request, 'method': 'GET'}
    if not isinstance(request, dict):
        raise ValueError('Postman request 应为对象或 URL')
    url = request.get('url', '')
    query = None
    if isinstance(url, dict):
        query = _pairs(url.get('query', [])) if 'query' in url else None
        raw = url.get('raw', '')
        if not raw:
            host, path = url.get('host', ''), url.get('path', '')
            host = '.'.join(host) if isinstance(host, list) else host
            path = '/'.join(path) if isinstance(path, list) else path
            raw = str(url.get('protocol', 'http')) + '://' + host
            if url.get('port'):
                raw += ':' + str(url['port'])
            raw += '/' + path
        url = raw
    if not isinstance(url, str) or not url:
        raise ValueError('Postman 请求 URL 为空')
    parts = urlsplit(url)
    if query is None:
        query = [list(pair) for pair in parse_qsl(parts.query, keep_blank_values=True)]
    config = {'proto': 'http_client', 'method': request.get('method', 'GET'),
              'url': urlunsplit(parts._replace(query='')), 'params': query,
              'headers': _pairs(request.get('header', [])),
              'auth': _import_auth(request.get('auth')), 'body': None}
    body = request.get('body') or {}
    mode = body.get('mode')
    if mode == 'raw':
        language = ((body.get('options') or {}).get('raw') or {}).get('language', '')
        if not language:
            content_type = next((v for k, v in config['headers'] if k.lower() == 'content-type'), '')
            language = 'json' if 'json' in content_type else 'xml' if 'xml' in content_type else 'text'
        config['body'] = {'type': language if language in ('json', 'xml') else 'text',
                          'text': body.get('raw', '')}
    elif mode in ('urlencoded', 'formdata'):
        fields = body.get(mode, [])
        if any(field.get('type') == 'file' and not field.get('disabled') for field in fields):
            raise ValueError('暂不支持 Postman form-data 文件字段，请在导入后单独配置文件')
        config['body'] = {'type': 'form-data' if mode == 'formdata' else 'x-www-form-urlencoded',
                          'data': _pairs(fields)}
    elif mode == 'file':
        config['body'] = {'type': 'binary', 'path': (body.get('file') or {}).get('src', '')}
    elif mode:
        raise ValueError('暂不支持 Postman Body 类型: ' + str(mode))
    return config


def import_postman(data: dict):
    """展开嵌套目录；继承集合/目录鉴权，替换集合中有值的变量。"""
    try:
        schema = data.get('info', {}).get('schema', '')
        if schema and not any('/v' + version + '/' in schema for version in ('2.0.0', '2.1.0')):
            raise ValueError('仅支持 Postman Collection v2.0/v2.1')
        variables = dict(_pairs(data.get('variable', [])))
        targets = []
        warnings = []
        if data.get('event'):
            warnings.append('Postman 脚本不会执行或导入。')

        def visit(items, path, auth, scope):
            if not isinstance(items, list):
                raise ValueError('Postman item 应为数组')
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError('Postman item 应为对象')
                if item.get('event') and 'Postman 脚本不会执行或导入。' not in warnings:
                    warnings.append('Postman 脚本不会执行或导入。')
                local = dict(scope)
                local.update(dict(_pairs(item.get('variable', []))))
                inherited = item.get('auth', auth)
                name = str(item.get('name', '请求'))
                if 'item' in item:
                    visit(item['item'], path + [name], inherited, local)
                elif 'request' in item:
                    request = item['request']
                    if isinstance(request, dict):
                        request = dict(request)
                        if 'auth' not in request:
                            request['auth'] = inherited
                    config = _import_request(_resolve(request, local))
                    if '{{' in json.dumps(config, ensure_ascii=False) and '未解析的环境变量保留为 {{变量名}}，请在测试前替换。' not in warnings:
                        warnings.append('未解析的环境变量保留为 {{变量名}}，请在测试前替换。')
                    targets.append({'name': ' / '.join(path + [name]),
                                    'send_presets': {'_active_proto': 'http_client', 'http_client':
                                        [{'name': DEFAULT_PRESET_NAME, 'message': json.dumps(config, ensure_ascii=False)}]},
                                    'servers': []})
        visit(data.get('item', []), [], data.get('auth'), variables)
        return [{'name': data.get('info', {}).get('name') or 'Postman',
                 'protocol_type': 'http_client', 'targets': targets, '_import_warnings': warnings}], ''
    except (ValueError, TypeError, AttributeError) as exc:
        return None, 'Postman 导入失败: ' + str(exc)


def _export_auth(auth):
    if not auth:
        return {'type': 'noauth'}
    kind = {'Bearer Token': 'bearer', 'Basic Auth': 'basic',
            'Digest Auth': 'digest', 'API Key': 'apikey'}.get(auth.get('type'))
    if not kind:
        return {'type': 'noauth'}
    values = {key: value for key, value in auth.items() if key not in ('type', 'location')}
    if kind == 'apikey':
        values['in'] = 'query' if auth.get('location') in ('Query', 'Query Param') else 'header'
    return {'type': kind, kind: [{'key': k, 'value': str(v), 'type': 'string'} for k, v in values.items()]}


def _export_request(config):
    url = config.get('url', '')
    parts = urlsplit(url)
    query = [list(pair) for pair in parse_qsl(parts.query, keep_blank_values=True)]
    query += config.get('params') or []
    raw = urlunsplit(parts._replace(query=urlencode([tuple(pair) for pair in query])))
    result = {'method': config.get('method', 'GET'),
              'url': {'raw': raw, 'query': [{'key': k, 'value': v} for k, v in query]},
              'header': [{'key': k, 'value': v} for k, v in config.get('headers') or []],
              'auth': _export_auth(config.get('auth'))}
    cookies = config.get('cookies') or []
    if cookies and not any(h['key'].lower() == 'cookie' for h in result['header']):
        result['header'].append({'key': 'Cookie', 'value': '; '.join(k + '=' + v for k, v in cookies)})
    body = config.get('body') or {}
    kind = body.get('type')
    if kind in ('json', 'xml', 'text'):
        result['body'] = {'mode': 'raw', 'raw': body.get('text', ''),
                          'options': {'raw': {'language': kind}}}
    elif kind in ('form-data', 'x-www-form-urlencoded'):
        mode = 'formdata' if kind == 'form-data' else 'urlencoded'
        result['body'] = {'mode': mode, mode: [{'key': k, 'value': v, 'type': 'text'}
                                              for k, v in body.get('data', [])]}
    elif kind == 'binary':
        result['body'] = {'mode': 'file', 'file': {'src': body.get('path', '')}}
    return result


def export_postman(filepath, collections):
    """导出 HTTP 预设；非 HTTP 目标显式反馈，不伪造 Postman 请求。"""
    folders, skipped = [], 0
    try:
        for collection in collections:
            items = []
            for target in collection.get('targets', []):
                presets = target.get('send_presets') or {}
                if isinstance(presets, str):
                    presets = json.loads(presets)
                http = presets.get('http_client', []) if isinstance(presets, dict) else []
                if not http:
                    skipped += 1
                    continue
                requests = []
                for preset in http:
                    config = preset.get('message') or '{}'
                    if isinstance(config, str):
                        config = json.loads(config)
                    requests.append({'name': preset.get('name', '请求'), 'request': _export_request(config)})
                items.append({'name': target.get('name') or '目标', 'item': requests})
            if items:
                folders.append({'name': collection.get('name') or '集合', 'item': items})
        if not folders:
            return False, '没有可导出的 HTTP 请求；Postman Collection 不支持 TCP/WS 测试配置。'
        data = {'info': {'name': collections[0].get('name') if len(collections) == 1 else 'TestTool',
                         'schema': SCHEMA}, 'item': folders}
        Path(filepath).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        notes = [f'已跳过 {skipped} 个没有 HTTP 预设的目标。'] if skipped else []
        if any(target.get('servers') or target.get('stress_params') for collection in collections
               for target in collection.get('targets', [])):
            notes.append('Postman 文件不包含 Mock 服务端和压测参数；完整配置请使用 TestTool JSON。')
        return True, '\n'.join(notes)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return False, 'Postman 导出失败: ' + str(exc)


def postman_connectivity_targets(data):
    """连通测试仅提取 HTTP URL 的主机/端口，不能转换未解析的环境变量。"""
    collections, error = import_postman(data)
    if error:
        return [], [error]
    targets, errors = [], []
    for collection in collections:
        for target in collection['targets']:
            config = json.loads(target['send_presets']['http_client'][0]['message'])
            try:
                url = urlsplit(config['url'])
                host = url.hostname
                port = url.port or (443 if url.scheme == 'https' else 80)
                if not host or '{{' in host or url.scheme not in ('http', 'https'):
                    raise ValueError('URL 的主机或协议无效，或含未解析变量')
                targets.append({'ip': host, 'port': port, 'description': target['name'],
                                'collection_name': collection['name']})
            except ValueError as exc:
                errors.append(target['name'] + ': ' + str(exc))
    return targets, errors


def export_connectivity_postman(filepath, targets):
    """以 GET 请求表示连通目标；HTTPS 默认端口 443，其余端口使用 HTTP。"""
    grouped = {}
    for target in targets:
        host, port = target['ip'], target['port']
        if ':' in host and not host.startswith('['):
            host = '[' + host + ']'
        config = {'method': 'GET', 'url': ('https' if port == 443 else 'http')
                  + '://' + host + ':' + str(port) + '/', 'proto': 'http_client'}
        grouped.setdefault(target.get('collection_name') or '未分类', []).append({
            'name': target.get('description') or host + ':' + str(port),
            'send_presets': {'http_client': [{'name': DEFAULT_PRESET_NAME,
                                             'message': json.dumps(config, ensure_ascii=False)}]}})
    return export_postman(filepath, [{'name': name, 'targets': items} for name, items in grouped.items()])
