"""联调录制的数据模型、流式分帧和请求响应关联。"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from collections import deque

MAX_BYTES = 10 * 1024 * 1024


@dataclass
class RecordingSession:
    id: int
    name: str
    config: str
    started_at: str
    ended_at: str
    status: str
    error: str


@dataclass
class RecordingConnection:
    id: str
    session_id: int
    source: str
    upstream: str
    opened_at: float
    closed_at: float | None
    status: str
    error: str


@dataclass
class RecordingExchange:
    id: str
    connection_id: str
    request_id: str
    response_id: str | None
    status: str
    association: str
    elapsed_ms: float
    source: str = ""
    upstream: str = ""
    request: bytes = b""
    response: bytes | None = None
    request_meta: str = "{}"
    response_meta: str = "{}"


@dataclass
class RecordingEvent:
    id: str
    connection_id: str
    direction: str
    sequence: int
    timestamp: float
    wire: bytes
    payload: bytes
    metadata: str
    payload_size: int = 0


@dataclass
class ReplayRule:
    id: int
    session_id: int
    name: str
    protocol: str
    request: bytes
    response: bytes
    request_meta: str
    response_meta: str
    mode: str
    fields: str
    copy_fields: str
    delay_ms: int
    enabled: int


def json_field(data, path):
    """支持 $.a.b 和数组数字索引；缺失字段不能参与匹配。"""
    for key in (path[2:] if path.startswith("$.") else path).split("."):
        data = data[int(key)] if isinstance(data, list) else data[key]
    return data


def set_json_field(data, path, value):
    keys = path[2:].split(".") if path.startswith("$.") else path.split(".")
    for key in keys[:-1]:
        data = data[int(key)] if isinstance(data, list) else data[key]
    data[int(keys[-1]) if isinstance(data, list) else keys[-1]] = value


class MessageParser:
    """只观察流量，不参与转发；完整帧保留 wire，业务报文保留 payload。"""

    def __init__(self, config, direction, methods=None):
        self.config = config
        self.direction = direction
        self.buffer = bytearray()
        self.methods = methods if methods is not None else deque()
        self.ws = False
        self.fragment = bytearray()
        self.fragment_wire = bytearray()
        self.fragment_opcode = None

    def feed(self, data, eof=False):
        self.buffer.extend(data)
        result = []
        while self.buffer:
            message = self._next(eof)
            if message is None:
                break
            result.append(message)
        if len(self.buffer) > MAX_BYTES:
            raise ValueError("录制报文超过 10 MB，停止解析此连接")
        return result

    def _next(self, eof):
        proto = self.config.get("protocol", "tcp")
        if proto in ("http", "ws"):
            return self._websocket() if self.ws else self._http(eof)
        framing = self.config.get("framing", "length")
        if framing == "length":
            size = self.config.get("head_length", 5)
            if len(self.buffer) < size:
                return None
            header = bytes(self.buffer[:size])
            if not header.isdigit():
                raise ValueError("无效长度头；检查代理分帧配置")
            count = int(header)
            if count > MAX_BYTES:
                raise ValueError("长度头指定的报文超过 10 MB")
            end = size + count
            if len(self.buffer) < end:
                return None
            wire = bytes(self.buffer[:end])
            del self.buffer[:end]
            return wire, wire[size:], {}
        if framing == "delimiter":
            delimiter = bytes.fromhex(self.config.get("delimiter", "0a"))
            pos = self.buffer.find(delimiter)
            if pos < 0:
                return None
            end = pos + len(delimiter)
            if end > MAX_BYTES:
                raise ValueError("分隔符报文超过 10 MB")
            wire = bytes(self.buffer[:end])
            del self.buffer[:end]
            return wire, wire[:-len(delimiter)], {}
        if not eof:
            return None
        wire = bytes(self.buffer)
        self.buffer.clear()
        return wire, wire, {}

    def _http(self, eof):
        pos = self.buffer.find(b"\r\n\r\n")
        if pos < 0:
            if len(self.buffer) > 65536:
                raise ValueError("HTTP 头超过 64 KB")
            return None
        if pos > 65536:
            raise ValueError("HTTP 头超过 64 KB")
        lines = bytes(self.buffer[:pos]).decode("latin-1").split("\r\n")
        headers = []
        for line in lines[1:]:
            key, sep, value = line.partition(":")
            if not sep:
                raise ValueError("HTTP 头格式无效")
            headers.append([key.strip(), value.strip()])
        values = {k.lower(): v for k, v in headers}
        lengths = [v for k, v in headers if k.lower() == "content-length"]
        if len(set(lengths)) > 1 or (lengths and "transfer-encoding" in values):
            raise ValueError("HTTP 长度头存在歧义，不能可靠录制")
        start = pos + 4
        meta = {"headers": headers}
        request = self.direction == "request"
        if request:
            method, path, version = lines[0].split(" ", 2)
            meta.update(method=method, path=path, version=version)
            if method == "CONNECT":
                raise ValueError("暂不解析 HTTP CONNECT 隧道，请使用原始 TCP 转发")
            no_body = False
        else:
            version, code, *_ = lines[0].split(" ")
            code = int(code)
            meta.update(status_code=code, version=version)
            no_body = (100 <= code < 200 or code in (204, 304)
                       or bool(self.methods and self.methods[0] == "HEAD"))
        if no_body:
            end, body = start, b""
        elif "transfer-encoding" in values:
            if values["transfer-encoding"].lower() != "chunked":
                raise ValueError("暂不支持此 HTTP Transfer-Encoding")
            cursor, chunks = start, []
            while True:
                line_end = self.buffer.find(b"\r\n", cursor)
                if line_end < 0:
                    return None
                size = int(bytes(self.buffer[cursor:line_end]).split(b";", 1)[0], 16)
                if size < 0 or size > MAX_BYTES:
                    raise ValueError("HTTP chunk 长度无效")
                cursor = line_end + 2
                if size == 0:
                    if self.buffer[cursor:cursor + 2] == b"\r\n":
                        end = cursor + 2
                    else:
                        tail = self.buffer.find(b"\r\n\r\n", cursor)
                        if tail < 0:
                            return None
                        end = tail + 4
                    body = b"".join(chunks)
                    break
                if len(self.buffer) < cursor + size + 2:
                    return None
                if self.buffer[cursor + size:cursor + size + 2] != b"\r\n":
                    raise ValueError("HTTP chunk 结束符无效")
                chunks.append(bytes(self.buffer[cursor:cursor + size]))
                cursor += size + 2
        elif "content-length" in values:
            size = int(values["content-length"])
            if not 0 <= size <= MAX_BYTES:
                raise ValueError("HTTP Content-Length 无效")
            end = start + size
            if len(self.buffer) < end:
                return None
            body = bytes(self.buffer[start:end])
        elif request:
            end, body = start, b""
        elif eof:
            end, body = len(self.buffer), bytes(self.buffer[start:])
        else:
            return None
        if end > MAX_BYTES + 65536 or len(body) > MAX_BYTES:
            raise ValueError("HTTP 报文体超过 10 MB")
        wire = bytes(self.buffer[:end])
        del self.buffer[:end]
        if request:
            self.methods.append(method)
        elif code >= 200 and self.methods:
            self.methods.popleft()
        if self.config.get("protocol") == "ws":
            if (request and values.get("upgrade", "").lower() == "websocket") or (not request and code == 101):
                # 压缩扩展需要解压上下文，不能把压缩消息当作业务报文。
                if not request and "permessage-deflate" in values.get("sec-websocket-extensions", ""):
                    raise ValueError("WebSocket 压缩扩展暂不支持，请关闭 permessage-deflate")
                self.ws = True
                meta["handshake"] = True
        if not request and 100 <= code < 200 and code != 101:
            meta["informational"] = True
        return wire, body, meta

    def _websocket(self):
        b = self.buffer
        if len(b) < 2:
            return None
        fin, opcode = bool(b[0] & 128), b[0] & 15
        if b[0] & 112:
            raise ValueError("WebSocket 扩展帧暂不支持")
        masked, count, offset = bool(b[1] & 128), b[1] & 127, 2
        if count in (126, 127):
            width = 2 if count == 126 else 8
            if len(b) < offset + width:
                return None
            count = int.from_bytes(b[offset:offset + width], "big")
            offset += width
        if count > MAX_BYTES:
            raise ValueError("WebSocket 报文超过 10 MB")
        mask = bytes(b[offset:offset + 4]) if masked else None
        offset += 4 if masked else 0
        end = offset + count
        if len(b) < end:
            return None
        wire, body = bytes(b[:end]), bytes(b[offset:end])
        del b[:end]
        if masked:
            body = bytes(v ^ mask[i % 4] for i, v in enumerate(body))
        if opcode in (8, 9, 10):
            return wire, body, {"opcode": opcode, "control": True}
        if opcode in (1, 2):
            if self.fragment_opcode is not None:
                raise ValueError("WebSocket 分片顺序无效")
            self.fragment_opcode = opcode
        elif opcode != 0 or self.fragment_opcode is None:
            raise ValueError("WebSocket opcode 无效")
        self.fragment.extend(body)
        self.fragment_wire.extend(wire)
        if len(self.fragment) > MAX_BYTES:
            raise ValueError("WebSocket 分片报文超过 10 MB")
        if not fin:
            # 返回控制事件以保存分片原始字节，完成时再提供整条消息。
            return wire, b"", {"fragment": True, "control": True}
        body, wire, opcode = bytes(self.fragment), bytes(self.fragment_wire), self.fragment_opcode
        self.fragment.clear()
        self.fragment_wire.clear()
        self.fragment_opcode = None
        return wire, body, {"opcode": opcode}


class ExchangeRecorder:
    """连接内按业务字段或显式顺序关联；无法关联的响应保存为独立事件。"""

    def __init__(self, connection_id, config, emit):
        self.connection_id, self.config, self.emit = connection_id, config, emit
        self.pending = []
        self.sequence = 0
        methods = deque()
        self.parsers = {d: MessageParser(config, d, methods) for d in ("request", "response")}
        self.failed = False

    def feed(self, direction, data, eof=False):
        if self.failed:
            return
        for wire, payload, meta in self.parsers[direction].feed(data, eof):
            self.sequence += 1
            event_id, now = uuid.uuid4().hex, time.time()
            self.emit("event", (event_id, self.connection_id, direction, self.sequence,
                                now, wire, payload, json.dumps(meta, ensure_ascii=False)))
            if meta.get("control") or meta.get("handshake") or meta.get("informational"):
                continue
            field = self.config.get("association_field", "")
            key = None
            if field:
                try:
                    key = json.dumps(json_field(json.loads(payload.decode(self.config.get("encoding", "UTF-8"))), field), sort_keys=True)
                except (ValueError, KeyError, TypeError, IndexError):
                    pass
            if direction == "request":
                if len(self.pending) >= 1000:
                    raise ValueError("单连接未响应请求超过 1000 条，停止录制解析")
                eid = uuid.uuid4().hex
                self.pending.append((eid, event_id, now, key, time.monotonic()))
                self.emit("exchange", (eid, self.connection_id, event_id, None, "pending",
                                       "json:" + field if field else "sequence", 0.0))
            else:
                matches = [p for p in self.pending if not field or (key is not None and p[3] == key)]
                # 同一流水号重复且并发时不能猜测归属。
                if not matches or (field and len(matches) != 1):
                    continue
                pending = matches[0]
                self.pending.remove(pending)
                self.emit("exchange", (pending[0], self.connection_id, pending[1], event_id,
                                       "complete", "json:" + field if field else "sequence",
                                       max(0.0, (time.monotonic() - pending[4]) * 1000)))

    def close(self, status):
        for eid, req, _, _, _ in self.pending:
            self.emit("exchange", (eid, self.connection_id, req, None, status,
                                   "json:" + self.config["association_field"] if self.config.get("association_field") else "sequence", 0.0))
        self.pending.clear()

RECORDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS recording_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, config TEXT NOT NULL,
    started_at TEXT DEFAULT CURRENT_TIMESTAMP, ended_at TEXT DEFAULT '',
    status TEXT DEFAULT 'recording', error TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS recording_connections (
    id TEXT PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES recording_sessions(id) ON DELETE CASCADE,
    source TEXT NOT NULL, upstream TEXT NOT NULL, opened_at REAL NOT NULL,
    closed_at REAL, status TEXT DEFAULT 'connected', error TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS recording_events (
    id TEXT PRIMARY KEY, connection_id TEXT NOT NULL REFERENCES recording_connections(id) ON DELETE CASCADE,
    direction TEXT NOT NULL, sequence INTEGER NOT NULL, timestamp REAL NOT NULL,
    wire BLOB NOT NULL, payload BLOB NOT NULL, metadata TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recording_exchanges (
    id TEXT PRIMARY KEY, connection_id TEXT NOT NULL REFERENCES recording_connections(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL REFERENCES recording_events(id) ON DELETE CASCADE,
    response_id TEXT REFERENCES recording_events(id) ON DELETE SET NULL,
    status TEXT NOT NULL, association TEXT NOT NULL, elapsed_ms REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS recording_connections_session ON recording_connections(session_id);
CREATE INDEX IF NOT EXISTS recording_events_connection ON recording_events(connection_id, sequence);
CREATE INDEX IF NOT EXISTS recording_exchanges_connection ON recording_exchanges(connection_id);
CREATE TABLE IF NOT EXISTS recording_replay_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES recording_sessions(id) ON DELETE CASCADE,
    name TEXT NOT NULL, protocol TEXT NOT NULL, request BLOB NOT NULL, response BLOB NOT NULL,
    request_meta TEXT NOT NULL, response_meta TEXT NOT NULL,
    mode TEXT NOT NULL, fields TEXT DEFAULT '', copy_fields TEXT DEFAULT '',
    delay_ms INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1
);
"""
