"""HTTP 客户端组件 —— 集成到协议测试面板中，作为 HTTP 协议的客户端实现。

提供:
  HttpRequestWorker  — QThread Worker，与 TcpClientWorker/WsClientWorker 保持相同接口
  HttpParamWidget     — 紧凑的 HTTP 请求参数编辑区（Method + URL + Tabs）
"""

from __future__ import annotations

import json
import time
from datetime import datetime

import requests
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.ui import shortcuts
from src.ui.message_format import format_payload



HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]
BODY_TYPES = ["none", "x-www-form-urlencoded", "form-data", "json", "xml", "text", "binary"]

DEFAULT_HEADERS = [
    ("Content-Type", "application/json"),
    ("Accept", "*/*"),
    ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"),
    ("Cache-Control", "no-cache"),
]


# ── HTTP Worker ────────────────────────────────────────────────


class HttpRequestWorker(QThread):
    """HTTP 客户端请求 Worker —— 与 TcpClientWorker 保持相同信号接口。"""

    finished = Signal(bool, str)  # (success, response_text)

    def __init__(self, method: str, url: str,
                 headers: dict | None = None,
                 params: dict | None = None,
                 data=None, json_data=None, files=None,
                 auth=None, cookies: dict | None = None,
                 timeout: float = 30.0,
                 allow_redirects: bool = True,
                 verify_ssl: bool = True,
                 parent=None):
        super().__init__(parent)
        self._method = method.upper()
        self._url = url
        self._headers = headers or {}
        self._params = params or {}
        self._data = data
        self._json_data = json_data
        self._files = files
        self._auth = auth
        self._cookies = cookies or {}
        self._timeout = timeout
        self._allow_redirects = allow_redirects
        self._verify_ssl = verify_ssl

    def run(self) -> None:
        """在线程中执行 HTTP 请求。"""
        try:
            kwargs = {
                "method": self._method,
                "url": self._url,
                "headers": self._headers if self._headers else None,
                "params": self._params if self._params else None,
                "cookies": self._cookies if self._cookies else None,
                "timeout": self._timeout,
                "allow_redirects": self._allow_redirects,
                "verify": self._verify_ssl,
            }

            if self._json_data is not None:
                kwargs["json"] = self._json_data
            elif self._files is not None:
                kwargs["files"] = self._files
            elif self._data is not None:
                kwargs["data"] = self._data

            if self._auth is not None:
                kwargs["auth"] = self._auth

            start = time.perf_counter()
            resp = requests.request(**kwargs)
            elapsed = (time.perf_counter() - start) * 1000

            # 构建响应文本（含状态行和响应头）
            from http.client import responses
            status_text = responses.get(resp.status_code, "")
            lines = [
                f"HTTP/1.1 {resp.status_code} {status_text}",
            ]
            for k, v in resp.headers.items():
                lines.append(f"{k}: {v}")
            lines.append("")
            try:
                lines.append(resp.text)
            except Exception:
                lines.append(resp.content.decode("utf-8", errors="replace"))
            lines.append("")
            lines.append(f"--- 耗时: {elapsed:.1f}ms, 大小: {len(resp.content)} bytes ---")

            self.finished.emit(True, "\n".join(lines))

        except requests.exceptions.Timeout:
            self.finished.emit(False, f"请求超时 ({self._timeout}s)")
        except requests.exceptions.ConnectionError as e:
            self.finished.emit(False, f"连接失败: {e}")
        except requests.exceptions.SSLError as e:
            self.finished.emit(False, f"SSL 错误: {e}")
        except requests.exceptions.RequestException as e:
            self.finished.emit(False, f"请求错误: {e}")
        except Exception as e:
            self.finished.emit(False, f"未知错误: {e}")


# ── 紧凑型键值表格 ──────────────────────────────────────────


