"""代理录制/离线回放的本机集成测试，所有数据库均为临时文件。"""
import json
import socket
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from src.database import Database
from src.protocol import read_message_bytes
from src.proxy import RecordingProxyEngine, RecordingWriter
from src.recording import ExchangeRecorder, MessageParser, ReplayRule
from src.replay import ReplayServerEngine, build_reply, match_rule, validate_rule, ws_frame


def config(**kwargs):
    result = dict(protocol="tcp", framing="length", head_length=5, delimiter="0a",
                  encoding="UTF-8", association_field="", timeout=3,
                  listen_host="127.0.0.1", listen_port=free_port(),
                  upstream_host="127.0.0.1", upstream_port=free_port())
    result.update(kwargs)
    return result


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def frame(data):
    return str(len(data)).zfill(5).encode() + data


def rule(protocol="tcp", request=b'{"code":"Q","id":"old"}', response=b'{"id":"old","ok":true}', **kwargs):
    result = dict(id=1, session_id=1, name="查询", protocol=protocol, request=request,
                  response=response, request_meta="{}", response_meta="{}", mode="exact",
                  fields="", copy_fields="", delay_ms=0, enabled=1)
    result.update(kwargs)
    return ReplayRule(**result)


@contextmanager
def server(handler):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(20)
    listener.settimeout(0.1)
    stop = threading.Event()
    workers, errors = [], []

    def run_client(sock):
        try:
            with sock:
                sock.settimeout(3)
                handler(sock)
        except Exception as exc:
            errors.append(exc)

    def accept():
        while not stop.is_set():
            try:
                sock, _ = listener.accept()
            except socket.timeout:
                continue
            thread = threading.Thread(target=run_client, args=(sock,))
            workers.append(thread)
            thread.start()

    thread = threading.Thread(target=accept)
    thread.start()
    try:
        yield listener.getsockname()[1]
    finally:
        stop.set()
        thread.join(5)
        listener.close()
        for worker in workers:
            worker.join(5)
        assert not thread.is_alive()
        assert not any(worker.is_alive() for worker in workers)
        assert not errors


@contextmanager
def running(engine):
    thread = threading.Thread(target=engine.start)
    thread.start()
    try:
        assert engine.ready.wait(5), engine.error
        yield engine
    finally:
        engine.stop()
        thread.join(5)
        assert not thread.is_alive()


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "recording.db")


def test_length_split_coalesce_and_binary():
    parser = MessageParser(dict(protocol="tcp", framing="length", head_length=5), "request")
    assert parser.feed(b"000") == []
    messages = parser.feed(b"03\x00\xff\x0100002ok")
    assert [p for _, p, _ in messages] == [b"\x00\xff\x01", b"ok"]
    assert not parser.buffer


def test_delimiter_and_eof():
    parser = MessageParser(dict(protocol="tcp", framing="delimiter", delimiter="0d0a"), "request")
    assert parser.feed(b"a\r") == []
    assert [p for _, p, _ in parser.feed(b"\nb\r\n")] == [b"a", b"b"]
    parser = MessageParser(dict(protocol="tcp", framing="eof"), "request")
    assert parser.feed(b"abc") == []
    assert parser.feed(b"", True)[0][1] == b"abc"


