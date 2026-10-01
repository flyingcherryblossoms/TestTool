"""历史文件读写在后台执行，避免 Excel 导入导出阻塞窗口。"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from src.history_handler import read_history, write_history


class HistoryFileWorker(QThread):
    completed = Signal(int, str)

    def __init__(self, filepath, sessions=None, db=None, target=None, parent=None):
        super().__init__(parent)
        self.filepath, self.sessions, self.db, self.target = filepath, sessions, db, target

    def run(self):
        try:
            if self.sessions is not None:
                write_history(self.filepath, self.sessions)
                count = len(self.sessions)
            else:
                sessions = read_history(self.filepath)
                count = self.db.import_protocol_test_sessions(sessions, *self.target)
            self.completed.emit(count, '')
        except Exception as exc:
            self.completed.emit(0, str(exc))
