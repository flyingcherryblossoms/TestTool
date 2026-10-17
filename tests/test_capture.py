"""被动抓包的重组、缺口保护、原始数据持久化和后台生命周期测试。"""
from __future__ import annotations

import json
import os
import shlex
import struct
import subprocess
import threading
import time
from pathlib import Path

import pytest

from src.capture import (Packet, PassiveRecorder, PassiveCaptureEngine, TcpDirection,
                         capture_command, ssh_capture_command, validate_capture_config)
from src.database import Database

CLIENT = ("10.0.0.1", 43000)
SERVER = ("10.0.0.2", 8000)


def config(**changes):
    cfg = dict(protocol="tcp", framing="length", head_length=5, delimiter="0a",
               encoding="UTF-8", association_field="", timeout=30,
               listen_host="127.0.0.1", listen_port=9000, upstream_host="unused", upstream_port=1,
               capture_mode="local", capture_port=8000, capture_host="", capture_interface="lo",
               tshark_path="tshark")
    cfg.update(changes)
    return cfg


def packet(seq, data=b"", response=False, timestamp=1000.0, **flags):
    return Packet(timestamp, flags.pop("stream", "0"), SERVER if response else CLIENT,
                  CLIENT if response else SERVER, seq, flags.get("syn", False),
                  flags.get("ack", False), flags.get("fin", False), flags.get("reset", False),
                  data, flags.get("truncated", False))


def handshake(recorder, stream="0"):
    recorder.feed(packet(100, syn=True, stream=stream))
    recorder.feed(packet(500, syn=True, ack=True, response=True, stream=stream))


def recorder(tmp_path, cfg=None):
    cfg = cfg or config()
    db = Database(tmp_path / "capture.db")
    sid = db.create_recording_session("被动采集", json.dumps(cfg))
    return db, sid, PassiveRecorder(cfg, sid, lambda kind, values: db.write_recording_batch([(kind, values)]))


def test_reordered_retransmitted_and_real_timestamps(tmp_path):
    db, sid, rec = recorder(tmp_path)
    handshake(rec)
    rec.feed(packet(105, b"3abc", timestamp=1000.1))
    rec.feed(packet(101, b"0000", timestamp=1000.2))
    rec.feed(packet(101, b"00003abc", timestamp=1000.3))
    rec.feed(packet(501, b"00002OK", response=True, timestamp=1000.45))
    rec.feed(packet(109, fin=True, timestamp=1000.5))
    rec.feed(packet(508, response=True, fin=True, timestamp=1000.6))
    rec.finish()
    exchanges = db.get_recording_exchanges(sid)
    assert len(exchanges) == 1
    assert exchanges[0].request == b"abc"
    assert exchanges[0].response == b"OK"
    assert exchanges[0].elapsed_ms == pytest.approx(250)
    assert exchanges[0].source == "10.0.0.1:43000"
    events = db.get_recording_events(sid)
    messages = [e for e in events if not json.loads(e.metadata).get("control")]
    assert sorted(e.timestamp for e in messages) == [1000.2, 1000.45]
    assert len(events) == 6  # 四个原始片段与两个业务报文。
    assert db.get_recording_connections(sid)[0].status == "closed"


def test_missing_segment_invalidates_previously_paired_exchange(tmp_path):
    db, sid, rec = recorder(tmp_path)
    handshake(rec)
    rec.feed(packet(101, b"00001A"))
    rec.feed(packet(501, b"00001B", response=True))
    rec.feed(packet(110, b"00001C"))
    rec.finish()
    assert db.get_recording_exchanges(sid)[0].status == "incomplete"
    assert "缺口" in rec.error
    ex = db.get_recording_exchanges(sid)[0]
    with pytest.raises(ValueError):
        db.add_recording_rule(sid, "不完整", "tcp", ex)


@pytest.mark.parametrize("data,flags,reason", [
    (b"00001A", {}, "SYN"),
    (b"\x16\x03\x03\x00\x03abc", {"handshake": True}, "TLS"),
    (b"00001", {"handshake": True, "truncated": True}, "截断"),
])
def test_unusable_capture_retains_raw_data(tmp_path, data, flags, reason):
    db, sid, rec = recorder(tmp_path)
    if flags.pop("handshake", False):
        handshake(rec)
    rec.feed(packet(101, data, **flags))
    rec.finish()
    assert reason in rec.error
    events = db.get_recording_events(sid)
    assert len(events) == 1 and events[0].wire == data
    assert json.loads(events[0].metadata)["capture_segment"]
    assert not db.get_recording_exchanges(sid)


