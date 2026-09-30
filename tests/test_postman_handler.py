"""Postman 转换和既有 TestTool JSON 的兼容性检查。"""
from __future__ import annotations

import json

from src.database import DEFAULT_PRESET_NAME
from src.json_handler import import_collection_from_json
from src.postman_handler import (
    SCHEMA, export_postman, import_postman, export_connectivity_postman,
    postman_connectivity_targets,
)


def collection(request, **extras):
    return {'info': {'name': '示例', 'schema': SCHEMA},
            'item': [{'name': '目录', 'item': [{'name': '请求', 'request': request}]}], **extras}


def config_of(target):
    return json.loads(target['send_presets']['http_client'][0]['message'])


def test_import_inheritance_variables_and_disabled_fields():
    data = collection({'method': 'POST', 'url': {'raw': '{{base}}/api?a=1',
                       'query': [{'key': 'a', 'value': '1'}, {'key': 'skip', 'value': '2', 'disabled': True}]},
                       'header': [{'key': 'X-Name', 'value': '{{name}}'}],
                       'body': {'mode': 'raw', 'raw': '{"value":"{{name}}"}',
                                'options': {'raw': {'language': 'json'}}}},
                      variable=[{'key': 'base', 'value': 'https://example.test'}, {'key': 'name', 'value': '中文'}],
                      auth={'type': 'bearer', 'bearer': [{'key': 'token', 'value': '{{name}}'}]})
    result, error = import_postman(data)
    assert not error
    target = result[0]['targets'][0]
    assert target['name'] == '目录 / 请求'
    assert target['send_presets']['http_client'][0]['name'] == DEFAULT_PRESET_NAME
    config = config_of(target)
    assert config['url'] == 'https://example.test/api'
    assert config['params'] == [['a', '1']]
    assert config['auth'] == {'type': 'Bearer Token', 'token': '中文'}
    assert config['body']['type'] == 'json'
    assert '中文' in config['body']['text']


def test_http_roundtrip_body_auth_queries_and_cookies(tmp_path):
    configurations = [
        {'body': {'type': 'json', 'text': '{"a":1}'}, 'auth': {'type': 'Basic Auth', 'username': 'u', 'password': 'p'}},
        {'body': {'type': 'xml', 'text': '<x>1</x>'}, 'auth': {'type': 'Digest Auth', 'username': 'u', 'password': 'p'}},
        {'body': {'type': 'text', 'text': '正文'}, 'auth': {'type': 'API Key', 'key': 'key', 'value': 'secret', 'location': 'Query Param'}},
        {'body': {'type': 'form-data', 'data': [['field', '中文']]}},
        {'body': {'type': 'x-www-form-urlencoded', 'data': [['field', 'value']]}},
        {'body': {'type': 'binary', 'path': '/tmp/example.bin'}},
    ]
    presets = []
    for index, config in enumerate(configurations):
        config.update(url='https://example.test/api?existing=yes', method='POST',
                      params=[['a', '1'], ['a', '2']], headers=[['X-Test', 'yes']], cookies=[['sid', 'cookie']])
        presets.append({'name': str(index), 'message': json.dumps(config)})
    path = tmp_path / 'requests.postman_collection.json'
    ok, error = export_postman(path, [{'name': '集合', 'targets': [{'name': '目标', 'send_presets': {'http_client': presets}}]}])
    assert ok and not error
    result, error = import_collection_from_json(path)
    assert not error and len(result[0]['targets']) == len(configurations)
    for target, expected in zip(result[0]['targets'], configurations):
        config = config_of(target)
        assert config['body'] == expected['body']
        assert config['auth'] == expected.get('auth')
        assert config['url'] == 'https://example.test/api'
        assert config['params'] == [['existing', 'yes'], ['a', '1'], ['a', '2']]
        assert ['Cookie', 'sid=cookie'] in config['headers']


def test_noauth_overrides_collection_auth():
    data = collection({'method': 'GET', 'url': 'http://example.test', 'auth': {'type': 'noauth'}},
                      auth={'type': 'bearer', 'bearer': [{'key': 'token', 'value': 'inherited'}]})
    result, error = import_postman(data)
    assert not error and config_of(result[0]['targets'][0])['auth'] is None


def test_structured_url_and_connectivity_ports(tmp_path):
    data = collection({'method': 'GET', 'url': {'protocol': 'https', 'host': ['example', 'test'], 'port': '8443', 'path': ['api']}})
    targets, errors = postman_connectivity_targets(data)
    assert not errors and targets[0]['ip'] == 'example.test' and targets[0]['port'] == 8443
    path = tmp_path / 'connectivity.json'
    assert export_connectivity_postman(path, [{'ip': '::1', 'port': 443, 'description': '', 'collection_name': 'IPv6'}])[0]
    restored, errors = postman_connectivity_targets(json.loads(path.read_text()))
    assert not errors and restored[0]['ip'] == '::1' and restored[0]['port'] == 443


def test_explicit_errors_and_skipped_non_http(tmp_path):
    assert import_postman(collection({'url': ''}))[1]
    assert import_postman(collection({'url': 'https://example.test', 'body': {'mode': 'formdata', 'formdata': [{'key': 'file', 'type': 'file', 'src': 'x'}]}}))[1]
    assert import_postman({'info': {'schema': 'https://example.test/v3.0.0/'}, 'item': []})[1]
    path = tmp_path / 'tcp.json'
    ok, error = export_postman(path, [{'name': 'TCP', 'targets': [{'name': 'tcp', 'send_presets': {'tcp_client': []}}]}])
    assert not ok and 'TCP/WS' in error and not path.exists()
    targets, errors = postman_connectivity_targets(collection({'url': 'https://{{host}}/'}))
    assert not targets and errors


def test_existing_testtool_json_unchanged(tmp_path):
    path = tmp_path / 'testtool.json'
    path.write_text(json.dumps({'version': 1, 'type': 'protocol_collection', 'name': 'TCP',
                               'protocol_type': 'tcp_client', 'targets': [{'ip': 'localhost', 'port': 123}]}))
    result, error = import_collection_from_json(path)
    assert not error and result[0]['targets'][0]['port'] == 123
