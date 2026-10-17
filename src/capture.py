"""通过 TShark 被动采集 TCP，不监听业务端口、不转发或修改业务流量。"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from src.proxy import validate_config
from src.recording import ExchangeRecorder, MAX_BYTES

FIELDS = (
    "frame.time_epoch", "tcp.stream", "ip.src", "ipv6.src", "tcp.srcport",
    "ip.dst", "ipv6.dst", "tcp.dstport", "tcp.seq_raw", "tcp.flags.syn",
    "tcp.flags.ack", "tcp.flags.fin", "tcp.flags.reset", "tcp.len", "tcp.payload",
    "frame.cap_len", "frame.len",
)


def validate_capture_config(config):
    # 分帧校验沿用录制协议配置；抓包不使用代理的地址。
    validate_config(dict(config, listen_host="127.0.0.1", listen_port=1,
                         upstream_host="127.0.0.1", upstream_port=1))
    if config.get("capture_mode") not in ("local", "ssh", "file"):
        raise ValueError("请选择本机抓包、SSH 远端抓包或抓包文件")
    if not 1 <= int(config.get("capture_port", 0)) <= 65535:
        raise ValueError("采集端口必须在 1 到 65535 之间")
    if config.get("capture_host"):
        ipaddress.ip_address(config["capture_host"])
    if config["capture_mode"] == "file":
        if not Path(config.get("capture_file", "")).is_file():
            raise ValueError("请选择存在的 pcap / pcapng 文件")
    elif not config.get("capture_interface", "").strip():
        raise ValueError("请输入采集网卡（Linux 可填 any，其他平台可填 TShark 网卡编号）")
    if config["capture_mode"] == "ssh":
        if not re.fullmatch(r"[A-Za-z0-9_.@:\[\]-]+", config.get("ssh_host", "")) or config["ssh_host"].startswith("-"):
            raise ValueError("SSH 地址应为 user@host 或 ~/.ssh/config 中的主机别名")
        if not 1 <= int(config.get("ssh_port", 22)) <= 65535:
            raise ValueError("SSH 端口无效")
        if int(config["capture_port"]) == int(config.get("ssh_port", 22)):
            raise ValueError("采集端口不能与 SSH 管理端口相同，避免把采集传输再次录入；请改用抓包文件导入")
        if config.get("ssh_key") and not Path(config["ssh_key"]).is_file():
            raise ValueError("SSH 私钥文件不存在")


def tshark_executable(config):
    value = config.get("tshark_path", "").strip()
    if value:
        return value
    found = shutil.which("tshark")
    if found:
        return found
    for path in (Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Wireshark/tshark.exe",
                 Path("/Applications/Wireshark.app/Contents/MacOS/tshark")):
        if path.is_file():
            return str(path)
    raise ValueError("未找到 TShark。请安装 Wireshark（含 TShark），或填写可执行文件完整路径。")


def capture_command(config):
    """本地 TShark：SSH 模式从标准输入解析远端 pcap 流。"""
    validate_capture_config(config)
    mode, port = config["capture_mode"], config["capture_port"]
    executable = tshark_executable(config)
    args = [executable, "-l", "-n", "-o", "tcp.desegment_tcp_streams:FALSE"]
    display = "tcp.port == {}".format(port)
    host = config.get("capture_host", "")
    if host:
        display += " && {}.addr == {}".format("ipv6" if ":" in host else "ip", host)
    if mode == "file":
        args += ["-r", config["capture_file"]]
    elif mode == "ssh":
        args += ["-r", "-"]
    else:
        # 不使用端口 capture filter，避免丢弃不带 TCP 头的 IP 分片；显示过滤在重组后生效。
        args += ["-i", config["capture_interface"], "-s", "0", "-f", "ip proto 6 or ip6"]
    args += ["-Y", display, "-T", "fields", "-E", "occurrence=f", "-E", "quote=n"]
    for field in FIELDS:
        args += ["-e", field]
    return args


def ssh_capture_command(config):
    """远端只运行 tcpdump，pcap 二进制与错误输出分别走 stdout/stderr。"""
    validate_capture_config(config)
    ssh = shutil.which("ssh")
    if not ssh:
        raise ValueError("未找到 OpenSSH 客户端 ssh")
    capture_filter = "tcp port {}".format(config["capture_port"])
    if config.get("capture_host"):
        capture_filter += " and host " + config["capture_host"]
    tcpdump = [config.get("tcpdump_path", "").strip() or "tcpdump", "-U", "-n", "-s", "0",
               "-i", config["capture_interface"], "-w", "-", capture_filter]
    # 不分配 PTY，防止二进制流被终端转换；输入通道关闭即停止远端进程。
    remote = ("exec 3<&0; " + " ".join(shlex.quote(arg) for arg in tcpdump) +
              " < /dev/null & capture_pid=$!; "
              "(cat <&3 >/dev/null; kill \"$capture_pid\" 2>/dev/null) & watcher_pid=$!; "
              "wait \"$capture_pid\"; result=$?; kill \"$watcher_pid\" 2>/dev/null; exit \"$result\"")
    args = [ssh, "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=2",
            "-p", str(config.get("ssh_port", 22))]
    if config.get("ssh_key"):
        args += ["-i", config["ssh_key"]]
    return args + [config["ssh_host"], remote]


@dataclass
class Packet:
    timestamp: float
    stream: str
    source: tuple
    destination: tuple
    sequence: int
    syn: bool
    ack: bool
    fin: bool
    reset: bool
    payload: bytes
    truncated: bool = False

    @classmethod
    def from_line(cls, line):
        values = line.rstrip("\r\n").split("\t")
        if len(values) != len(FIELDS):
            raise ValueError("TShark 输出字段不完整，请检查 TShark 版本")
        t, stream, ip, ip6, sport, dst, dst6, dport, seq, syn, ack, fin, rst, size, data, captured, original = values
        payload = bytes.fromhex(data.replace(":", ""))
        return cls(float(t), stream, (ip or ip6, int(sport)), (dst or dst6, int(dport)),
                   int(seq), syn.lower() in ("1", "true"), ack.lower() in ("1", "true"),
                   fin.lower() in ("1", "true"), rst.lower() in ("1", "true"), payload,
                   int(size) != len(payload) or int(captured) < int(original))


def endpoint(address):
    host, port = address
    return "[{}]:{}".format(host, port) if ":" in host else "{}:{}".format(host, port)


class TcpDirection:
    """按序列号增量重组，去除重传并缓存乱序；缺口不拼接成伪报文。"""

    def __init__(self):
        self.next_seq = None
        self.pending = []
        self.fin_seq = None
        self.closed = False
        self.prefix = bytearray()
        self.history = b""

    def feed(self, packet):
        if packet.syn and self.next_seq is None:
            self.next_seq = (packet.sequence + 1) & 0xffffffff
        if self.next_seq is None:
            raise ValueError("未捕获此方向 SYN，无法确定流起点；请在建立业务连接前开始抓包")
        seq = (packet.sequence + int(packet.syn)) & 0xffffffff
        delta = ((seq - self.next_seq + 0x80000000) & 0xffffffff) - 0x80000000
        data = packet.payload
        if self.closed:
            if data and delta + len(data) > 0:
                raise ValueError("FIN 后出现新数据")
            return b"", False
        if packet.fin:
            self.fin_seq = (seq + len(data)) & 0xffffffff
        if delta < 0:
            # 检查最近 64 KB 的重叠内容，不接受互相矛盾的重传。
            lo, hi = max(delta, -len(self.history)), min(0, delta + len(data))
            if lo < hi and self.history[len(self.history) + lo:len(self.history) + hi] != data[lo - delta:hi - delta]:
                raise ValueError("TCP 重传内容冲突")
            data, delta = data[-delta:], 0
        if data:
            for offset, previous in self.pending:
                lo, hi = max(offset, delta), min(offset + len(previous), delta + len(data))
                if lo < hi and previous[lo - offset:hi - offset] != data[lo - delta:hi - delta]:
                    raise ValueError("TCP 乱序片段内容冲突")
            if (delta, data) not in self.pending:
                self.pending.append((delta, data))
        if sum(len(data) for _, data in self.pending) > MAX_BYTES or len(self.pending) > 4096:
            raise ValueError("TCP 重组缓冲超限，可能存在抓包缺口")
        self.pending.sort(key=lambda item: item[0])
        output = bytearray()
        consumed = 0
        remaining = []
        for offset, data in self.pending:
            if offset > consumed:
                remaining.append((offset, data))
            elif offset + len(data) > consumed:
                output.extend(data[consumed - offset:])
                consumed = offset + len(data)
        self.pending = [(offset - consumed, data) for offset, data in remaining]
        self.next_seq = (self.next_seq + consumed) & 0xffffffff
        self.history = (self.history + output)[-65536:]
        self.prefix.extend(output[:max(0, 3 - len(self.prefix))])
        if len(self.prefix) >= 3 and self.prefix[0] in (20, 21, 22, 23) and self.prefix[1] == 3:
            raise ValueError("检测到 TLS 加密流量，仅保存原始数据，不能生成明文回放")
        finished = self.fin_seq == self.next_seq
        if finished:
            self.closed = True
        return bytes(output), finished


class PassiveRecorder:
    """TShark stream ID 区分端口复用；只把完整解析的消息交给关联器。"""

    def __init__(self, config, session_id, emit):
        self.config, self.session_id, self.emit = config, session_id, emit
        self.flows = {}
        self.error = ""
        self.packet_count = 0

    def feed(self, packet):
        if not (packet.payload or packet.syn or packet.fin or packet.reset or packet.truncated):
            return
        self.packet_count += 1
        flow = self.flows.get(packet.stream)
        if flow is None:
            if len(self.flows) >= 512:
                raise ValueError("单次录制超过 512 条连接，请分批采集")
            if packet.source[1] == packet.destination[1] == self.config["capture_port"]:
                if not packet.syn:
                    raise ValueError("两端端口相同且缺失握手，无法识别请求方向")
                client = packet.destination if packet.ack else packet.source
            else:
                client = packet.source if packet.destination[1] == self.config["capture_port"] else packet.destination
            server = packet.destination if client == packet.source else packet.source
            cid = uuid.uuid4().hex
            recorder = ExchangeRecorder(cid, self.config, self.emit)
            flow = dict(id=cid, client=client, recorder=recorder,
                        directions={d: TcpDirection() for d in ("request", "response")},
                        error="", timestamp=packet.timestamp, closed=False)
            self.flows[packet.stream] = flow
            self.emit("connection", (cid, self.session_id, endpoint(client), endpoint(server), packet.timestamp))
        flow["timestamp"] = packet.timestamp
        direction = "request" if packet.source == flow["client"] else "response"
        if packet.payload:
            # 保留每个抓到的片段，包括密文、重传和无法解析的数据；不允许作为业务消息配对。
            meta = dict(control=True, capture_segment=True, tcp_sequence=packet.sequence,
                        truncated=packet.truncated, source=endpoint(packet.source), destination=endpoint(packet.destination))
            self.emit("event", (uuid.uuid4().hex, flow["id"], direction, -self.packet_count,
                                packet.timestamp, packet.payload, packet.payload, json.dumps(meta)))
        if flow["error"] or flow["closed"]:
            return
        try:
            if packet.truncated:
                raise ValueError("抓包被截断，不能完整还原报文（请使用 snaplen=0）")
            data, eof = flow["directions"][direction].feed(packet)
            flow["recorder"].feed(direction, data, eof=eof, timestamp=packet.timestamp)
            if packet.reset:
                raise ValueError("TCP 连接被 RST 重置")
            if all(d.closed for d in flow["directions"].values()):
                self._close(flow)
            total = sum(sum(len(data) for _, data in d.pending) + len(d.history)
                        for f in self.flows.values() for d in f["directions"].values())
            total += sum(len(p.buffer) + len(p.fragment) + len(p.fragment_wire)
                         for f in self.flows.values() for p in f["recorder"].parsers.values())
            if total > 64 * 1024 * 1024:
                raise ValueError("抓包解析内存超过 64 MB，请缩小采集范围")
        except (ValueError, UnicodeError, IndexError) as exc:
            self._fail(flow, str(exc))

    def _fail(self, flow, error):
        flow["error"] = error
        self.error = error
        flow["recorder"].close("incomplete")
        self.emit("invalidate", (flow["id"],))
        self.emit("close", (flow["timestamp"], "incomplete", error, flow["id"]))
        # 后续仍保存数据包，但释放此连接的解析缓存。
        for parser in flow["recorder"].parsers.values():
            parser.buffer.clear()
            parser.fragment.clear()
            parser.fragment_wire.clear()
        for stream in flow["directions"].values():
            stream.pending.clear()
            stream.history = b""

    def _close(self, flow):
        if flow["error"] or flow["closed"]:
            return
        if any(d.pending or (d.fin_seq is not None and not d.closed) for d in flow["directions"].values()):
            self._fail(flow, "TCP 序列存在缺口，部分数据未捕获")
        elif any(p.buffer or p.fragment_opcode is not None for p in flow["recorder"].parsers.values()):
            self._fail(flow, "采集结束时存在未完成的报文；EOF 分帧必须捕获真实 FIN")
        else:
            flow["recorder"].close("no_response")
            status = "closed" if all(d.closed for d in flow["directions"].values()) else "capture_stopped"
            self.emit("close", (flow["timestamp"], status, "", flow["id"]))
            for stream in flow["directions"].values():
                stream.history = b""
        flow["closed"] = True

    def finish(self):
        for flow in self.flows.values():
            self._close(flow)


class PassiveCaptureEngine:
    def __init__(self, config, session_id, emit, on_status, on_error):
        validate_capture_config(config)
        self.config, self.on_status, self.on_error = config, on_status, on_error
        self.recorder = PassiveRecorder(config, session_id, emit)
        self.cancel_event = threading.Event()
        self.process = None
        self.source_process = None
        self.lock = threading.Lock()
        self.error = self.recording_error = ""

    def start(self):
        try:
            command = capture_command(self.config)
            source_command = ssh_capture_command(self.config) if self.config["capture_mode"] == "ssh" else None
            with tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as source_errors:
                try:
                    with self.lock:
                        if self.cancel_event.is_set():
                            return
                        options = dict(start_new_session=os.name != "nt",
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                        if source_command:
                            self.source_process = subprocess.Popen(source_command, stdout=subprocess.PIPE,
                                                                   stderr=source_errors, stdin=subprocess.PIPE, **options)
                        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors,
                                                        stdin=self.source_process.stdout if self.source_process else subprocess.PIPE,
                                                        **options)
                        if self.source_process:
                            # 管道直接交给本地 TShark，父进程不持有多余读端，也不缓存 pcap。
                            self.source_process.stdout.close()
                    self.on_status("远端 tcpdump → 本地 TShark 已启动，等待流量…" if source_command
                                   else "被动采集已启动，等待匹配端口的 TCP 流量…")
                    for raw in self.process.stdout:
                        if self.cancel_event.is_set():
                            break
                        self.recorder.feed(Packet.from_line(raw.decode("utf-8")))
                        if self.recorder.packet_count % 50 == 0 or self.recorder.packet_count == 1:
                            self.on_status("已采集 {} 个数据/连接包，{} 条连接".format(
                                self.recorder.packet_count, len(self.recorder.flows)))
                    if not self.cancel_event.is_set():
                        result = self.process.wait()
                        source_result = self.source_process.wait(timeout=5) if self.source_process else 0
                        if result or source_result:
                            details = []
                            for label, file in (("本地 TShark", errors), ("SSH / 远端 tcpdump", source_errors)):
                                file.seek(0)
                                message = file.read(8192).decode("utf-8", errors="replace").strip()
                                if message:
                                    details.append(label + ":\n" + message)
                            raise ValueError("采集失败（检查工具路径、网卡、远端抓包权限或 SSH 认证）：\n" + "\n".join(details))
                finally:
                    self.stop()
                    for process in (self.process, self.source_process):
                        if process:
                            for pipe in (process.stdin, process.stdout):
                                if pipe:
                                    pipe.close()
                            process.wait(timeout=5)
        except Exception as exc:
            self.error = str(exc)
            self.on_error(self.error)
        finally:
            self.recorder.finish()
            self.recording_error = self.recorder.error
            if self.recording_error:
                self.on_error(self.recording_error)

    def stop(self):
        self.cancel_event.set()
        with self.lock:
            processes = (self.source_process, self.process)
        for process in processes:
            if process and process.poll() is None:
                try:
                    if process.stdin:
                        process.stdin.close()
                    if os.name == "nt":
                        process.terminate()
                    else:
                        os.killpg(process.pid, signal.SIGTERM)
                except OSError:
                    continue
                # GUI 调用不等待；分别回收 SSH 和 TShark，防止无流量时退出挂起。
                threading.Thread(target=self._reap, args=(process,), daemon=True).start()

    @staticmethod
    def _reap(process):
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            process.wait()
