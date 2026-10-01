"""历史参数恢复为独立配置，可切换协议并保留已有配置。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from src.database import Database
from src.protocol_history import history_client_config
from src.ui.protocol_panel import _TargetDetailPanel


@pytest.mark.parametrize('proto', ['tcp_client', 'ws_client', 'http_client'])
def test_import_new_preset_and_select_history_protocol(tmp_path, proto):
    app = QApplication.instance() or QApplication([])
    db = Database(str(tmp_path / 'history-import.db'))
    coll_id = db.add_protocol_collection('测试', 'tcp_client')
    existing = {
        'tcp_client': [{'name': '已有 TCP', 'message': json.dumps(dict(
            proto='tcp_client', ip='127.0.0.1', port=8000, send_message='原 TCP'))}],
        'ws_client': [{'name': '已有 WS', 'message': json.dumps(dict(
            proto='ws_client', ws_url='ws://127.0.0.1:8000/ws', send_message='原 WS'))}],
        'http_client': [], '_active_proto': 'ws_client' if proto != 'ws_client' else 'tcp_client',
    }
    target_id = db.add_protocol_target(coll_id, name='目标', send_presets=json.dumps(existing))
    detail = _TargetDetailPanel(db)
    detail.set_target(db.get_protocol_target(target_id), db.get_protocol_collection(coll_id))
    params = dict(proto=proto, ip='192.0.2.1', port=9000, encoding='GBK',
                  recv_encoding='UTF-8', head_length=0, timeout=12,
                  ws_url='ws://example.com:9001/path', ws_timeout=7, ws_ssl=False,
                  send_message='旧草稿', message_format='json',
                  method='POST', url='http://example.com/api', headers=[['X-Test', 'yes']],
                  body_type='json', body={'type': 'json', 'text': '{"key": 1}'}, settings={'timeout': 9})
    request = '{"历史":true}'
    detail._hist_sessions = [SimpleNamespace(id=42, started_at='2026-10-01 22:00:00',
        protocol_type=proto, test_params=json.dumps(params), request=request)]
    detail._import_history_config(0)
    client = detail._client_panel
    assert client._proto_combo.currentData() == proto
    assert detail._tabs.currentIndex() == 0
    selected = client.get_presets()[client._selected_preset_idx]
    restored = json.loads(selected['message'])
    assert restored == history_client_config(json.dumps(params), proto, request)
    assert selected['name'].startswith('历史配置')
    if proto != 'http_client':
        assert client._send_edit.toPlainText() == request
    else:
        assert client._http_params.get_config()['url'] == params['url']
        assert client._http_params.get_config()['method'] == 'POST'
        assert client._http_params.get_config()['body'] == params['body']
    stored = json.loads(db.get_protocol_target(target_id).send_presets)
    for protocol in ('tcp_client', 'ws_client'):
        assert stored[protocol][0] == existing[protocol][0]
    detail._import_history_config(0)
    presets = client.get_presets()
    assert len({p['name'] for p in presets}) == len(presets)
    assert json.loads(presets[-1]['message']) == restored
    detail.close()


def test_import_missing_snapshot_does_not_create_config(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    db = Database(str(tmp_path / 'missing.db'))
    coll_id = db.add_protocol_collection('测试', 'tcp_client')
    target_id = db.add_protocol_target(coll_id, name='目标')
    detail = _TargetDetailPanel(db)
    detail.set_target(db.get_protocol_target(target_id), db.get_protocol_collection(coll_id))
    before = db.get_protocol_target(target_id).send_presets
    detail._hist_sessions = [SimpleNamespace(protocol_type='tcp_client', test_params='{}', request='text')]
    warnings = []
    monkeypatch.setattr('src.ui.protocol_panel.QMessageBox.warning', lambda *args: warnings.append(args))
    detail._import_history_config(0)
    assert warnings
    assert db.get_protocol_target(target_id).send_presets == before
    detail.close()
