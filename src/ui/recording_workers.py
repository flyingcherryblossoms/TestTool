"""录制代理与离线回放的 QThread 封装。"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from src.proxy import RecordingProxyEngine, RecordingWriter
from src.replay import ReplayServerEngine


class RecordingWorker(QThread):
    status_changed = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, db, session_id, config, parent=None):
        super().__init__(parent)
        self.db, self.session_id = db, session_id
        self.writer = RecordingWriter(db, self.error_occurred.emit)
        # 提前创建引擎，使开始后立即停止也不会丢失停止信号。
        self.engine = RecordingProxyEngine(config, session_id, self.writer.emit,
                                           self.status_changed.emit, self.error_occurred.emit)

    def run(self):
        self.writer.start()
        try:
            self.engine.start()
        finally:
            self.writer.stop()
            error = self.writer.error or self.engine.error or self.engine.recording_error
            try:
                self.db.finish_recording_session(self.session_id, "incomplete" if error else "stopped", error)
            except Exception as exc:
                self.error_occurred.emit("保存会话状态失败: " + str(exc))

    def stop(self):
        self.engine.stop()


class ReplayWorker(QThread):
    status_changed = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, config, rules, parent=None):
        super().__init__(parent)
        self.engine = ReplayServerEngine(config, rules, self.status_changed.emit, self.error_occurred.emit)

    def run(self):
        self.engine.start()

    def stop(self):
        self.engine.stop()


class RecordingExportWorker(QThread):
    """完整会话导出在后台执行，并通过临时文件原子替换目标。"""
    finished_all = Signal(bool, str)

    def __init__(self, db, session, path, parent=None):
        super().__init__(parent)
        self.db, self.session, self.path = db, session, path

    def run(self):
        import base64
        import json
        import os
        import tempfile
        from dataclasses import asdict
        from pathlib import Path
        temp_path = None
        try:
            events = []
            for event in self.db.get_recording_events(self.session.id):
                data = asdict(event)
                for key in ("wire", "payload"):
                    data[key + "_base64"] = base64.b64encode(data.pop(key)).decode("ascii")
                events.append(data)
            rules = []
            for rule in self.db.get_recording_rules(self.session.id):
                data = asdict(rule)
                for key in ("request", "response"):
                    data[key + "_base64"] = base64.b64encode(data.pop(key)).decode("ascii")
                rules.append(data)
            exchanges = [dict(id=e.id, connection_id=e.connection_id, request_id=e.request_id,
                              response_id=e.response_id, status=e.status, association=e.association,
                              elapsed_ms=e.elapsed_ms) for e in self.db.get_recording_exchanges(self.session.id, preview=True)]
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(Path(self.path).parent),
                                             prefix=".testtool-recording-", suffix=".tmp", delete=False) as file:
                temp_path = file.name
                json.dump(dict(format="testtool-recording", version=1, session=asdict(self.session),
                               connections=[asdict(c) for c in self.db.get_recording_connections(self.session.id)],
                               events=events, exchanges=exchanges, rules=rules), file, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.path)
            temp_path = None
            self.finished_all.emit(True, self.path)
        except Exception as exc:
            self.finished_all.emit(False, "导出录制失败: " + str(exc))
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
