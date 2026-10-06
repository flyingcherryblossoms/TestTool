"""当前客户端参数生成 Mock 配置及界面联动回归检查。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.database import Database
from src.mock_config import client_to_mock_config
from src.ui.protocol_panel import ProtocolPanel, _TargetDetailPanel


def test_tcp_encodings_are_reversed_and_framing_preserved():
    config, notice = client_to_mock_config(dict(proto='tcp_client', ip='192.0.2.1',
        port=9000, encoding='GBK', recv_encoding='UTF-8', head_length=4,
        send_message='当前未保存报文', message_format='xml'))
    assert config['server_type'] == 'tcp_server'
    assert config['ip'] == '127.0.0.1'
    assert config['port'] == 9000
    assert config['encoding'] == 'UTF-8'
    assert config['recv_encoding'] == 'GBK'
    assert config['head_length'] == 4
    assert config['response_message'] == '当前未保存报文'
    assert config['response_messages'][0]['format'] == 'xml'
    assert notice == ''


@pytest.mark.parametrize('proto,url,port,path', [
    ('ws_client', 'ws://example.com:9001/chat?token=secret', 9001, '/chat'),
    ('ws_client', 'ws://[::1]/', 80, '/'),
    ('http_client', 'example.com:8080/api', 8080, '/'),
    ('http_client', 'http://example.com', 80, '/'),
])
def test_url_mapping(proto, url, port, path):
    config, notice = client_to_mock_config(dict(proto=proto, url=url, ws_url=url,
                                               send_message='hello'))
    assert config['port'] == port
    assert config['ws_path'] == path
    assert notice == ''


def test_http_body_and_response_headers():
    params = dict(proto='http_client', url='http://example.com:8000/api',
                  body={'type': 'json', 'text': '{"未保存":true}'},
                  headers=[['Content-Type', 'application/json'], ['Authorization', 'secret'],
                           ['Cookie', 'secret'], ['Content-Length', '999']])
    config, notice = client_to_mock_config(params)
    response = config['response_messages'][0]
    assert response == dict(name='默认响应', active=True, status_code=200,
                           headers=[['Content-Type', 'application/json']],
                           body='{"未保存":true}', format='json')
    assert notice == ''


@pytest.mark.parametrize('url,proto', [('https://example.com', 'http_client'),
                                      ('wss://example.com/chat', 'ws_client')])
def test_tls_generates_plain_mock_with_explanation(url, proto):
    config, notice = client_to_mock_config(dict(proto=proto, url=url, ws_url=url))
    assert config['port'] == 443
    assert '不支持 TLS' in notice


def test_file_body_is_not_read():
    config, notice = client_to_mock_config(dict(proto='http_client', url='http://localhost:8000',
                                               body={'type': 'binary', 'path': '/does/not/exist'}))
    assert config['response_message'] == ''
    assert '未复制' in notice


def test_http_form_preserves_values_and_excludes_empty_keys():
    config, notice = client_to_mock_config(dict(proto='http_client', url='localhost:8000',
        body={'type': 'x-www-form-urlencoded',
              'data': [['name', '你好 世界'], ['name', 'second'], ['', 'ignored']]}))
    assert config['response_message'] == 'name=%E4%BD%A0%E5%A5%BD+%E4%B8%96%E7%95%8C&name=second'
    assert notice == ''


@pytest.mark.parametrize('port', [0, 65536, 'invalid'])
def test_invalid_tcp_port_rejected(port):
    with pytest.raises(ValueError, match='端口'):
        client_to_mock_config(dict(proto='tcp_client', port=port))


@pytest.mark.parametrize('url', ['', 'http://', 'ftp://example.com', 'http://x:0',
                                'http://x:65536', 'http://x:not-a-port', 'http://bad host'])
def test_invalid_http_url_rejected(url):
    with pytest.raises(ValueError):
        client_to_mock_config(dict(proto='http_client', url=url))


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('proto', ['tcp_client', 'ws_client', 'http_client'])
@pytest.mark.parametrize('target_bound', [False, True])
def test_generate_from_live_widgets_and_select_server(tmp_path, app, proto, target_bound):
    db = Database(str(tmp_path / 'mock.db'))
    if target_bound:
        collection = db.add_protocol_collection('测试', 'tcp_client')
        tid = db.add_protocol_target(collection, name='目标')
        panel = _TargetDetailPanel(db)
        panel.set_target(db.get_protocol_target(tid), db.get_protocol_collection(collection))
        client, servers = panel._client_panel, panel._server_panel
    else:
        tid = None
        panel = ProtocolPanel(db)
        client, servers = panel._standalone_client, panel._server_tab
        servers._type_filter.setCurrentIndex(3)
    panel.resize(1400, 900)
    panel.show()
    app.processEvents()
    servers._search.setText('不会匹配')
    client._proto_combo.setCurrentIndex(client._proto_combo.findData(proto))
    if proto == 'http_client':
        client._http_params.set_config(dict(url='http://example.com:8099/api',
            method='POST', body={'type': 'json', 'text': '{"当前":1}'},
            headers=[['Content-Type', 'application/json']]))
        expected_body = '{"当前":1}'
    else:
        client._param_port.setValue(8099)
        client._param_ws_url.setText('ws://example.com:8099/chat')
        client._send_edit.setPlainText('未保存正文')
        expected_body = '未保存正文'
    before = client.collect_params()
    try:
        for count in (1, 2):
            servers._mock_btn.click()
            records = db.get_all_protocol_servers()
            assert len(records) == count
            selected = servers._table.item(servers._table.currentRow(), 0).data(Qt.UserRole)
            mock = db.get_protocol_server(selected)
            assert mock.target_id == tid
            assert mock.port == 8099
            assert mock.server_type == proto.replace('_client', '_server')
            assert mock.response_message == expected_body
            assert mock.active_message() == expected_body
            assert not servers._all_workers()
            assert client.collect_params() == before
            assert servers._search.text() == ''
            if target_bound:
                assert panel._tabs.currentIndex() == 0
                assert not panel._server_collapsed
            else:
                assert panel._tabs.currentWidget() is servers
        assert len({record.name for record in records}) == 2
    finally:
        panel.close()


def test_invalid_url_does_not_create_server(tmp_path, app, monkeypatch):
    db = Database(str(tmp_path / 'invalid.db'))
    panel = ProtocolPanel(db)
    client = panel._standalone_client
    client._proto_combo.setCurrentIndex(2)
    client._http_params._url_edit.setText('http://')
    errors = []
    monkeypatch.setattr('src.ui.protocol_components.QMessageBox.warning', lambda *args: errors.append(args))
    panel._server_tab._mock_btn.click()
    assert errors
    assert not db.get_all_protocol_servers()
    panel.close()