class _CompactKvTable(QWidget):
    """紧凑型键值表格 —— 列: [Key, Value]，用于 HTTP Headers/Params/Cookies。"""

    def __init__(self, columns: int = 2, labels: tuple | None = None, parent=None):
        super().__init__(parent)
        self._columns = columns
        self._labels = labels or ("参数名", "参数值")
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._table = QTableWidget()
        self._table.setColumnCount(self._columns)
        self._table.setHorizontalHeaderLabels(self._labels)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionsClickable(False)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self._table)

        bl = QHBoxLayout()
        bl.addWidget(QPushButton("添加", clicked=self._add_row))
        bl.addWidget(QPushButton("删除", clicked=self._delete_rows))
        bl.addStretch()
        layout.addLayout(bl)

    def _add_row(self):
        r = self._table.rowCount()
        self._table.insertRow(r)
        for c in range(self._columns):
            self._table.setItem(r, c, QTableWidgetItem(""))

    def _delete_rows(self):
        rows = sorted(set(i.row() for i in self._table.selectedIndexes()), reverse=True)
        for r in rows:
            self._table.removeRow(r)

    def get_dict(self) -> dict:
        result = {}
        for r in range(self._table.rowCount()):
            k = self._table.item(r, 0)
            v = self._table.item(r, 1)
            if k and k.text().strip():
                result[k.text().strip()] = v.text() if v else ""
        return result

    def set_data(self, data: list[tuple]):
        self._table.setRowCount(0)
        for r, (k, v) in enumerate(data):
            self._table.insertRow(r)
            self._table.setItem(r, 0, QTableWidgetItem(k))
            self._table.setItem(r, 1, QTableWidgetItem(v))

    def keyPressEvent(self, event):
        if shortcuts.event_matches(event, "delete"):
            self._delete_rows()
        else:
            super().keyPressEvent(event)


# ── HTTP 参数编辑区（紧凑型）────────────────────────────────