def test_http_chunked_keepalive_head_and_informational():
    methods = deque()
    cfg = dict(protocol="http")
    request = MessageParser(cfg, "request", methods)
    response = MessageParser(cfg, "response", methods)
    assert len(request.feed(b"HEAD /a HTTP/1.1\r\nHost: x\r\n\r\nGET /b HTTP/1.1\r\nHost: x\r\n\r\n")) == 2
    messages = response.feed(b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 200 OK\r\nContent-Length: 99\r\n\r\nHTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n0\r\nX-Test: y\r\n\r\n")
    assert len(messages) == 3
    assert messages[0][2]["informational"]
    assert messages[1][1] == b""
    assert messages[2][1] == b"abc"
    assert not methods


def masked_frame(payload, opcode=1, fin=True):
    mask = b"abcd"
    return bytes([(128 if fin else 0) | opcode, 128 | len(payload)]) + mask + bytes(v ^ mask[i % 4] for i, v in enumerate(payload))


def test_ws_handshake_mask_and_fragment():
    parser = MessageParser(dict(protocol="ws"), "request")
    handshake = b"GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n\r\n"
    assert parser.feed(handshake)[0][2]["handshake"]
    assert parser.feed(masked_frame(b"ab", fin=False))[0][2]["control"]
    assert parser.feed(masked_frame(b"cd", opcode=0))[0][1] == b"abcd"


def test_json_association_out_of_order_and_manual_pair(db):
    cfg = dict(protocol="tcp", framing="length", head_length=5, association_field="$.id")
    sid = db.create_recording_session("测试", json.dumps(cfg))
    actions = [("connection", ("conn", sid, "source", "upstream", time.time()))]
    recorder = ExchangeRecorder("conn", cfg, lambda kind, values: actions.append((kind, values)))
    for body in (b'{"id":1}', b'{"id":2}'):
        recorder.feed("request", frame(body))
    for body in (b'{"id":2,"ok":true}', b'{"id":1,"ok":true}', b'{"push":true}'):
        recorder.feed("response", frame(body))
    db.write_recording_batch(actions)
    exchanges = db.get_recording_exchanges(sid)
    assert [json.loads(e.response)["id"] for e in exchanges] == [1, 2]
    assert len(db.get_recording_events(sid)) == 5
    events = db.get_recording_events(sid)
    db.pair_recording_events(events[0].id, events[4].id)
    assert db.get_recording_exchanges(sid)[0].association == "manual"
    sid2 = db.create_recording_session("其他", "{}")
    db.write_recording_batch([("connection", ("c2", sid2, "s", "u", time.time())),
                              ("event", ("e2", "c2", "response", 1, time.time(), b"x", b"x", "{}"))])
    with pytest.raises(ValueError):
        db.pair_recording_events(events[0].id, "e2")
    db.delete_recording_session(sid)
    assert db.get_recording_events(sid) == []
    assert db.get_recording_exchanges(sid) == []


def test_duplicate_json_id_is_not_guessed():
    actions = []
    cfg = dict(protocol="tcp", framing="length", head_length=5, association_field="id")
    recorder = ExchangeRecorder("c", cfg, lambda *a: actions.append(a))
    recorder.feed("request", frame(b'{"id":1}') * 2)
    recorder.feed("response", frame(b'{"id":1}'))
    assert len(recorder.pending) == 2
    assert not any(k == "exchange" and v[4] == "complete" for k, v in actions)


def test_transparent_proxy_concurrent_persistent_binary(db):
    def upstream(sock):
        for _ in range(3):
            body = read_message_bytes(sock, 5)
            response = frame(body[::-1])
            sock.sendall(response[:3])
            sock.sendall(response[3:])

    errors = []
    with server(upstream) as port:
        cfg = config(upstream_port=port)
        sid = db.create_recording_session("TCP", json.dumps(cfg))
        writer = RecordingWriter(db, errors.append)
        writer.start()
        engine = RecordingProxyEngine(cfg, sid, writer.emit, lambda _: None, errors.append)
        with running(engine):
            def client(index):
                with socket.create_connection(("127.0.0.1", engine.bound_port), timeout=3) as sock:
                    for n in range(3):
                        body = bytes([index, n, 255, 0])
                        sock.sendall(frame(body))
                        assert read_message_bytes(sock, 5) == body[::-1]
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(client, range(4)))
        writer.stop()
    assert not errors
    exchanges = db.get_recording_exchanges(sid)
    assert len(exchanges) == 12
    assert all(e.status == "complete" and e.response == e.request[::-1] for e in exchanges)
    assert len({e.connection_id for e in exchanges}) == 4


def test_eof_half_close(db):
    def upstream(sock):
        data = bytearray()
        while True:
            chunk = sock.recv(100)
            if not chunk:
                break
            data.extend(chunk)
        sock.sendall(bytes(data).upper())

    errors = []
    with server(upstream) as port:
        cfg = config(upstream_port=port, framing="eof")
        sid = db.create_recording_session("EOF", json.dumps(cfg))
        writer = RecordingWriter(db, errors.append)
        writer.start()
        engine = RecordingProxyEngine(cfg, sid, writer.emit, lambda _: None, errors.append)
        with running(engine):
            with socket.create_connection(("127.0.0.1", engine.bound_port), timeout=3) as sock:
                sock.sendall(b"hello")
                sock.shutdown(socket.SHUT_WR)
                assert sock.recv(100) == b"HELLO"
                assert sock.recv(100) == b""
        writer.stop()
    exchanges = db.get_recording_exchanges(sid)
    assert len(exchanges) == 1
    assert exchanges[0].response == b"HELLO"
    assert not errors