def test_stop_does_not_fabricate_eof(tmp_path):
    db, sid, rec = recorder(tmp_path, config(framing="eof"))
    handshake(rec)
    rec.feed(packet(101, b"abc"))
    rec.finish()
    assert "FIN" in rec.error
    assert not db.get_recording_exchanges(sid)


def test_fin_can_arrive_before_missing_bytes():
    stream = TcpDirection()
    stream.feed(packet(100, syn=True))
    assert stream.feed(packet(104, fin=True)) == (b"", False)
    assert stream.feed(packet(101, b"abc")) == (b"abc", True)


def test_sequence_wrap_and_conflicting_retransmission():
    stream = TcpDirection()
    stream.feed(packet(0xfffffffd, syn=True))
    assert stream.feed(packet(0, b"cd")) == (b"", False)
    assert stream.feed(packet(0xfffffffe, b"ab")) == (b"abcd", False)
    assert stream.feed(packet(0xfffffffe, b"abcd")) == (b"", False)
    with pytest.raises(ValueError, match="冲突"):
        stream.feed(packet(0, b"XX"))


def test_json_correlation_out_of_order_and_port_reuse(tmp_path):
    db, sid, rec = recorder(tmp_path, config(association_field="$.id"))
    handshake(rec)
    req = b'00008{"id":1}00008{"id":2}'
    rsp = b'00008{"id":2}00008{"id":1}'
    rec.feed(packet(101, req))
    rec.feed(packet(501, rsp, response=True))
    handshake(rec, "1")
    rec.feed(packet(101, b'00008{"id":3}', stream="1"))
    rec.feed(packet(501, b'00008{"id":3}', response=True, stream="1"))
    rec.finish()
    ex = db.get_recording_exchanges(sid)
    assert len(ex) == 3 and all(e.request == e.response and e.status == "complete" for e in ex)
    assert len(db.get_recording_connections(sid)) == 2