class HttpParamWidget(QWidget):
    """HTTP 请求参数编辑区 —— Method + URL + 紧凑 Tab 页（Headers/Params/Body/Auth/Cookies/设置）。"""

    method_changed = Signal(str)
    config_changed = Signal()
    send_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        # 默认添加常用 Headers（屏蔽变更信号）
        self.blockSignals(True)
        self._headers_table.set_data(DEFAULT_HEADERS)
        self.blockSignals(False)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # ── Row 1: Method + URL + 发送/终止 ──
        method_url = QHBoxLayout()
        self._method_combo = QComboBox()
        self._method_combo.addItems(HTTP_METHODS)
        self._method_combo.setEditable(True)
        self._method_combo.setFixedWidth(100)
        self._method_combo.currentTextChanged.connect(self.method_changed.emit)
        method_url.addWidget(QLabel("Method:"))
        method_url.addWidget(self._method_combo)
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("输入 URL, 例如 http://example.com/api")
        self._url_edit.setClearButtonEnabled(True)
        method_url.addWidget(self._url_edit, 1)
        self._http_send_btn = QPushButton("发送")
        self._http_send_btn.setFixedWidth(70)
        self._http_send_btn.clicked.connect(self.send_requested.emit)
        method_url.addWidget(self._http_send_btn)
        self._http_cancel_btn = QPushButton("终止")
        self._http_cancel_btn.setFixedWidth(70)
        self._http_cancel_btn.setVisible(False)
        self._http_cancel_btn.clicked.connect(self.cancel_requested.emit)
        method_url.addWidget(self._http_cancel_btn)
        layout.addLayout(method_url)

        # ── Row 2: 紧凑 Tab 页 ──
        self._tabs = QTabWidget()

        # Headers
        self._headers_table = _CompactKvTable(2, ("参数名", "参数值"))
        self._tabs.addTab(self._headers_table, "Headers")

        # Params
        self._params_table = _CompactKvTable(2, ("参数名", "参数值"))
        self._tabs.addTab(self._params_table, "Params")

        # Body
        self._body_widget = self._build_body_tab()
        self._tabs.addTab(self._body_widget, "Body")

        # Auth
        self._auth_widget = self._build_auth_tab()
        self._tabs.addTab(self._auth_widget, "Auth")

        # Cookies
        self._cookies_table = _CompactKvTable(2, ("参数名", "参数值"))
        self._tabs.addTab(self._cookies_table, "Cookies")

        # 设置
        self._settings_widget = self._build_settings_tab()
        self._tabs.addTab(self._settings_widget, "设置")

        layout.addWidget(self._tabs, 1)

        # ── 所有参数变更统一通知父组件 ──
        self._connect_change_signals()

        # 快捷键：格式化 Body 内容（可在设置中修改）
        self._format_shortcut = shortcuts.make_shortcut(
            self, "format_body", self._format_body)

    # ── 变更通知：所有可编辑控件统一连接 config_changed ──

    def _connect_change_signals(self):
        """将 Headers/Params/Body/Auth/Cookies/设置 的变更统一连接 config_changed。"""
        cc = self.config_changed.emit
        # 表格（Headers / Params / Cookies / URL-encoded body / form-data body）
        for tbl in (self._headers_table._table, self._params_table._table,
                    self._cookies_table._table, self._form_url_table._table,
                    self._form_data_table._table):
            tbl.itemChanged.connect(lambda *_: cc())
        # Body 编辑器（JSON / XML / Text）
        for edit in (self._json_edit, self._xml_edit, self._text_edit):
            edit.textChanged.connect(lambda *_: cc())
        # Body 类型 & 文件选择（文件选择在 _pick_file 中单独处理）
        self._body_type_combo.currentIndexChanged.connect(lambda *_: cc())
        # Auth
        self._auth_type_combo.currentIndexChanged.connect(lambda *_: cc())
        for le in (self._bearer_edit, self._basic_user, self._basic_pass,
                   self._apikey_key, self._apikey_value, self._digest_user,
                   self._digest_pass):
            le.textChanged.connect(lambda *_: cc())
        self._apikey_location.currentIndexChanged.connect(lambda *_: cc())
        # 设置
        self._timeout_spin.valueChanged.connect(lambda *_: cc())
        self._follow_redirects_cb.toggled.connect(lambda *_: cc())
        self._verify_ssl_cb.toggled.connect(lambda *_: cc())

    # ── Body Tab ────────────────────────────────────────

    def _build_body_tab(self) -> QWidget:
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(0, 0, 0, 0)
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Body 类型:"))
        self._body_type_combo = QComboBox()
        self._body_type_combo.addItems(BODY_TYPES)
        self._body_type_combo.currentIndexChanged.connect(self._on_body_type_changed)
        type_row.addWidget(self._body_type_combo)
        type_row.addStretch()
        self._body_format_btn = QPushButton("格式化")
        self._body_format_btn.setFixedWidth(60)
        self._body_format_btn.clicked.connect(self._format_body)
        self._body_format_btn.setVisible(False)  # none 类型隐藏
        type_row.addWidget(self._body_format_btn)
        vl.addLayout(type_row)

        self._body_stack = QStackedWidget()

        # none
        none_w = QWidget()
        nl = QVBoxLayout(none_w)
        nl.addWidget(QLabel("此请求没有 Body"))
        nl.addStretch()
        self._body_stack.addWidget(none_w)

        # x-www-form-urlencoded
        self._form_url_table = _CompactKvTable(2, ("参数名", "参数值"))
        self._body_stack.addWidget(self._form_url_table)

        # form-data
        self._form_data_table = _CompactKvTable(2, ("参数名", "参数值"))
        self._body_stack.addWidget(self._form_data_table)

        # json
        self._json_edit = QPlainTextEdit()
        self._json_edit.setPlaceholderText('{"key": "value"}')
        self._json_edit.setFont(QFont("Consolas", 10))
        self._body_stack.addWidget(self._json_edit)

        # xml
        self._xml_edit = QPlainTextEdit()
        self._xml_edit.setPlaceholderText('<root>\n    <key>value</key>\n</root>')
        self._xml_edit.setFont(QFont("Consolas", 10))
        self._body_stack.addWidget(self._xml_edit)

        # text
        self._text_edit = QPlainTextEdit()
        self._text_edit.setPlaceholderText("输入文本内容...")
        self._text_edit.setFont(QFont("Consolas", 10))
        self._body_stack.addWidget(self._text_edit)

        # binary
        bin_w = QWidget()
        bl = QVBoxLayout(bin_w)
        bl.setContentsMargins(0, 0, 0, 0)
        bh = QHBoxLayout()
        self._file_path_edit = QLineEdit()
        self._file_path_edit.setReadOnly(True)
        self._file_path_edit.setPlaceholderText("选择要发送的文件...")
        bh.addWidget(self._file_path_edit)
        bh.addWidget(QPushButton("选择文件...", clicked=self._pick_file))
        bl.addLayout(bh)
        bl.addStretch()
        self._body_stack.addWidget(bin_w)

        self._body_stack.setCurrentIndex(0)
        vl.addWidget(self._body_stack)
        return w

    def _on_body_type_changed(self, idx: int):
        self._body_stack.setCurrentIndex(idx)
        # 仅 json / xml / text 显示格式化按钮
        self._body_format_btn.setVisible(idx in (3, 4, 5))
        self.config_changed.emit()

    def _format_body(self):
        """按当前 Body 类型格式化编辑器内容。"""
        idx = self._body_type_combo.currentIndex()
        if idx == 3:  # json
            fmt = "json"
        elif idx == 4:  # xml
            fmt = "xml"
        else:  # text 或自动识别
            fmt = ""
        editor = self._body_stack.currentWidget()
        if not isinstance(editor, QPlainTextEdit):
            return
        text = editor.toPlainText()
        formatted, err = format_payload(text, fmt)
        if err:
            QMessageBox.warning(self, "格式化", err)
            return
        if formatted != text:
            editor.setPlainText(formatted)

    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", "所有文件 (*)")
        if path:
            self._file_path_edit.setText(path)
            self.config_changed.emit()

    # ── Auth Tab ────────────────────────────────────────

    def _build_auth_tab(self) -> QWidget:
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(0, 0, 0, 0)
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("鉴权类型:"))
        self._auth_type_combo = QComboBox()
        self._auth_type_combo.addItems(["No Auth", "Bearer Token", "Basic Auth", "API Key", "Digest Auth"])
        self._auth_type_combo.currentIndexChanged.connect(self._on_auth_type_changed)
        type_row.addWidget(self._auth_type_combo)
        type_row.addStretch()
        vl.addLayout(type_row)

        self._auth_stack = QStackedWidget()

        # No Auth
        na = QWidget()
        nal = QVBoxLayout(na)
        nal.addWidget(QLabel("不使用鉴权"))
        nal.addStretch()
        self._auth_stack.addWidget(na)

        # Bearer Token
        bt = QWidget()
        bf = QFormLayout(bt)
        self._bearer_edit = QLineEdit()
        self._bearer_edit.setPlaceholderText("输入 Bearer Token...")
        bf.addRow("Token:", self._bearer_edit)
        self._auth_stack.addWidget(bt)

        # Basic Auth
        ba = QWidget()
        baf = QFormLayout(ba)
        self._basic_user = QLineEdit()
        self._basic_user.setPlaceholderText("用户名")
        self._basic_pass = QLineEdit()
        self._basic_pass.setEchoMode(QLineEdit.Password)
        self._basic_pass.setPlaceholderText("密码")
        baf.addRow("用户名:", self._basic_user)
        baf.addRow("密码:", self._basic_pass)
        self._auth_stack.addWidget(ba)

        # API Key
        ak = QWidget()
        akf = QFormLayout(ak)
        self._apikey_key = QLineEdit()
        self._apikey_key.setPlaceholderText("Key 名称")
        self._apikey_value = QLineEdit()
        self._apikey_value.setPlaceholderText("Key 值")
        self._apikey_location = QComboBox()
        self._apikey_location.addItems(["Header", "Query Param"])
        akf.addRow("Key:", self._apikey_key)
        akf.addRow("Value:", self._apikey_value)
        akf.addRow("位置:", self._apikey_location)
        self._auth_stack.addWidget(ak)

        # Digest Auth
        da = QWidget()
        daf = QFormLayout(da)
        self._digest_user = QLineEdit()
        self._digest_user.setPlaceholderText("用户名")
        self._digest_pass = QLineEdit()
        self._digest_pass.setEchoMode(QLineEdit.Password)
        self._digest_pass.setPlaceholderText("密码")
        daf.addRow("用户名:", self._digest_user)
        daf.addRow("密码:", self._digest_pass)
        self._auth_stack.addWidget(da)

        self._auth_stack.setCurrentIndex(0)
        vl.addWidget(self._auth_stack)
        vl.addStretch()
        return w

    def _on_auth_type_changed(self, idx: int):
        self._auth_stack.setCurrentIndex(idx)
        self.config_changed.emit()

    # ── Settings Tab ────────────────────────────────────

    def _build_settings_tab(self) -> QWidget:
        w = QWidget()
        sf = QFormLayout(w)
        self._timeout_spin = QDoubleSpinBox()
        self._timeout_spin.setRange(0.1, 120.0)
        self._timeout_spin.setValue(30.0)
        self._timeout_spin.setSuffix("s")
        sf.addRow("超时:", self._timeout_spin)
        self._follow_redirects_cb = QCheckBox("跟随重定向")
        self._follow_redirects_cb.setChecked(True)
        sf.addRow("重定向:", self._follow_redirects_cb)
        self._verify_ssl_cb = QCheckBox("验证 SSL 证书")
        self._verify_ssl_cb.setChecked(True)
        sf.addRow("SSL:", self._verify_ssl_cb)
        return w

    # ── 公共方法 ─────────────────────────────────────────

    def get_method(self) -> str:
        return self._method_combo.currentText().strip().upper()

    def get_url(self) -> str:
        url = self._url_edit.text().strip()
        if url and not url.startswith(("http://", "https://")):
            url = "http://" + url
        return url

    def set_url(self, url: str):
        self._url_edit.setText(url)

    def get_headers_dict(self) -> dict:
        return self._headers_table.get_dict()

    def get_params_dict(self) -> dict:
        return self._params_table.get_dict()

    def get_body_data(self):
        """返回 (data, json_data, files, content_type_hint)。"""
        bt = self._body_type_combo.currentText()
        if bt == "none":
            return None, None, None, None
        elif bt == "x-www-form-urlencoded":
            return self._form_url_table.get_dict(), None, None, "application/x-www-form-urlencoded"
        elif bt == "form-data":
            return self._form_data_table.get_dict(), None, None, "multipart/form-data"
        elif bt == "json":
            text = self._json_edit.toPlainText().strip()
            if text:
                try:
                    return json.loads(text), None, None, "application/json"
                except json.JSONDecodeError:
                    return text, None, None, "application/json"
            return None, None, None, "application/json"
        elif bt == "xml":
            return self._xml_edit.toPlainText(), None, None, "application/xml"
        elif bt == "text":
            return self._text_edit.toPlainText(), None, None, "text/plain"
        elif bt == "binary":
            path = self._file_path_edit.text().strip()
            if path:
                try:
                    with open(path, "rb") as f:
                        return f.read(), None, None, "application/octet-stream"
                except OSError:
                    pass
            return None, None, None, None
        return None, None, None, None

    def get_auth(self):
        """返回 (auth, extra_headers, extra_params)。"""
        at = self._auth_type_combo.currentText()
        if at == "No Auth":
            return None, None, None
        elif at == "Bearer Token":
            token = self._bearer_edit.text().strip()
            return None, {"Authorization": f"Bearer {token}"}, None
        elif at == "Basic Auth":
            from requests.auth import HTTPBasicAuth
            return HTTPBasicAuth(
                self._basic_user.text().strip(),
                self._basic_pass.text()
            ), None, None
        elif at == "API Key":
            key = self._apikey_key.text().strip()
            value = self._apikey_value.text().strip()
            if self._apikey_location.currentText() == "Header":
                return None, {key: value}, None
            else:
                return None, None, {key: value}
        elif at == "Digest Auth":
            from requests.auth import HTTPDigestAuth
            return HTTPDigestAuth(
                self._digest_user.text().strip(),
                self._digest_pass.text()
            ), None, None
        return None, None, None

    def get_cookies_dict(self) -> dict:
        return self._cookies_table.get_dict()

    def get_settings(self) -> dict:
        return {
            "timeout": self._timeout_spin.value(),
            "allow_redirects": self._follow_redirects_cb.isChecked(),
            "verify_ssl": self._verify_ssl_cb.isChecked(),
        }

    def build_worker(self, parent=None) -> HttpRequestWorker:
        """构建 HTTP Worker，整合所有参数。"""
        headers = self.get_headers_dict()
        params = self.get_params_dict()
        data, json_data, files, ct_hint = self.get_body_data()
        auth, extra_headers, extra_params = self.get_auth()
        cookies = self.get_cookies_dict()
        settings = self.get_settings()

        # 合并 headers/params
        if extra_headers:
            headers.update(extra_headers)
        if extra_params:
            params.update(extra_params)

        # 自动设置 Content-Type
        if ct_hint and "content-type" not in {k.lower() for k in headers}:
            headers["Content-Type"] = ct_hint

        # JSON body 智能处理
        if data is not None and isinstance(data, str) and json_data is None:
            ct = headers.get("Content-Type", "")
            if "json" in ct and data.strip():
                try:
                    json_data = json.loads(data)
                    data = None
                except json.JSONDecodeError:
                    pass

        return HttpRequestWorker(
            method=self.get_method(),
            url=self.get_url(),
            headers=headers,
            params=params,
            data=data,
            json_data=json_data,
            files=files,
            auth=auth,
            cookies=cookies,
            timeout=settings["timeout"],
            allow_redirects=settings["allow_redirects"],
            verify_ssl=settings["verify_ssl"],
            parent=parent,
        )

    def set_sending_state(self, sending: bool):
        """设置发送中/空闲状态，控制按钮的启用/禁用。"""
        self._http_send_btn.setEnabled(not sending)
        self._http_send_btn.setText("发送中..." if sending else "发送")
        self._http_cancel_btn.setVisible(sending)

    def reset_send_button(self):
        """恢复发送按钮到初始状态。"""
        self._http_send_btn.setEnabled(True)
        self._http_send_btn.setText("发送")
        self._http_cancel_btn.setVisible(False)

    # ── 配置序列化（用于预设保存/加载）───────────────────

    @staticmethod
    def _get_table_data(table: QTableWidget) -> list:
        """读取表格所有行，返回 [[col0, col1, ...], ...]。"""
        rows = []
        for r in range(table.rowCount()):
            row = []
            for c in range(table.columnCount()):
                item = table.item(r, c)
                row.append(item.text() if item else "")
            rows.append(row)
        return rows

    @staticmethod
    def _set_table_data(table: QTableWidget, data: list):
        """从 [[col0, col1, ...], ...] 填充表格。"""
        table.setRowCount(0)
        for r, row in enumerate(data):
            table.insertRow(r)
            for c, val in enumerate(row):
                if c < table.columnCount():
                    table.setItem(r, c, QTableWidgetItem(str(val)))

    def _get_body_config(self) -> dict | None:
        """序列化 Body 配置。"""
        bt = self._body_type_combo.currentText()
        if bt == "none":
            return None
        elif bt in ("x-www-form-urlencoded", "form-data"):
            table = self._form_url_table if bt == "x-www-form-urlencoded" else self._form_data_table
            return {"type": bt, "data": self._get_table_data(table._table)}
        elif bt == "json":
            return {"type": bt, "text": self._json_edit.toPlainText()}
        elif bt == "xml":
            return {"type": bt, "text": self._xml_edit.toPlainText()}
        elif bt == "text":
            return {"type": bt, "text": self._text_edit.toPlainText()}
        elif bt == "binary":
            return {"type": bt, "path": self._file_path_edit.text()}
        return None

    def _set_body_config(self, config: dict | None):
        """从序列化数据恢复 Body 配置。"""
        if not config:
            idx = self._body_type_combo.findText("none")
            if idx >= 0:
                self._body_type_combo.setCurrentIndex(idx)
            return
        bt = config.get("type", "none")
        idx = self._body_type_combo.findText(bt)
        if idx >= 0:
            self._body_type_combo.setCurrentIndex(idx)
        if bt in ("x-www-form-urlencoded", "form-data"):
            table = self._form_url_table if bt == "x-www-form-urlencoded" else self._form_data_table
            self._set_table_data(table._table, config.get("data", []))
        elif bt == "json":
            self._json_edit.setPlainText(config.get("text", ""))
        elif bt == "xml":
            self._xml_edit.setPlainText(config.get("text", ""))
        elif bt == "text":
            self._text_edit.setPlainText(config.get("text", ""))
        elif bt == "binary":
            self._file_path_edit.setText(config.get("path", ""))

    def _get_auth_config(self) -> dict | None:
        """序列化 Auth 配置。"""
        at = self._auth_type_combo.currentText()
        if at == "No Auth":
            return None
        elif at == "Bearer Token":
            return {"type": at, "token": self._bearer_edit.text()}
        elif at == "Basic Auth":
            return {"type": at, "username": self._basic_user.text(),
                    "password": self._basic_pass.text()}
        elif at == "API Key":
            return {"type": at, "key": self._apikey_key.text(),
                    "value": self._apikey_value.text(),
                    "location": self._apikey_location.currentText()}
        elif at == "Digest Auth":
            return {"type": at, "username": self._digest_user.text(),
                    "password": self._digest_pass.text()}
        return None

    def _set_auth_config(self, config: dict | None):
        """从序列化数据恢复 Auth 配置。"""
        if not config:
            idx = self._auth_type_combo.findText("No Auth")
            if idx >= 0:
                self._auth_type_combo.setCurrentIndex(idx)
            return
        at = config.get("type", "No Auth")
        idx = self._auth_type_combo.findText(at)
        if idx >= 0:
            self._auth_type_combo.setCurrentIndex(idx)
        if at == "Bearer Token":
            self._bearer_edit.setText(config.get("token", ""))
        elif at == "Basic Auth":
            self._basic_user.setText(config.get("username", ""))
            self._basic_pass.setText(config.get("password", ""))
        elif at == "API Key":
            self._apikey_key.setText(config.get("key", ""))
            self._apikey_value.setText(config.get("value", ""))
            loc_idx = self._apikey_location.findText(config.get("location", "Header"))
            if loc_idx >= 0:
                self._apikey_location.setCurrentIndex(loc_idx)
        elif at == "Digest Auth":
            self._digest_user.setText(config.get("username", ""))
            self._digest_pass.setText(config.get("password", ""))

    def get_config(self) -> dict:
        """导出当前所有 HTTP 参数为可序列化的 dict，用于预设保存。"""
        return {
            "method": self._method_combo.currentText().strip().upper(),
            "url": self._url_edit.text().strip(),
            "headers": self._get_table_data(self._headers_table._table),
            "params": self._get_table_data(self._params_table._table),
            "body_type": self._body_type_combo.currentText(),
            "body": self._get_body_config(),
            "auth_type": self._auth_type_combo.currentText(),
            "auth": self._get_auth_config(),
            "cookies": self._get_table_data(self._cookies_table._table),
            "settings": self.get_settings(),
        }

    def set_config(self, config: dict):
        """从 dict 恢复所有 HTTP 参数（静默设置，不触发变更信号）。"""
        self.blockSignals(True)
        try:
            # Method
            method = config.get("method", "GET")
            idx = self._method_combo.findText(method)
            if idx >= 0:
                self._method_combo.setCurrentIndex(idx)
            else:
                self._method_combo.setCurrentText(method)

            # URL
            self._url_edit.setText(config.get("url", ""))

            # Headers
            headers = config.get("headers", [])
            self._set_table_data(self._headers_table._table,
                                 headers if headers else [list(h) for h in DEFAULT_HEADERS])

            # Params
            self._set_table_data(self._params_table._table, config.get("params", []))

            # Body
            self._set_body_config(config.get("body"))

            # Auth
            self._set_auth_config(config.get("auth"))

            # Cookies
            self._set_table_data(self._cookies_table._table, config.get("cookies", []))

            # Settings
            settings = config.get("settings", {})
            if settings:
                self._timeout_spin.setValue(settings.get("timeout", 30.0))
                self._follow_redirects_cb.setChecked(settings.get("allow_redirects", True))
            self._verify_ssl_cb.setChecked(settings.get("verify_ssl", True))
        finally:
            self.blockSignals(False)