def test_parser_failure_still_forwards(db):
    def upstream(sock):
        body = sock.recv(100)
        sock.sendall(body)

    errors = []
    with server(upstream) as port:
        cfg = config(upstream_port=port)
        sid = db.create_recording_session("无效帧", json.dumps(cfg))
        writer = RecordingWriter(db, errors.append)
        writer.start()
        engine = RecordingProxyEngine(cfg, sid, writer.emit, lambda _: None, errors.append)
        with running(engine):
            with socket.create_connection(("127.0.0.1", engine.bound_port), timeout=3) as sock:
                sock.sendall(b"bad-header")
                assert sock.recv(100) == b"bad-header"
        writer.stop()
    assert engine.recording_error
    assert db.get_recording_connections(sid)[0].status == "incomplete"


def test_json_mock_dynamic_copy_and_length_recalculation():
    r = rule(mode="json", fields="$.code", copy_fields="$.id")
    validate_rule(r)
    body = b'{"code":"Q","id":"a-much-longer-new-id"}'
    assert match_rule(r, body, {}, "UTF-8")
    errors = []
    cfg = config()
    engine = ReplayServerEngine(cfg, [r], lambda _: None, errors.append)
    with running(engine):
        with socket.create_connection(("127.0.0.1", engine.bound_port), timeout=3) as sock:
            sock.sendall(frame(body))
            assert json.loads(read_message_bytes(sock, 5))["id"] == "a-much-longer-new-id"
    assert not errors


def test_http_proxy_record_and_offline_mock(db):
    request = b"POST /query?a=1 HTTP/1.1\r\nHost: original\r\nContent-Length: 2\r\n\r\n{}"
    response = b"HTTP/1.1 201 Created\r\nTransfer-Encoding: chunked\r\nX-Test: yes\r\n\r\n2\r\nok\r\n0\r\n\r\n"

    def upstream(sock):
        received = bytearray()
        while len(received) < len(request):
            received.extend(sock.recv(100))
        assert bytes(received) == request
        sock.sendall(response)

    errors = []
    with server(upstream) as port:
        cfg = config(protocol="http", upstream_port=port)
        sid = db.create_recording_session("HTTP", json.dumps(cfg))
        writer = RecordingWriter(db, errors.append)
        writer.start()
        proxy = RecordingProxyEngine(cfg, sid, writer.emit, lambda _: None, errors.append)
        with running(proxy):
            with socket.create_connection(("127.0.0.1", proxy.bound_port), timeout=3) as sock:
                sock.sendall(request)
                received = bytearray()
                while len(received) < len(response):
                    received.extend(sock.recv(100))
                assert bytes(received) == response
        writer.stop()
    ex = db.get_recording_exchanges(sid)[0]
    assert ex.request == b"{}" and ex.response == b"ok"
    db.add_recording_rule(sid, "HTTP", "http", ex)
    cfg["listen_port"] = free_port()
    replay = ReplayServerEngine(cfg, db.get_recording_rules(sid), lambda _: None, errors.append)
    with running(replay):
        with socket.create_connection(("127.0.0.1", replay.bound_port), timeout=3) as sock:
            sock.sendall(request)
            parser = MessageParser(cfg, "response", deque(["POST"]))
            messages = []
            while not messages:
                messages.extend(parser.feed(sock.recv(100)))
            assert messages[0][1] == b"ok"
            assert messages[0][2]["status_code"] == 201
    assert not errors


def test_ws_offline_handshake_and_binary():
    errors = []
    cfg = config(protocol="ws")
    r = rule(protocol="ws", request=b"hello", response=b"\xff\x00", request_meta='{"opcode":1}', response_meta='{"opcode":2}')
    engine = ReplayServerEngine(cfg, [r], lambda _: None, errors.append)
    with running(engine):
        with socket.create_connection(("127.0.0.1", engine.bound_port), timeout=3) as sock:
            sock.sendall(b"GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n")
            response = bytearray()
            while b"\r\n\r\n" not in response:
                response.extend(sock.recv(100))
            assert b"s3pPLMBiTxaQ9kYGzzhZRbK+xOo=" in response
            sock.sendall(masked_frame(b"hello"))
            assert sock.recv(100) == ws_frame(b"\xff\x00", 2)
    assert not errors


def test_writer_overflow_and_database_failure(db):
    errors = []
    writer = RecordingWriter(db, errors.append, max_queue=1)
    writer.emit("connection", ("c", 999, "s", "u", 0))
    writer.emit("connection", ("c2", 999, "s", "u", 0))
    assert writer.failed.is_set() and "队列" in writer.error
    writer.start()
    writer.stop()
    assert errors
    errors.clear()
    writer = RecordingWriter(db, errors.append)
    writer.start()
    writer.emit("connection", ("c", 999, "s", "u", 0))
    writer.stop()
    assert writer.failed.is_set() and "数据库" in writer.error