def test_http_and_ipv6(tmp_path):
    db, sid, rec = recorder(tmp_path, config(protocol="http"))
    packets = [packet(100, syn=True), packet(500, syn=True, ack=True, response=True),
               packet(101, b"GET /q HTTP/1.1\r\nHost: example\r\n\r\n"),
               packet(501, b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK", response=True)]
    for p in packets:
        p.source = ("::1", p.source[1])
        p.destination = ("::2", p.destination[1]) if p.destination[1] == 8000 else ("::1", p.destination[1])
        if p.source[1] == 8000:
            p.source = ("::2", 8000)
        rec.feed(p)
    rec.finish()
    ex = db.get_recording_exchanges(sid)[0]
    assert ex.status == "complete" and ex.response == b"OK"
    assert ex.upstream == "[::2]:8000"


def test_command_no_shell_injection(tmp_path):
    cfg = config(capture_mode="ssh", ssh_host="user@server", ssh_port=22,
                 capture_interface="any; touch /tmp/not-a-command", tshark_path="/local/my tools/tshark",
                 tcpdump_path="/remote/my tools/tcpdump")
    cmd = ssh_capture_command(cfg)
    remote = cmd[-1]
    assert shlex.quote(cfg["capture_interface"]) in remote
    assert shlex.quote(cfg["tcpdump_path"]) in remote
    assert cfg["tshark_path"] not in remote
    local_parser = capture_command(cfg)
    assert local_parser[0] == cfg["tshark_path"]
    assert local_parser[local_parser.index("-r") + 1] == "-"
    assert "-i" not in local_parser
    assert "-U" in remote and "tcp port 8000" in remote
    assert "BatchMode=yes" in cmd and "StrictHostKeyChecking=no" not in cmd
    assert "cat <&3" in remote
    with pytest.raises(ValueError):
        capture_command(dict(cfg, ssh_host="-oProxyCommand=bad"))
    with pytest.raises(ValueError):
        capture_command(dict(cfg, capture_host="127.0.0.1 or tcp"))
    local = capture_command(config())
    assert "-i" in local and "-Y" in local and "-f" in local


def test_packet_fields():
    line = "1000.125\t0\t10.0.0.1\t\t43000\t10.0.0.2\t\t8000\t101\t0\t1\t0\t0\t2\t4142\t56\t56\n"
    p = Packet.from_line(line)
    assert p.payload == b"AB" and not p.truncated and p.ack
    assert p.timestamp == 1000.125


def test_stop_idle_subprocess(monkeypatch):
    import sys
    monkeypatch.setattr("src.capture.capture_command", lambda cfg: [sys.executable, "-c", "import time; time.sleep(60)"])
    errors = []
    engine = PassiveCaptureEngine(config(), 1, lambda *args: None, lambda msg: None, errors.append)
    thread = threading.Thread(target=engine.start)
    thread.start()
    deadline = time.monotonic() + 3
    while engine.process is None and time.monotonic() < deadline:
        time.sleep(0.01)
    engine.stop()
    thread.join(5)
    assert not thread.is_alive()
    assert engine.process.poll() is not None
    assert not errors


def write_pcap(path):
    """生成以太网/IPv4/TCP fixture，用真实 TShark 验证字段与协议入口。"""
    packets = [packet(100, syn=True), packet(500, syn=True, ack=True, response=True),
               packet(101, b"00003abc", timestamp=1000.1),
               packet(501, b"00002OK", response=True, timestamp=1000.35),
               packet(109, fin=True), packet(508, response=True, fin=True)]
    with open(path, "wb") as f:
        f.write(struct.pack("<IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1))
        for p in packets:
            source = bytes(int(x) for x in p.source[0].split("."))
            dest = bytes(int(x) for x in p.destination[0].split("."))
            flags = int(p.syn) * 2 + int(p.fin) + int(p.ack) * 16
            tcp = struct.pack("!HHIIBBHHH", p.source[1], p.destination[1], p.sequence, 0, 0x50, flags, 65535, 0, 0)
            ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 40 + len(p.payload), 0, 0, 64, 6, 0, source, dest)
            data = b"\x00" * 12 + b"\x08\x00" + ip + tcp + p.payload
            seconds = int(p.timestamp)
            f.write(struct.pack("<IIII", seconds, round((p.timestamp - seconds) * 1000000), len(data), len(data)))
            f.write(data)


@pytest.mark.skipif(not os.environ.get("TESTTOOL_TSHARK"), reason="需要本机 TShark")
def test_real_tshark_pcap_import(tmp_path):
    path = tmp_path / "traffic.pcap"
    write_pcap(path)
    cfg = config(capture_mode="file", capture_file=str(path), tshark_path=os.environ["TESTTOOL_TSHARK"])
    db = Database(tmp_path / "real.db")
    sid = db.create_recording_session("pcap", json.dumps(cfg))
    errors = []
    engine = PassiveCaptureEngine(cfg, sid, lambda k, v: db.write_recording_batch([(k, v)]), lambda msg: None, errors.append)
    engine.start()
    assert not errors
    exchanges = db.get_recording_exchanges(sid)
    assert len(exchanges) == 1 and exchanges[0].status == "complete"
    assert exchanges[0].response == b"OK"
    assert exchanges[0].elapsed_ms == pytest.approx(250)


def test_capture_ui_export_uses_observed_destination(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QInputDialog
    from src.ui.recording_panel import RecordingPanel
    app = QApplication.instance() or QApplication([])
    db, sid, rec = recorder(tmp_path)
    handshake(rec)
    rec.feed(packet(101, b"00003abc"))
    rec.feed(packet(501, b"00002OK", response=True))
    rec.finish()
    panel = RecordingPanel(db)
    panel.refresh_sessions(select_id=sid)
    panel.exchange_table.selectRow(0)
    errors = []
    monkeypatch.setattr(panel, "_warning", errors.append)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("抓包客户端", True))
    panel._export_client()
    assert not errors
    collections = db.get_all_protocol_collections()
    target = db.get_protocol_targets(collections[0].id)[0]
    preset = json.loads(json.loads(target.send_presets)["tcp_client"][0]["message"])
    assert preset["ip"] == "10.0.0.2" and preset["port"] == 8000
    assert panel.capture_mode.currentData() == "local"
    panel._load_config()
    assert panel.capture_port.value() == 8000
    panel.stop_all()
    panel.close()


def test_ssh_remote_monitor_stops_capture_on_disconnect(tmp_path):
    import sys
    fake = tmp_path / "fake tcpdump"
    fake.write_text("#!" + sys.executable + "\nimport os,time\nprint(os.getpid(), flush=True)\ntime.sleep(60)\n")
    fake.chmod(0o755)
    command = ssh_capture_command(config(capture_mode="ssh", ssh_host="user@host", tcpdump_path=str(fake)))
    process = subprocess.Popen(["sh", "-c", command[-1]], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        pid = int(process.stdout.readline())
        process.stdin.close()
        process.wait(timeout=5)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()


def test_tshark_failure_is_reported(monkeypatch):
    import sys
    monkeypatch.setattr("src.capture.capture_command", lambda cfg: [sys.executable, "-c",
                        "import sys; print('permission denied', file=sys.stderr); sys.exit(1)"])
    errors = []
    engine = PassiveCaptureEngine(config(), 1, lambda *args: None, lambda msg: None, errors.append)
    engine.start()
    assert errors and "permission denied" in errors[0]


def test_incomplete_capture_cannot_be_manually_promoted(tmp_path):
    db, sid, rec = recorder(tmp_path)
    handshake(rec)
    rec.feed(packet(101, b"00001A"))
    rec.feed(packet(501, b"00001B", response=True))
    rec.feed(packet(112, b"gap"))
    rec.finish()
    ex = db.get_recording_exchanges(sid)[0]
    with pytest.raises(ValueError, match="不完整"):
        db.pair_recording_events(ex.request_id, ex.response_id)


@pytest.mark.skipif(not os.environ.get("TESTTOOL_TSHARK"), reason="需要本机 TShark")
def test_tcpdump_stream_to_local_tshark_before_remote_exit(tmp_path, monkeypatch):
    import shutil
    tcpdump = shutil.which("tcpdump")
    if not tcpdump:
        pytest.skip("需要 tcpdump 进行流式集成验证")
    fixture = tmp_path / "remote.pcap"
    write_pcap(fixture)
    # 模拟远端已有的 tcpdump：真实 tcpdump 输出 pcap，之后连接保持打开。
    wrapper = tmp_path / "remote tcpdump"
    wrapper.write_text("#!/bin/sh\n" + shlex.quote(tcpdump) + " -U -n -r " + shlex.quote(str(fixture)) +
                       " -w - 'tcp port 8000'\nexec sleep 60\n")
    wrapper.chmod(0o755)
    cfg = config(capture_mode="ssh", ssh_host="unused", tcpdump_path=str(wrapper),
                 tshark_path=os.environ["TESTTOOL_TSHARK"])
    command = ssh_capture_command(cfg)
    # 本地执行远端命令，仅替代 SSH 传输，不替代 tcpdump/TShark 或二进制管道。
    monkeypatch.setattr("src.capture.ssh_capture_command", lambda c: ["sh", "-c", command[-1]])
    db = Database(tmp_path / "stream.db")
    sid = db.create_recording_session("流式", json.dumps(cfg))
    errors = []
    engine = PassiveCaptureEngine(cfg, sid, lambda k, v: db.write_recording_batch([(k, v)]), lambda m: None, errors.append)
    thread = threading.Thread(target=engine.start)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        exchanges = []
        while time.monotonic() < deadline:
            exchanges = db.get_recording_exchanges(sid)
            if exchanges and exchanges[0].status == "complete":
                break
            time.sleep(0.02)
        assert not errors
        assert len(exchanges) == 1 and exchanges[0].response == b"OK"
        assert thread.is_alive() and engine.source_process.poll() is None
        assert exchanges[0].elapsed_ms == pytest.approx(250)
    finally:
        engine.stop()
        thread.join(6)
    assert not thread.is_alive()
    assert engine.process.poll() is not None and engine.source_process.poll() is not None
    assert not errors


def test_ssh_failure_reports_remote_stderr(tmp_path, monkeypatch):
    import sys
    decoder = tmp_path / "decoder.py"
    decoder.write_text("import sys\nsys.stdin.buffer.read()\n")
    monkeypatch.setattr("src.capture.capture_command", lambda c: [sys.executable, str(decoder)])
    monkeypatch.setattr("src.capture.ssh_capture_command", lambda c: [sys.executable, "-c",
                        "import sys; print('tcpdump: permission denied', file=sys.stderr); sys.exit(7)"])
    errors = []
    engine = PassiveCaptureEngine(config(capture_mode="ssh", ssh_host="unused"), 1,
                                  lambda *a: None, lambda m: None, errors.append)
    engine.start()
    assert errors and "SSH / 远端 tcpdump" in errors[0] and "permission denied" in errors[0]
    assert engine.process.poll() is not None and engine.source_process.poll() is not None


def test_decoder_start_failure_cleans_up_ssh(monkeypatch):
    import sys
    monkeypatch.setattr("src.capture.capture_command", lambda c: ["/nonexistent/testtool-tshark"])
    monkeypatch.setattr("src.capture.ssh_capture_command", lambda c: [sys.executable, "-c", "import time; time.sleep(60)"])
    errors = []
    engine = PassiveCaptureEngine(config(capture_mode="ssh", ssh_host="unused"), 1,
                                  lambda *a: None, lambda m: None, errors.append)
    engine.start()
    assert errors and engine.source_process.poll() is not None
