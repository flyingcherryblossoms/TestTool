"""历史文件往返、旧版列兼容、批量追加和失败不写入。"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict

import pytest

from src.database import Database, ProtocolTestSession
from src.history_handler import HEADERS, read_history, write_history
from src.ui.history_worker import HistoryFileWorker


def records():
    return [ProtocolTestSession(id=0, protocol_type=proto, started_at='2026-10-01 12:13:14',
        target_ip='127.0.0.1', target_port=9000, success=index != 1,
        request='=原文,"中文"\n第二行', response='response', error_msg='error' if index == 1 else '',
        test_params=json.dumps({'proto': proto, **params}), request_raw=b'\x00\xff\r\n',
        response_raw=b'' if index == 1 else b'\x80') for index, (proto, params) in enumerate([
            ('tcp_client', {'ip': '127.0.0.1', 'port': 9000, 'head_length': 0}),
            ('ws_client', {'ws_url': 'ws://example.com', 'ws_timeout': 8}),
            ('http_client', {'method': 'POST', 'url': 'http://example.com',
                             'body': {'type': 'json', 'text': '{"中文":true}'}})])]


@pytest.mark.parametrize('extension', ['csv', 'xlsx', 'json'])
def test_history_roundtrip(extension, tmp_path):
    path = tmp_path / ('history.' + extension)
    source = records()
    write_history(path, source)
    restored = read_history(path)
    def normalized(row):
        result = asdict(row)
        result['test_params'] = json.loads(result['test_params'])
        return result
    assert [normalized(row) for row in restored] == [normalized(row) for row in source]


def test_old_csv_export_without_raw_bytes(tmp_path):
    path = tmp_path / 'legacy.csv'
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(HEADERS[:9])
        writer.writerow(['2026-10-01 12:13:14', 'TCP', '127.0.0.1', 9000, 'OK',
                         'request', 'response', '', '{"proto":"tcp_client","ws_url":"wrong"}'])
    restored = read_history(path)[0]
    assert restored.request == 'request'
    assert restored.request_raw is None
    assert 'ws_url' not in json.loads(restored.test_params)


@pytest.mark.parametrize('extension', ['csv', 'json'])
def test_large_and_control_character_messages_roundtrip(tmp_path, extension):
    source = records()
    source[0].request = 'a' * 200000 + '\x00'
    path = tmp_path / ('large.' + extension)
    write_history(path, source)
    assert read_history(path)[0].request == source[0].request


def test_excel_does_not_silently_truncate_messages(tmp_path):
    source = records()
    source[0].request = 'a' * 32768
    with pytest.raises(ValueError, match='CSV 或 JSON'):
        write_history(tmp_path / 'too-long.xlsx', source)


def test_worker_import_appends_to_current_target_and_preserves_time(tmp_path):
    db = Database(str(tmp_path / 'history.db'))
    collection = db.add_protocol_collection('当前集合', 'tcp_client')
    target = db.add_protocol_target(collection, name='当前目标')
    source = records() * 20
    path = tmp_path / 'history.json'
    write_history(path, source)
    worker = HistoryFileWorker(path, db=db, target=(collection, '当前集合', target))
    results = []
    worker.completed.connect(lambda count, error: results.append((count, error)))
    worker.run()
    assert results == [(60, '')]
    imported = db.get_protocol_test_sessions_by_target(target)
    assert len(imported) == 60
    assert all(s.started_at == source[0].started_at and s.target_id == target
               and s.collection_id == collection and s.collection_name == '当前集合' for s in imported)
    assert len({s.id for s in imported}) == 60
    worker.run()
    assert len(db.get_protocol_test_sessions_by_target(target)) == 120


def test_invalid_file_reports_row_and_leaves_database_unchanged(tmp_path):
    db = Database(str(tmp_path / 'invalid.db'))
    collection = db.add_protocol_collection('集合', 'tcp_client')
    target = db.add_protocol_target(collection)
    path = tmp_path / 'invalid.json'
    write_history(path, records())
    document = json.loads(path.read_text())
    document['records'][1]['测试参数'] = 'broken'
    path.write_text(json.dumps(document))
    worker = HistoryFileWorker(path, db=db, target=(collection, '集合', target))
    results = []
    worker.completed.connect(lambda count, error: results.append((count, error)))
    worker.run()
    assert results[0][0] == 0 and '第 3 行' in results[0][1]
    assert not db.get_protocol_test_sessions_by_target(target)


def test_deleted_target_is_not_recreated_by_import(tmp_path):
    db = Database(str(tmp_path / 'deleted.db'))
    with pytest.raises(ValueError, match='已被删除'):
        db.import_protocol_test_sessions(records(), 99, '集合', 99)


def test_ui_import_file_keeps_destination_when_current_target_changes(tmp_path, monkeypatch):
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    from src.ui.protocol_panel import _TargetDetailPanel
    app = QApplication.instance() or QApplication([])
    db = Database(str(tmp_path / 'ui-import.db'))
    collection = db.add_protocol_collection('集合', 'tcp_client')
    first, second = (db.add_protocol_target(collection, name=name) for name in ['目标1', '目标2'])
    path = tmp_path / 'history.csv'
    write_history(path, records())
    monkeypatch.setattr('src.ui.protocol_panel.QFileDialog.getOpenFileName', lambda *args: (str(path), 'CSV'))
    messages = []
    loop = QEventLoop()
    def done(*args):
        messages.append(args)
        loop.quit()
    monkeypatch.setattr('src.ui.protocol_panel.QMessageBox.information', done)
    monkeypatch.setattr('src.ui.protocol_panel.QMessageBox.critical', done)
    detail = _TargetDetailPanel(db)
    coll = db.get_protocol_collection(collection)
    detail.set_target(db.get_protocol_target(first), coll)
    detail._import_history()
    worker = detail._history_file_worker
    detail.set_target(db.get_protocol_target(second), coll)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    worker.wait()
    app.processEvents()
    assert len(messages) == 1 and messages[0][1] == '导入完成'
    assert '3 条' in messages[0][2]
    assert len(db.get_protocol_test_sessions_by_target(first)) == 3
    assert not db.get_protocol_test_sessions_by_target(second)
    detail.close()