def test_export_case_atomic_rollback(db):
    from src.recording import RecordingExchange
    ex = RecordingExchange("e", "c", "q", "a", "complete", "manual", 1, request=b"q", response=b"a")
    cfg = config()
    before = len(db.get_all_protocol_collections())
    with pytest.raises(Exception):
        db.export_recording_case("回测", "tcp_client", "{}", ex, cfg, "q", "a", {"invalid_column": "x"})
    assert len(db.get_all_protocol_collections()) == before
    tid = db.export_recording_case("回测", "tcp_client", "{}", ex, cfg, "q", "a")
    assert db.get_protocol_target(tid)
    assert db.get_protocol_test_sessions_by_target(tid)[0].response_raw == b"a"


def test_stop_idle_client_promptly(db):
    def upstream(sock):
        sock.recv(100)
    errors = []
    with server(upstream) as port:
        cfg = config(upstream_port=port, timeout=30)
        engine = RecordingProxyEngine(cfg, 1, lambda *a: None, lambda _: None, errors.append)
        with running(engine):
            with socket.create_connection(("127.0.0.1", engine.bound_port), timeout=3):
                started = time.monotonic()
                engine.stop()
        assert time.monotonic() - started < 2


def test_http_ambiguous_length_rejected():
    parser = MessageParser(dict(protocol="http"), "request")
    with pytest.raises(ValueError, match="歧义"):
        parser.feed(b"POST / HTTP/1.1\r\nContent-Length: 2\r\nTransfer-Encoding: chunked\r\n\r\n")


def test_replay_ambiguity_and_sequence():
    cfg = config(replay_policy="sequence")
    first, second = rule(request=b"q", response=b"a"), rule(request=b"q", response=b"b", id=2)
    errors = []
    engine = ReplayServerEngine(cfg, [first, second], lambda _: None, errors.append)
    with running(engine):
        for _ in range(2):
            with socket.create_connection(("127.0.0.1", engine.bound_port), timeout=3) as sock:
                sock.sendall(frame(b"q"))
                assert read_message_bytes(sock, 5) == b"a"
                sock.sendall(frame(b"q"))
                assert read_message_bytes(sock, 5) == b"b"
    assert not errors
    cfg["listen_port"] = free_port()
    cfg["replay_policy"] = "unique"
    engine = ReplayServerEngine(cfg, [first, second], lambda _: None, errors.append)
    with running(engine):
        with socket.create_connection(("127.0.0.1", engine.bound_port), timeout=3) as sock:
            sock.sendall(frame(b"q"))
            assert sock.recv(100) == b""
    assert any("多条" in e for e in errors)


def test_head_replay_keeps_declared_length():
    cfg = dict(protocol="http", encoding="UTF-8")
    r = rule(protocol="http", request=b"", response=b"", response_meta='{"status_code":200,"headers":[["Content-Length","99"]]}')
    wire = build_reply(cfg, r, b"", {"method": "HEAD"})
    assert b"Content-Length: 99" in wire
    assert wire.endswith(b"\r\n\r\n")


def test_ws_proxy_records_decoded_messages(db):
    request = b"GET /ws HTTP/1.1\r\nHost: original\r\nUpgrade: websocket\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
    response = b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n\r\n"
    message = masked_frame(b"hello")

    def upstream(sock):
        received = bytearray()
        while len(received) < len(request):
            received.extend(sock.recv(100))
        assert received == request
        sock.sendall(response)
        received = bytearray()
        while len(received) < len(message):
            received.extend(sock.recv(100))
        assert received == message
        sock.sendall(ws_frame(b"reply"))

    errors = []
    with server(upstream) as port:
        cfg = config(protocol="ws", upstream_port=port)
        sid = db.create_recording_session("WS", json.dumps(cfg))
        writer = RecordingWriter(db, errors.append)
        writer.start()
        engine = RecordingProxyEngine(cfg, sid, writer.emit, lambda _: None, errors.append)
        with running(engine):
            with socket.create_connection(("127.0.0.1", engine.bound_port), timeout=3) as sock:
                sock.sendall(request)
                received = bytearray()
                while len(received) < len(response):
                    received.extend(sock.recv(100))
                sock.sendall(message)
                received = bytearray()
                while len(received) < 7:
                    received.extend(sock.recv(100))
                assert received == ws_frame(b"reply")
        writer.stop()
    assert not errors
    ex = db.get_recording_exchanges(sid)[0]
    assert ex.request == b"hello" and ex.response == b"reply"
    assert len(db.get_recording_events(sid)) == 4


