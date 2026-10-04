"""HTTP 服务端响应原始字节及十六进制详情回归检查。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import socket
import time

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.database import Database
from src.protocol import build_http_response
from src.ui.message_history import _hex_dump
from src.ui.protocol_components import ServerPanelBase


def wait_for(app, condition):
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert condition(), '等待 HTTP 服务端事件超时'


def request_response(port):
    # 网络操作在独立线程执行，GUI 线程持续处理 Worker 信号。
    with socket.create_connection(('127.0.0.1', port), timeout=3) as connection:
        connection.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
        chunks = []
        while True:
            chunk = connection.recv(4096)
            if not chunk:
                return b''.join(chunks)
            chunks.append(chunk)


@pytest.mark.parametrize('body', ['{"消息":"你好"}', ''])
def test_http_response_hex_matches_network_bytes(tmp_path, body):
    app = QApplication.instance() or QApplication([])
    db = Database(str(tmp_path / 'http-messages.db'))
    headers = [['X-Test', 'café'], ['Content-Type', 'application/json']]
    sid = db.add_protocol_server('HTTP', 'http_server', ip='127.0.0.1', port=0,
        response_messages=json.dumps([{'name': '响应', 'status_code': 201,
                                       'headers': headers, 'body': body}]))
    panel = ServerPanelBase(db)
    panel._toggle_server(db.get_protocol_server(sid))
    worker = panel._http_workers[sid]
    errors = []
    worker.error_occurred.connect(errors.append)
    try:
        wait_for(app, lambda: errors or (worker._engine is not None and worker._engine.is_running()))
        assert not errors
        port = worker._engine._server_sock.getsockname()[1]
        history = panel._histories[sid]
        # 在收到响应前打开十六进制，验证异步补充字节后会自动刷新。
        history.hex_toggle.setChecked(True)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(request_response, port)
            wait_for(app, future.done)
            received = future.result()
        wait_for(app, lambda: len(history.messages) == 2
                 and history.messages[-1]['raw'] is not None)
        assert not errors
        assert received == build_http_response(201, headers, body)
        record = history.messages[-1]
        assert record['direction'] == '发送'
        assert record['payload'] == body
        assert record['raw'] == received
        assert history.detail.toPlainText() == _hex_dump(received)
        # 接收记录与发送记录各自保留字节，切换后仍可查看。
        history.list.setCurrentRow(0)
        assert history.detail.toPlainText() == _hex_dump(history.messages[0]['raw'])
        history.list.setCurrentRow(1)
        assert history.detail.toPlainText() == _hex_dump(received)
        history.hex_toggle.setChecked(False)
        if body:
            assert json.loads(history.detail.toPlainText()) == json.loads(body)
        else:
            assert history.detail.toPlainText() == ''
        history.original_toggle.setChecked(True)
        assert history.detail.toPlainText().splitlines()[0] == 'HTTP/1.1 201 Created'
        panel._clear_log(sid)
        assert not history.messages
        assert history.detail.toPlainText() == ''
    finally:
        worker.stop_server()
        app.processEvents()
        panel.close()
