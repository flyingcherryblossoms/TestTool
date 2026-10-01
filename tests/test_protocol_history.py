"""发送时的协议快照及旧历史记录的参数筛选。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.protocol_history import load_protocol_history_params, protocol_history_params


@pytest.mark.parametrize('proto,expected,excluded', [
    ('tcp_client', {'ip', 'port', 'head_length', 'timeout', 'encoding'},
     {'ws_url', 'ws_timeout', 'ws_ssl', 'url', 'settings'}),
    ('ws_client', {'ws_url', 'ws_timeout', 'ws_ssl'},
     {'ip', 'port', 'head_length', 'timeout', 'encoding', 'url', 'settings'}),
    ('http_client', {'url', 'settings', 'headers'},
     {'ip', 'port', 'head_length', 'timeout', 'ws_url', 'ws_timeout', 'ws_ssl'}),
])
def test_history_filters_by_recorded_protocol(proto, expected, excluded):
    mixed = dict(proto='ws_client', ip='127.0.0.1', port=9000, head_length=5,
                 timeout=30, encoding='GBK', recv_encoding='UTF-8',
                 ws_url='ws://example.com', ws_timeout=10, ws_ssl=True,
                 url='https://example.com', settings={'timeout': 8}, headers=[])
    result = load_protocol_history_params(json.dumps(mixed), proto)
    assert result['proto'] == proto
    assert expected <= result.keys()
    assert excluded.isdisjoint(result)
    assert mixed['proto'] == 'ws_client'


def test_snapshot_is_frozen_when_protocol_or_nested_config_changes():
    config = {'proto': 'http_client', 'url': 'http://example.com',
              'settings': {'timeout': 8}}
    frozen = protocol_history_params(config)
    config['proto'] = 'tcp_client'
    config['settings']['timeout'] = 30
    assert frozen['proto'] == 'http_client'
    assert frozen['settings']['timeout'] == 8


@pytest.mark.parametrize('serialized', ['', '{', '[]', 'null', '1'])
def test_empty_or_invalid_legacy_snapshot(serialized):
    assert load_protocol_history_params(serialized, 'tcp_client') == {}


def test_client_capture_keeps_protocol_when_ui_switches(tmp_path):
    from PySide6.QtWidgets import QApplication
    from src.database import Database
    from src.ui.protocol_components import ClientPanelBase
    app = QApplication.instance() or QApplication([])
    client = ClientPanelBase(Database(str(tmp_path / 'capture.db')))
    client.set_params({'proto': 'tcp_client', 'ip': '127.0.0.1', 'port': 9000,
                       'ws_url': 'ws://other.example/ws', 'ws_timeout': 8})
    snapshot = client._capture_test_context('request')
    assert snapshot['params']['proto'] == 'tcp_client'
    assert 'ws_url' not in snapshot['params']
    assert client.collect_params()['ws_url'] == 'ws://other.example/ws'
    client._proto_combo.setCurrentIndex(client._proto_combo.findData('ws_client'))
    assert client._capture_test_context('next')['params']['proto'] == 'ws_client'
    assert 'ip' not in client._capture_test_context('next')['params']
    assert snapshot['params']['proto'] == 'tcp_client'
    client.close()


@pytest.mark.parametrize('proto,excluded', [
    ('tcp_client', ('ws_url', 'ws_timeout', 'ws_ssl')),
    ('ws_client', ('ip', 'port', 'head_length', 'timeout', 'encoding')),
])
def test_detail_filters_legacy_send_and_receive_params(tmp_path, proto, excluded):
    from PySide6.QtWidgets import QApplication
    from src.database import Database
    from src.ui.protocol_panel import _TargetDetailPanel
    app = QApplication.instance() or QApplication([])
    detail = _TargetDetailPanel(Database(str(tmp_path / 'detail.db')))
    params = dict(proto='http_client', ip='127.0.0.1', port=9000, head_length=5,
                  timeout=30, encoding='UTF-8', recv_encoding='UTF-8',
                  ws_url='ws://other.example/ws', ws_timeout=8, ws_ssl=True)
    detail._hist_sessions = [SimpleNamespace(
        protocol_type=proto, test_params=json.dumps(params), success=True,
        request='request', response='response', error_msg='',
        request_raw=b'request', response_raw=b'response')]
    detail._on_hist_cell_clicked(0, 0)
    for pane in (detail._hist_send_detail, detail._hist_recv_detail):
        displayed = json.loads(pane.params.toPlainText())
        assert displayed['proto'] == proto
        assert set(excluded).isdisjoint(displayed)
    assert detail._hist_send_detail.message.toPlainText() == 'request'
    assert detail._hist_recv_detail.message.toPlainText() == 'response'
    detail.close()