def test_large_preview_does_not_truncate_export(db, tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from src.ui.recording_workers import RecordingExportWorker
    app = QApplication.instance() or QApplication([])
    sid = db.create_recording_session("长报文", "{}")
    payload = b"x" * 10000
    db.write_recording_batch([("connection", ("c", sid, "s", "u", time.time())),
                              ("event", ("e", "c", "request", 1, time.time(), payload, payload, "{}"))])
    preview = db.get_recording_events(sid, preview=True)[0]
    assert len(preview.payload) == 256 and preview.payload_size == len(payload)
    path = tmp_path / "export.json"
    worker = RecordingExportWorker(db, db.get_recording_sessions()[0], str(path))
    worker.start()
    assert worker.wait(5000)
    import base64
    data = json.loads(path.read_text())
    assert base64.b64decode(data["events"][0]["payload_base64"]) == payload
    assert data["format"] == "testtool-recording"
    assert not list(tmp_path.glob(".testtool-recording-*.tmp"))


def test_upstream_unavailable_still_records_request(db):
    errors = []
    cfg = config()
    sid = db.create_recording_session("不可达", json.dumps(cfg))
    writer = RecordingWriter(db, errors.append)
    writer.start()
    engine = RecordingProxyEngine(cfg, sid, writer.emit, lambda _: None, errors.append)
    with running(engine):
        with socket.create_connection(("127.0.0.1", engine.bound_port), timeout=3) as sock:
            sock.sendall(frame(b"request"))
            assert sock.recv(100) == b""
    writer.stop()
    ex = db.get_recording_exchanges(sid)[0]
    assert ex.request == b"request" and ex.response is None and ex.status == "error"
    assert db.get_recording_connections(sid)[0].error


def test_ui_exports_complete_client_and_fixed_mock(db, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QInputDialog
    from src.database import target_display_info
    from src.ui.recording_panel import RecordingPanel
    app = QApplication.instance() or QApplication([])
    cfg = config()
    sid = db.create_recording_session("界面导出", json.dumps(cfg))
    actions = [("connection", ("c", sid, "s", "u", time.time()))]
    recorder = ExchangeRecorder("c", cfg, lambda *a: actions.append(a))
    recorder.feed("request", frame(b"q" * 1000))
    recorder.feed("response", frame(b"a" * 1000))
    db.write_recording_batch(actions)
    db.finish_recording_session(sid, "stopped")
    panel = RecordingPanel(db)
    panel.timer.stop()
    panel.refresh_sessions(select_id=sid)
    panel.exchange_table.selectRow(0)
    errors = []
    monkeypatch.setattr(panel, "_warning", errors.append)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("录制用例", True))
    panel._export_mock()
    assert not errors
    collection = next(c for c in db.get_all_protocol_collections() if c.name == "录制用例")
    target = db.get_protocol_targets(collection.id)[0]
    info = target_display_info(target)
    assert info["send_message"] == "q" * 1000
    assert info["head_length"] == 5
    servers = db.get_protocol_servers_by_target(target.id)
    assert len(servers) == 1 and servers[0].active_message() == "a" * 1000
    history = db.get_protocol_test_sessions_by_target(target.id)[0]
    assert history.response_raw == b"a" * 1000
    assert json.loads(history.test_params)["send_message"] == "q" * 1000
    panel.stop_all()
    panel.close()


def test_http_client_export_preserves_host_and_binary_expectation(db, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QInputDialog
    from src.ui.recording_panel import RecordingPanel
    app = QApplication.instance() or QApplication([])
    cfg = config(protocol="http")
    sid = db.create_recording_session("HTTP 导出", json.dumps(cfg))
    actions = [("connection", ("c", sid, "s", "u", time.time()))]
    recorder = ExchangeRecorder("c", cfg, lambda *a: actions.append(a))
    recorder.feed("request", b"GET /api HTTP/1.1\r\nHost: partner.example\r\n\r\n")
    recorder.feed("response", b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Encoding: gzip\r\n\r\n\xff\x00")
    db.write_recording_batch(actions)
    db.finish_recording_session(sid, "stopped")
    panel = RecordingPanel(db)
    panel.timer.stop()
    panel.refresh_sessions(select_id=sid)
    panel.exchange_table.selectRow(0)
    errors = []
    monkeypatch.setattr(panel, "_warning", errors.append)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("HTTP 用例", True))
    panel._export_client()
    assert not errors
    col = next(c for c in db.get_all_protocol_collections() if c.name == "HTTP 用例")
    target = db.get_protocol_targets(col.id)[0]
    preset = json.loads(json.loads(target.send_presets)["http_client"][0]["message"])
    assert ["Host", "partner.example"] in preset["headers"]
    assert db.get_protocol_test_sessions_by_target(target.id)[0].response_raw == b"\xff\x00"
    panel.stop_all()
    panel.close()
