"""数据库层 —— SQLite 操作封装。

提供集合、目标、测试会话和测试结果的完整 CRUD 接口。
所有数据库操作均返回 dataclass 实例，方便上层使用。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


# ── 数据模型 ──────────────────────────────────────────────


@dataclass
class Collection:
    id: int
    name: str
    created_at: str = ""
    target_count: int = 0  # 非数据库字段，查询时动态计算


@dataclass
class Target:
    id: int
    ip: str
    port: int
    description: str = ""
    collection_id: Optional[int] = None
    collection_name: str = ""  # 非数据库字段，JOIN 时填充
    created_at: str = ""


@dataclass
class TestSession:
    id: int
    collection_id: Optional[int] = None
    collection_name: str = ""
    started_at: str = ""
    completed_at: str = ""
    total_count: int = 0
    success_count: int = 0
    fail_count: int = 0


@dataclass
class TestResult:
    id: int
    session_id: int
    target_id: int
    ip: str
    port: int
    description: str = ""
    success: bool = False
    latency_ms: float = 0.0
    error_msg: str = ""
    tested_at: str = ""


@dataclass
class ProtocolCollection:
    """协议测试集合。"""
    id: int
    name: str
    protocol_type: str = "tcp_client"  # tcp_client | ws_client
    created_at: str = ""


@dataclass
class ProtocolMessage:
    """协议测试集合内的消息模板。"""
    id: int
    collection_id: int
    direction: str = "send"           # "send" | "expected_response"
    message: str = ""
    sort_order: int = 0
    created_at: str = ""


@dataclass
class ProtocolServer:
    """持久化的协议服务端监听器配置。"""
    id: int
    name: str = ""
    server_type: str = ""             # "tcp_server" | "ws_server"
    ip: str = "0.0.0.0"
    port: int = 0
    encoding: str = "UTF-8"
    recv_encoding: str = "UTF-8"
    head_length: int = 0
    ws_path: str = ""
    response_mode: str = "fixed"      # "fixed" | "echo"
    response_message: str = ""
    response_delay: int = 0           # 响应延迟（毫秒）
    target_id: int | None = None      # 关联的协议目标
    sort_order: int = 0
    created_at: str = ""


@dataclass
class ProtocolTarget:
    """协议测试集合内的目标。

    配置（IP/端口/编码/超时/报文等）全部保存在 send_presets JSON 中，
    不再使用独立列存储。DisplayInfo 可按需解析预设获取展示字段。
    """
    id: int
    collection_id: int
    name: str = ""
    send_presets: str = "{}"          # JSON: {proto: [{name, message}, ...]}
    stress_params: str = "{}"         # JSON: {"concurrency":..,"qps_limit":..,..}
    sort_order: int = 0
    created_at: str = ""


@dataclass
class ProtocolTestSession:
    """协议测试会话记录。"""
    id: int
    collection_id: int | None = None
    collection_name: str = ""
    target_id: int | None = None
    protocol_type: str = ""
    target_ip: str = ""
    target_port: int = 0
    started_at: str = ""
    success: bool = False
    request: str = ""
    response: str = ""
    error_msg: str = ""


# ── SQL 建表语句 ──────────────────────────────────────────

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS connect_collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS connect_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER,
    ip TEXT NOT NULL,
    port INTEGER NOT NULL,
    description TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (collection_id) REFERENCES connect_collections(id) ON DELETE SET NULL
);

-- 兼容旧表: 添加排序列（新表已有则忽略）

CREATE TABLE IF NOT EXISTS connect_test_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER,
    collection_name TEXT DEFAULT '',
    started_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    completed_at TIMESTAMP,
    total_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    FOREIGN KEY (collection_id) REFERENCES connect_collections(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS connect_test_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    ip TEXT NOT NULL,
    port INTEGER NOT NULL,
    description TEXT DEFAULT '',
    success INTEGER NOT NULL DEFAULT 0,
    latency_ms REAL DEFAULT 0,
    error_msg TEXT DEFAULT '',
    tested_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (session_id) REFERENCES connect_test_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES connect_targets(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_targets_collection ON connect_targets(collection_id);
CREATE INDEX IF NOT EXISTS idx_results_session ON connect_test_results(session_id);
CREATE INDEX IF NOT EXISTS idx_results_status ON connect_test_results(success);

CREATE TABLE IF NOT EXISTS protocol_collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    protocol_type TEXT NOT NULL DEFAULT 'tcp_client',
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS protocol_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL,
    direction TEXT NOT NULL DEFAULT 'send',
    message TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (collection_id) REFERENCES protocol_collections(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS protocol_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    server_type TEXT NOT NULL DEFAULT 'tcp_server',
    ip TEXT DEFAULT '0.0.0.0',
    port INTEGER NOT NULL,
    encoding TEXT DEFAULT 'UTF-8',
    recv_encoding TEXT DEFAULT 'UTF-8',
    head_length INTEGER DEFAULT 0,
    ws_path TEXT DEFAULT '',
    response_mode TEXT DEFAULT 'fixed',
    response_message TEXT DEFAULT '',
    response_delay INTEGER DEFAULT 0,
    target_id INTEGER,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (target_id) REFERENCES protocol_targets(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_protocol_msgs_collection
    ON protocol_messages(collection_id);
CREATE INDEX IF NOT EXISTS idx_protocol_servers_type
    ON protocol_servers(server_type);

CREATE TABLE IF NOT EXISTS protocol_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    send_presets TEXT DEFAULT '{}',
    stress_params TEXT DEFAULT '{}',
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (collection_id) REFERENCES protocol_collections(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS protocol_test_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER,
    collection_name TEXT DEFAULT '',
    target_id INTEGER,
    protocol_type TEXT DEFAULT '',
    target_ip TEXT DEFAULT '',
    target_port INTEGER DEFAULT 0,
    started_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    success INTEGER DEFAULT 0,
    request TEXT DEFAULT '',
    response TEXT DEFAULT '',
    error_msg TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_protocol_targets_coll
    ON protocol_targets(collection_id);
CREATE INDEX IF NOT EXISTS idx_protocol_sessions_time
    ON protocol_test_sessions(started_at DESC);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
"""


# ── 辅助函数 ──────────────────────────────────────────────


def _protocol_target_from_row(r) -> ProtocolTarget:
    """从数据库行构建 ProtocolTarget，兼容新旧 schema。"""
    def _get(key, default):
        try:
            return r[key]
        except (KeyError, IndexError):
            return default

    return ProtocolTarget(
        id=_get("id", 0),
        collection_id=_get("collection_id", 0),
        name=_get("name", ""),
        send_presets=_get("send_presets", "{}"),
        stress_params=_get("stress_params", "{}"),
        sort_order=_get("sort_order", 0),
        created_at=_get("created_at", ""),
    )


def target_display_info(target: ProtocolTarget) -> dict:
    """从目标的 send_presets 中提取展示信息（IP、端口、协议类型、编码等）。

    返回 dict 包含: proto, ip, port, encoding, recv_encoding, head_length,
    timeout, ws_url, ws_ssl, send_message, url, http_method
    用于表格展示和旧代码兼容。
    """
    import json as _json
    try:
        all_presets = _json.loads(target.send_presets) if target.send_presets else {}
    except (_json.JSONDecodeError, TypeError):
        all_presets = {}

    if not isinstance(all_presets, dict):
        return _empty_display_info()

    # _active_proto 记录用户最后使用的协议，优先使用
    active = all_presets.get("_active_proto", "")
    proto_order = [active] + [p for p in ("tcp_client", "ws_client", "http_client") if p != active] if active else ("tcp_client", "ws_client", "http_client")

    # 按优先级查找有"默认配置"的协议
    for proto in proto_order:
        proto_presets = all_presets.get(proto, [])
        if not isinstance(proto_presets, list):
            continue
        default = next((p for p in proto_presets if p.get("name") == "默认配置"), None)
        if not default:
            # 取第一个预设作为展示数据
            default = proto_presets[0] if proto_presets else None
        if default:
            try:
                cfg = _json.loads(default.get("message", "{}"))
            except (_json.JSONDecodeError, TypeError):
                continue
            if isinstance(cfg, dict):
                cfg["_proto"] = proto
                # WS 目标字段以 ws_* 为准；兼容旧数据（对话框曾存 ws_path / timeout）
                if proto == "ws_client":
                    ws_url = cfg.get("ws_url", "") or cfg.get("ws_path", "")
                    timeout = cfg.get("ws_timeout", cfg.get("timeout", 30.0))
                else:
                    ws_url = cfg.get("ws_url", "")
                    timeout = cfg.get("timeout", 30.0)
                return {
                    "proto": proto,
                    "ip": cfg.get("ip", ""),
                    "port": cfg.get("port", 0),
                    "encoding": cfg.get("encoding", "UTF-8"),
                    "recv_encoding": cfg.get("recv_encoding", "UTF-8"),
                    "head_length": cfg.get("head_length", 0),
                    "timeout": timeout,
                    "ws_timeout": timeout,
                    "ws_url": ws_url,
                    "ws_ssl": cfg.get("ws_ssl", False),
                    "send_message": cfg.get("send_message", ""),
                    "url": cfg.get("url", ""),
                    "http_method": cfg.get("method", "GET"),
                }
    return _empty_display_info()


def _empty_display_info() -> dict:
    return {
        "proto": "tcp_client", "ip": "", "port": 0,
        "encoding": "UTF-8", "recv_encoding": "UTF-8",
        "head_length": 0, "timeout": 30.0, "ws_timeout": 30.0,
        "ws_url": "", "ws_ssl": False,
        "send_message": "", "url": "", "http_method": "GET",
    }


# ── Database 类 ───────────────────────────────────────────


class Database:
    """SQLite 数据库操作封装。"""

    def __init__(self, db_path: str | Path = ""):
        if not db_path:
            db_path = Path(__file__).parent.parent / "testtool.db"
        self.db_path = Path(db_path)
        self._init_db()

    # ── 初始化 ─────────────────────────────────────────────

    def _init_db(self) -> None:
        """创建数据库和表结构。"""
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            # 服务端响应延迟列：老库缺列时幂等补列（默认 0，不迁移历史数据）
            try:
                conn.execute(
                    "ALTER TABLE protocol_servers "
                    "ADD COLUMN response_delay INTEGER DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass  # 列已存在
            # 目标压测参数字段：老库缺列时幂等补列
            try:
                conn.execute(
                    "ALTER TABLE protocol_targets "
                    "ADD COLUMN stress_params TEXT DEFAULT '{}'"
                )
            except sqlite3.OperationalError:
                pass  # 列已存在
            cols = [r["name"] for r in conn.execute(
                "PRAGMA table_info(protocol_servers)").fetchall()]
            self._servers_have_delay = "response_delay" in cols
            # 列兼容
            for tbl, old, new in [("connect_test_sessions", "batch_name", "collection_name")]:
                try:
                    conn.execute(f"ALTER TABLE {tbl} RENAME COLUMN {old} TO {new}")
                except sqlite3.OperationalError:
                    pass
            for tbl in ("protocol_targets", "protocol_servers"):
                try:
                    conn.execute(f"ALTER TABLE {tbl} ADD COLUMN recv_encoding TEXT DEFAULT 'UTF-8'")
                except sqlite3.OperationalError:
                    pass
            for table, col in [
                ("connect_collections", "sort_order"),
                ("connect_targets", "sort_order"),
                ("protocol_collections", "sort_order"),
            ]:
                try:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {col} INTEGER DEFAULT 0"
                    )
                except sqlite3.OperationalError:
                    pass  # 列已存在
            # 协议测试会话新增请求报文字段
            try:
                conn.execute(
                    "ALTER TABLE protocol_test_sessions ADD COLUMN request TEXT DEFAULT ''"
                )
            except sqlite3.OperationalError:
                pass  # 列已存在
            # 集合不再需要描述列，旧数据库迁移时一并删除
            for table in ("connect_collections", "protocol_collections"):
                try:
                    conn.execute(f"ALTER TABLE {table} DROP COLUMN description")
                except sqlite3.OperationalError:
                    pass  # 列不存在

    def _connect(self) -> sqlite3.Connection:
        """获取数据库连接（启用 WAL 和外键）。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ── 连通测试集合操作 ──────────────────────────────────────

    def get_all_collections(self) -> list[Collection]:
        """获取所有集合，含目标计数。"""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT b.*, COUNT(t.id) AS target_count
                FROM connect_collections b
                LEFT JOIN connect_targets t ON t.collection_id = b.id
                GROUP BY b.id
                ORDER BY b.sort_order, b.created_at DESC
            """).fetchall()
            return [Collection(
                id=r["id"], name=r["name"],
                target_count=r["target_count"], created_at=r["created_at"]
            ) for r in rows]

    def get_collection(self, collection_id: int) -> Optional[Collection]:
        """获取单个集合。"""
        with self._connect() as conn:
            r = conn.execute("""
                SELECT b.*, COUNT(t.id) AS target_count
                FROM connect_collections b
                LEFT JOIN connect_targets t ON t.collection_id = b.id
                WHERE b.id = ? GROUP BY b.id
            """, (collection_id,)).fetchone()
            if r:
                return Collection(
                    id=r["id"], name=r["name"],
                    target_count=r["target_count"], created_at=r["created_at"]
                )
            return None

    def add_collection(self, name: str) -> int:
        """添加集合，返回新 ID。"""
        with self._connect() as conn:
            max_order = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) FROM connect_collections"
            ).fetchone()[0]
            cur = conn.execute(
                "INSERT INTO connect_collections (name, sort_order) VALUES (?, ?)",
                (name.strip(), max_order + 1)
            )
            return cur.lastrowid

    def update_collection(self, collection_id: int, name: str) -> None:
        """更新集合名称。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE connect_collections SET name = ? WHERE id = ?",
                (name.strip(), collection_id)
            )

    def delete_collection(self, collection_id: int) -> None:
        """删除集合（目标外键 ON DELETE SET NULL 保留不删）。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM connect_collections WHERE id = ?", (collection_id,))

    # ── 目标操作 ────────────────────────────────────────────

    def get_targets(self, collection_id: Optional[int] = None) -> list[Target]:
        """获取目标列表。`collection_id=None` 获取全部，`collection_id=0` 获取未分类。"""
        with self._connect() as conn:
            if collection_id is None:
                rows = conn.execute("""
                    SELECT t.*, b.name AS collection_name
                    FROM connect_targets t
                    LEFT JOIN connect_collections b ON t.collection_id = b.id
                    ORDER BY t.sort_order, t.created_at DESC
                """).fetchall()
            elif collection_id == 0:
                # 未分类：既包含未归属目标（NULL），也包含"未分类"集合内的目标
                uncat = conn.execute(
                    "SELECT id FROM connect_collections WHERE name = '未分类' LIMIT 1"
                ).fetchone()
                if uncat:
                    rows = conn.execute("""
                        SELECT t.*, b.name AS collection_name
                        FROM connect_targets t
                        LEFT JOIN connect_collections b ON t.collection_id = b.id
                        WHERE t.collection_id IS NULL OR t.collection_id = ?
                        ORDER BY t.sort_order, t.created_at DESC
                    """, (uncat["id"],)).fetchall()
                else:
                    rows = conn.execute("""
                        SELECT t.*, b.name AS collection_name
                        FROM connect_targets t
                        LEFT JOIN connect_collections b ON t.collection_id = b.id
                        WHERE t.collection_id IS NULL
                        ORDER BY t.sort_order, t.created_at DESC
                    """).fetchall()
            else:
                rows = conn.execute("""
                    SELECT t.*, b.name AS collection_name
                    FROM connect_targets t
                    LEFT JOIN connect_collections b ON t.collection_id = b.id
                    WHERE t.collection_id = ?
                    ORDER BY t.sort_order, t.created_at DESC
                """, (collection_id,)).fetchall()
            return [Target(
                id=r["id"], ip=r["ip"], port=r["port"],
                description=r["description"], collection_id=r["collection_id"],
                collection_name=r["collection_name"] or "", created_at=r["created_at"]
            ) for r in rows]

    def get_target(self, target_id: int) -> Optional[Target]:
        """获取单个目标。"""
        with self._connect() as conn:
            r = conn.execute("""
                SELECT t.*, b.name AS collection_name
                FROM connect_targets t LEFT JOIN connect_collections b ON t.collection_id = b.id
                WHERE t.id = ?
            """, (target_id,)).fetchone()
            if r:
                return Target(
                    id=r["id"], ip=r["ip"], port=r["port"],
                    description=r["description"], collection_id=r["collection_id"],
                    collection_name=r["collection_name"] or "", created_at=r["created_at"]
                )
            return None

    def target_exists(self, ip: str, port: int, collection_id: Optional[int] = None) -> bool:
        """检查目标是否已存在。"""
        with self._connect() as conn:
            if collection_id is not None:
                r = conn.execute(
                    "SELECT 1 FROM connect_targets WHERE collection_id = ? AND ip = ? AND port = ? LIMIT 1",
                    (collection_id, ip.strip(), port)
                ).fetchone()
            else:
                r = conn.execute(
                    "SELECT 1 FROM connect_targets WHERE collection_id IS NULL AND ip = ? AND port = ? LIMIT 1",
                    (ip.strip(), port)
                ).fetchone()
            return r is not None

    def find_target_id(self, ip: str, port: int, collection_id: Optional[int] = None) -> Optional[int]:
        """查找目标 ID，用于防重。"""
        with self._connect() as conn:
            if collection_id is not None:
                r = conn.execute(
                    "SELECT id FROM connect_targets WHERE collection_id = ? AND ip = ? AND port = ? LIMIT 1",
                    (collection_id, ip.strip(), port)
                ).fetchone()
            else:
                r = conn.execute(
                    "SELECT id FROM connect_targets WHERE collection_id IS NULL AND ip = ? AND port = ? LIMIT 1",
                    (ip.strip(), port)
                ).fetchone()
            return r["id"] if r else None

    def add_target(self, ip: str, port: int, description: str = "",
                   collection_id: Optional[int] = None) -> int:
        """添加目标，返回新 ID。"""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO connect_targets (ip, port, description, collection_id) VALUES (?, ?, ?, ?)",
                (ip.strip(), port, description, collection_id)
            )
            return cur.lastrowid

    def add_targets_batch(self, targets: list[tuple[str, int, str, Optional[int]]]) -> int:
        """批量添加目标，返回成功数量。`targets` 为 [(ip, port, desc, collection_id), ...]"""
        with self._connect() as conn:
            count = 0
            for ip, port, desc, collection_id in targets:
                conn.execute(
                    "INSERT INTO connect_targets (ip, port, description, collection_id) VALUES (?, ?, ?, ?)",
                    (ip.strip(), port, desc, collection_id)
                )
                count += 1
            return count

    def update_target(self, target_id: int, ip: str, port: int,
                      description: str = "", collection_id: Optional[int] = None) -> None:
        """更新目标。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE connect_targets SET ip = ?, port = ?, description = ?, collection_id = ? WHERE id = ?",
                (ip.strip(), port, description, collection_id, target_id)
            )

    def delete_target(self, target_id: int) -> None:
        """删除单个目标。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM connect_targets WHERE id = ?", (target_id,))

    def delete_targets(self, target_ids: list[int]) -> None:
        """批量删除目标。"""
        with self._connect() as conn:
            conn.executemany("DELETE FROM connect_targets WHERE id = ?", [(tid,) for tid in target_ids])

    def move_targets_to_collection(self, target_ids: list[int], collection_id: Optional[int]) -> None:
        """将目标移动/归类到指定集合。collection_id 为 None 则取消分类。"""
        with self._connect() as conn:
            conn.executemany(
                "UPDATE connect_targets SET collection_id = ? WHERE id = ?",
                [(collection_id, tid) for tid in target_ids]
            )

    # ── 测试会话操作 ───────────────────────────────────────

    def create_test_session(self, collection_id: Optional[int] = None,
                            collection_name: str = "") -> int:
        """创建测试会话，返回 session_id。"""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO connect_test_sessions (collection_id, collection_name) VALUES (?, ?)",
                (collection_id, collection_name)
            )
            return cur.lastrowid

    def complete_test_session(self, session_id: int, total: int, success: int, fail: int) -> None:
        """标记测试会话完成。"""
        with self._connect() as conn:
            conn.execute("""
                UPDATE connect_test_sessions
                SET completed_at = datetime('now', 'localtime'),
                    total_count = ?, success_count = ?, fail_count = ?
                WHERE id = ?
            """, (total, success, fail, session_id))

    def get_test_sessions(self, limit: int = 100) -> list[TestSession]:
        """获取测试会话列表，按时间倒序。"""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM connect_test_sessions
                ORDER BY started_at DESC LIMIT ?
            """, (limit,)).fetchall()
            return [TestSession(
                id=r["id"], collection_id=r["collection_id"],
                collection_name=r["collection_name"] or "",
                started_at=r["started_at"], completed_at=r["completed_at"] or "",
                total_count=r["total_count"], success_count=r["success_count"],
                fail_count=r["fail_count"]
            ) for r in rows]

    def get_test_session(self, session_id: int) -> Optional[TestSession]:
        """获取单个测试会话。"""
        with self._connect() as conn:
            r = conn.execute(
                "SELECT * FROM connect_test_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if r:
                return TestSession(
                    id=r["id"], collection_id=r["collection_id"],
                    collection_name=r["collection_name"] or "",
                    started_at=r["started_at"], completed_at=r["completed_at"] or "",
                    total_count=r["total_count"], success_count=r["success_count"],
                    fail_count=r["fail_count"]
                )
            return None

    # ── 测试结果操作 ───────────────────────────────────────

    def save_test_results_batch(self, rows: list[tuple]) -> None:
        """批量保存测试结果（单事务，避免锁竞争）。"""
        with self._connect() as conn:
            conn.executemany("""
                INSERT INTO connect_test_results
                    (session_id, target_id, ip, port, description, success, latency_ms, error_msg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)

    def get_test_results(self, session_id: int,
                         status_filter: Optional[str] = None) -> list[TestResult]:
        """获取指定会话的测试结果，可选筛选状态。"""
        with self._connect() as conn:
            if status_filter == "success":
                rows = conn.execute("""
                    SELECT * FROM connect_test_results
                    WHERE session_id = ? AND success = 1
                    ORDER BY tested_at
                """, (session_id,)).fetchall()
            elif status_filter == "fail":
                rows = conn.execute("""
                    SELECT * FROM connect_test_results
                    WHERE session_id = ? AND success = 0
                    ORDER BY tested_at
                """, (session_id,)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM connect_test_results
                    WHERE session_id = ?
                    ORDER BY tested_at
                """, (session_id,)).fetchall()
            return [TestResult(
                id=r["id"], session_id=r["session_id"], target_id=r["target_id"],
                ip=r["ip"], port=r["port"], description=r["description"],
                success=bool(r["success"]), latency_ms=r["latency_ms"],
                error_msg=r["error_msg"], tested_at=r["tested_at"]
            ) for r in rows]

    def delete_test_session(self, session_id: int) -> None:
        """删除测试会话及其结果（CASCADE）。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM connect_test_sessions WHERE id = ?", (session_id,))

    def get_last_test_time(self) -> Optional[str]:
        """获取最近一次测试的时间（连通 + 协议）。"""
        with self._connect() as conn:
            r = conn.execute("""
                SELECT MAX(started_at) AS last_time FROM (
                    SELECT started_at FROM connect_test_sessions
                    UNION ALL
                    SELECT started_at FROM protocol_test_sessions
                )
            """).fetchone()
            return r["last_time"] if r and r["last_time"] else None

    def get_total_target_count(self) -> int:
        """获取目标总数（连通 + 协议）。"""
        with self._connect() as conn:
            c1 = conn.execute("SELECT COUNT(*) FROM connect_targets").fetchone()[0]
            c2 = conn.execute("SELECT COUNT(*) FROM protocol_targets").fetchone()[0]
            return c1 + c2

    def cleanup_old_sessions(self, keep_count: int = 50) -> int:
        """清理旧测试会话，保留最近 keep_count 条。返回删除数。"""
        with self._connect() as conn:
            # 先查出第 keep_count 条的时间
            row = conn.execute(
                "SELECT id FROM connect_test_sessions ORDER BY started_at DESC LIMIT 1 OFFSET ?",
                (keep_count,)
            ).fetchone()
            if row:
                cur = conn.execute(
                    "DELETE FROM connect_test_sessions WHERE id < ?", (row["id"],)
                )
                return cur.rowcount
            return 0

    def get_test_statistics(self) -> list[dict]:
        """获取各目标的测试统计（最近一次结果）。"""
        with self._connect() as conn:
            return [dict(r) for r in conn.execute("""
                SELECT target_id, MAX(tested_at) AS last_test,
                       SUM(CASE WHEN success THEN 1 ELSE 0 END) AS ok_count,
                       COUNT(*) AS total
                FROM connect_test_results
                GROUP BY target_id
            """).fetchall()]

    # ── 排序操作 ───────────────────────────────────────────

    def update_collections_order(self, ordered_ids: list[int]) -> None:
        """按传入的 ID 顺序更新集合排序。"""
        with self._connect() as conn:
            for idx, collection_id in enumerate(ordered_ids):
                conn.execute(
                    "UPDATE connect_collections SET sort_order = ? WHERE id = ?",
                    (idx, collection_id)
                )

    def update_targets_order(self, ordered_ids: list[int]) -> None:
        """按传入的 ID 顺序更新目标排序。"""
        with self._connect() as conn:
            for idx, tid in enumerate(ordered_ids):
                conn.execute(
                    "UPDATE connect_targets SET sort_order = ? WHERE id = ?",
                    (idx, tid)
                )

    def get_target_last_result(self, target_id: int) -> Optional[TestResult]:
        """获取某个目标最近一次的测试结果。"""
        with self._connect() as conn:
            r = conn.execute("""
                SELECT * FROM connect_test_results
                WHERE target_id = ?
                ORDER BY tested_at DESC LIMIT 1
            """, (target_id,)).fetchone()
            if r:
                return TestResult(
                    id=r["id"], session_id=r["session_id"], target_id=r["target_id"],
                    ip=r["ip"], port=r["port"], description=r["description"],
                    success=bool(r["success"]), latency_ms=r["latency_ms"],
                    error_msg=r["error_msg"], tested_at=r["tested_at"]
                )
            return None

    def get_targets_last_results(self, target_ids: list[int]) -> dict[int, bool]:
        """批量获取多个目标的最新测试结果。返回 {target_id: success_bool, ...}"""
        if not target_ids:
            return {}
        placeholders = ",".join("?" * len(target_ids))
        with self._connect() as conn:
            rows = conn.execute(f"""
                SELECT target_id, success FROM connect_test_results
                WHERE target_id IN ({placeholders})
                ORDER BY tested_at DESC
            """, target_ids).fetchall()
        result = {}
        for r in rows:
            tid = r["target_id"]
            if tid not in result:
                result[tid] = bool(r["success"])
        return result

    # ── 协议测试集合操作 ─────────────────────────────────────

    def get_all_protocol_collections(self,
                                     protocol_type: str | None = None
                                     ) -> list[ProtocolCollection]:
        """获取协议测试集合列表，可按类型筛选。"""
        with self._connect() as conn:
            if protocol_type:
                rows = conn.execute("""
                    SELECT * FROM protocol_collections
                    WHERE protocol_type = ?
                    ORDER BY sort_order, created_at DESC
                """, (protocol_type,)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM protocol_collections
                    ORDER BY sort_order, created_at DESC
                """).fetchall()
            return [ProtocolCollection(
                id=r["id"], name=r["name"],
                protocol_type=r["protocol_type"], created_at=r["created_at"]
            ) for r in rows]

    def get_protocol_collection(self, collection_id: int
                                ) -> Optional[ProtocolCollection]:
        """获取单个协议测试集合。"""
        with self._connect() as conn:
            r = conn.execute(
                "SELECT * FROM protocol_collections WHERE id = ?",
                (collection_id,)
            ).fetchone()
            if r:
                return ProtocolCollection(
                    id=r["id"], name=r["name"],
                    protocol_type=r["protocol_type"], created_at=r["created_at"]
                )
            return None

    def add_protocol_collection(self, name: str, protocol_type: str) -> int:
        """添加协议测试集合，返回新 ID。"""
        with self._connect() as conn:
            max_order = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) FROM protocol_collections"
            ).fetchone()[0]
            cur = conn.execute("""
                INSERT INTO protocol_collections (name, protocol_type, sort_order)
                VALUES (?, ?, ?)
            """, (name, protocol_type, max_order + 1))
            return cur.lastrowid

    def update_protocol_collection(self, collection_id: int, name: str,
                                   protocol_type: str) -> None:
        """更新协议测试集合。"""
        with self._connect() as conn:
            conn.execute("""
                UPDATE protocol_collections SET
                    name = ?, protocol_type = ?
                WHERE id = ?
            """, (name, protocol_type, collection_id))

    def update_protocol_collections_order(self, ordered_ids: list[int]) -> None:
        """按传入的 ID 顺序更新协议测试集合排序。"""
        with self._connect() as conn:
            for idx, collection_id in enumerate(ordered_ids):
                conn.execute(
                    "UPDATE protocol_collections SET sort_order = ? WHERE id = ?",
                    (idx, collection_id)
                )

    def move_protocol_targets_to_collection(self, from_collection_id: int,
                                             to_collection_id: int) -> int:
        """将集合内所有目标移动到另一个集合，返回移动数量。"""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE protocol_targets SET collection_id = ? WHERE collection_id = ?",
                (to_collection_id, from_collection_id)
            )
            return cur.rowcount

    def move_protocol_target_ids_to_collection(self, target_ids: list[int],
                                               collection_id: int) -> None:
        """将指定协议目标移动到集合（用于拖拽归集）。"""
        with self._connect() as conn:
            conn.executemany(
                "UPDATE protocol_targets SET collection_id = ? WHERE id = ?",
                [(collection_id, tid) for tid in target_ids]
            )

    def delete_protocol_collection(self, collection_id: int) -> None:
        """删除协议测试集合。先移动目标到未分类，再删除集合本身。"""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM protocol_collections WHERE id = ?",
                (collection_id,)
            )

    # ── 协议消息操作 ─────────────────────────────────────────

    def get_protocol_messages(self, collection_id: int
                              ) -> list[ProtocolMessage]:
        """获取指定集合的所有消息（按 sort_order 排序）。"""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM protocol_messages
                WHERE collection_id = ?
                ORDER BY sort_order, created_at
            """, (collection_id,)).fetchall()
            return [ProtocolMessage(
                id=r["id"], collection_id=r["collection_id"],
                direction=r["direction"], message=r["message"],
                sort_order=r["sort_order"], created_at=r["created_at"]
            ) for r in rows]

    def add_protocol_message(self, collection_id: int, direction: str,
                             message: str, sort_order: int = 0) -> int:
        """添加一条协议消息，返回新 ID。"""
        with self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO protocol_messages
                    (collection_id, direction, message, sort_order)
                VALUES (?, ?, ?, ?)
            """, (collection_id, direction, message, sort_order))
            return cur.lastrowid

    def save_protocol_messages_batch(self,
                                     rows: list[tuple]) -> int:
        """批量保存协议消息（事务写入）。

        rows 中每条为 (collection_id, direction, message, sort_order)
        """
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany("""
                INSERT INTO protocol_messages
                    (collection_id, direction, message, sort_order)
                VALUES (?, ?, ?, ?)
            """, rows)
            return len(rows)

    def delete_protocol_messages(self, collection_id: int) -> None:
        """删除指定集合的所有消息。"""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM protocol_messages WHERE collection_id = ?",
                (collection_id,)
            )

    # ── 协议服务端操作 ───────────────────────────────────────

    def get_all_protocol_servers(self,
                                 server_type: str | None = None,
                                 target_id: int | None = None
                                 ) -> list[ProtocolServer]:
        """获取协议服务端监听器列表，可按类型和目标筛选。"""
        with self._connect() as conn:
            conditions = []
            params: list = []
            if server_type:
                conditions.append("server_type = ?")
                params.append(server_type)
            if target_id is not None:
                conditions.append("target_id = ?")
                params.append(target_id)
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            rows = conn.execute(f"""
                SELECT * FROM protocol_servers
                {where}
                ORDER BY sort_order, created_at DESC
            """, params).fetchall()
            return [ProtocolServer(
                id=r["id"], name=r["name"], server_type=r["server_type"],
                ip=r["ip"], port=r["port"], encoding=r["encoding"],
                recv_encoding=r["recv_encoding"],
                head_length=r["head_length"], ws_path=r["ws_path"],
                response_mode=r["response_mode"],
                response_message=r["response_message"],
                response_delay=r["response_delay"] if "response_delay" in r.keys() else 0,
                target_id=r["target_id"],
                sort_order=r["sort_order"], created_at=r["created_at"]
            ) for r in rows]

    def get_protocol_server(self, server_id: int
                            ) -> Optional[ProtocolServer]:
        """获取单个协议服务端配置。"""
        with self._connect() as conn:
            r = conn.execute(
                "SELECT * FROM protocol_servers WHERE id = ?",
                (server_id,)
            ).fetchone()
            if r:
                return ProtocolServer(
                    id=r["id"], name=r["name"],
                    server_type=r["server_type"],
                    ip=r["ip"], port=r["port"], encoding=r["encoding"],
                    recv_encoding=r["recv_encoding"],
                    head_length=r["head_length"], ws_path=r["ws_path"],
                    response_mode=r["response_mode"],
                    response_message=r["response_message"],
                    response_delay=r["response_delay"] if "response_delay" in r.keys() else 0,
                    target_id=r["target_id"],
                    sort_order=r["sort_order"], created_at=r["created_at"]
                )
            return None

    def add_protocol_server(self, name: str, server_type: str,
                            ip: str = "0.0.0.0", port: int = 0,
                            encoding: str = "UTF-8",
                            recv_encoding: str = "UTF-8",
                            head_length: int = 0,
                            ws_path: str = "",
                            response_mode: str = "fixed",
                            response_message: str = "",
                            response_delay: int = 0,
                            target_id: int | None = None) -> int:
        """添加协议服务端配置，返回新 ID。"""
        with self._connect() as conn:
            if self._servers_have_delay:
                cur = conn.execute("""
                    INSERT INTO protocol_servers
                        (name, server_type, ip, port, encoding, recv_encoding,
                         head_length, ws_path, response_mode, response_message,
                         response_delay, target_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (name, server_type, ip, port, encoding, recv_encoding,
                      head_length, ws_path, response_mode, response_message,
                      response_delay, target_id))
            else:
                # 老库无 response_delay 列，插入时省略（默认 0）
                cur = conn.execute("""
                    INSERT INTO protocol_servers
                        (name, server_type, ip, port, encoding, recv_encoding,
                         head_length, ws_path, response_mode, response_message,
                         target_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (name, server_type, ip, port, encoding, recv_encoding,
                      head_length, ws_path, response_mode, response_message,
                      target_id))
            return cur.lastrowid

    def update_protocol_server(self, server_id: int, name: str,
                               server_type: str, ip: str = "0.0.0.0",
                               port: int = 0, encoding: str = "UTF-8",
                               recv_encoding: str = "UTF-8",
                               head_length: int = 0,
                               ws_path: str = "",
                               response_mode: str = "fixed",
                               response_message: str = "",
                               response_delay: int = 0,
                               target_id: int | None = None) -> None:
        """更新协议服务端配置。"""
        with self._connect() as conn:
            if self._servers_have_delay:
                conn.execute("""
                    UPDATE protocol_servers SET
                        name = ?, server_type = ?, ip = ?, port = ?,
                        encoding = ?, recv_encoding = ?, head_length = ?,
                        ws_path = ?, response_mode = ?, response_message = ?,
                        response_delay = ?, target_id = ?
                    WHERE id = ?
                """, (name, server_type, ip, port, encoding, recv_encoding,
                      head_length, ws_path, response_mode, response_message,
                      response_delay, target_id, server_id))
            else:
                conn.execute("""
                    UPDATE protocol_servers SET
                        name = ?, server_type = ?, ip = ?, port = ?,
                        encoding = ?, recv_encoding = ?, head_length = ?,
                        ws_path = ?, response_mode = ?, response_message = ?,
                        target_id = ?
                    WHERE id = ?
                """, (name, server_type, ip, port, encoding, recv_encoding,
                      head_length, ws_path, response_mode, response_message,
                      target_id, server_id))

    def delete_protocol_server(self, server_id: int) -> None:
        """删除协议服务端配置。"""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM protocol_servers WHERE id = ?",
                (server_id,)
            )

    def get_protocol_servers_by_target(self, target_id: int
                                       ) -> list[ProtocolServer]:
        """获取关联到指定目标的所有服务端配置。"""
        return self.get_all_protocol_servers(target_id=target_id)

    # ── 协议目标操作 ──────────────────────────────────────

    def get_protocol_targets(self, collection_id: int) -> list[ProtocolTarget]:
        """获取协议集合内的目标列表。"""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM protocol_targets
                WHERE collection_id = ?
                ORDER BY sort_order, created_at
            """, (collection_id,)).fetchall()
            return [_protocol_target_from_row(r) for r in rows]

    def add_protocol_target(self, collection_id: int,
                            name: str = "",
                            send_presets: str = "{}",
                            stress_params: str = "{}",
                            **_kwargs) -> int:
        """添加协议目标，返回新 ID。

        旧字段 (ip, port, encoding, ...) 通过 **_kwargs 兼容忽略。
        调用者应在创建目标后立即通过预设保存完整配置。
        """
        with self._connect() as conn:
            # 检测旧列是否存在（兼容老库 NOT NULL 约束）
            cols = [r["name"] for r in conn.execute(
                "PRAGMA table_info(protocol_targets)").fetchall()]
            has_old = "ip" in cols
            if has_old:
                cur = conn.execute("""
                    INSERT INTO protocol_targets
                        (collection_id, name, ip, port,
                         encoding, recv_encoding, head_length, timeout,
                         ws_path, ws_use_ssl, send_message,
                         send_presets, stress_params, url, http_config)
                    VALUES (?, ?, '', 0,
                            'UTF-8', 'UTF-8', 0, 30.0,
                            '', 0, '',
                            ?, ?, '', '{}')
                """, (collection_id, name, send_presets, stress_params))
            else:
                cur = conn.execute("""
                    INSERT INTO protocol_targets
                        (collection_id, name, send_presets, stress_params)
                    VALUES (?, ?, ?, ?)
                """, (collection_id, name, send_presets, stress_params))
            return cur.lastrowid

    def add_protocol_targets_batch(self, targets: list[tuple]) -> int:
        """批量添加协议目标。targets 为 [(collection_id, name), ...]"""
        with self._connect() as conn:
            count = 0
            for cid, name in targets:
                conn.execute(
                    "INSERT INTO protocol_targets (collection_id, name) VALUES (?, ?)",
                    (cid, name)
                )
                count += 1
            return count

    def delete_protocol_target(self, target_id: int) -> None:
        """删除单个协议目标。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM protocol_targets WHERE id = ?", (target_id,))

    def delete_protocol_targets_for(self, collection_id: int) -> None:
        """删除集合下所有协议目标。"""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM protocol_targets WHERE collection_id = ?",
                (collection_id,)
            )

    def get_protocol_target(self, target_id: int) -> Optional[ProtocolTarget]:
        """获取单个协议目标。"""
        with self._connect() as conn:
            r = conn.execute(
                "SELECT * FROM protocol_targets WHERE id = ?", (target_id,)
            ).fetchone()
            if r:
                return _protocol_target_from_row(r)
            return None

    def update_protocol_target(self, target_id: int, *,
                               name: str | None = None,
                               send_presets: str | None = None,
                               stress_params: str | None = None,
                               **kwargs) -> None:
        """更新协议目标（配置已移入预设，仅更新名称/预设/压测参数）。

        旧字段 (ip, port, encoding, ...) 通过 **kwargs 兼容，直接忽略。
        """
        with self._connect() as conn:
            fields = []
            values = []
            if name is not None:
                fields.append("name")
                values.append(name)
            if send_presets is not None:
                fields.append("send_presets")
                values.append(send_presets)
            if stress_params is not None:
                fields.append("stress_params")
                values.append(stress_params)
            if fields:
                values.append(target_id)
                conn.execute(
                    f"UPDATE protocol_targets SET "
                    f"{', '.join(f'{f} = ?' for f in fields)} "
                    f"WHERE id = ?",
                    values)

    # ── 协议测试会话操作 ────────────────────────────────────

    def add_protocol_test_session(self, collection_id: int | None,
                                  collection_name: str,
                                  target_id: int | None,
                                  protocol_type: str,
                                  target_ip: str, target_port: int,
                                  success: bool, request: str = "",
                                  response: str = "",
                                  error_msg: str = "") -> int:
        """记录一次协议测试，返回会话 ID。"""
        with self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO protocol_test_sessions
                    (collection_id, collection_name, target_id, protocol_type,
                     target_ip, target_port, success, request, response, error_msg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (collection_id, collection_name, target_id, protocol_type,
                  target_ip, target_port, 1 if success else 0, request, response, error_msg))
            return cur.lastrowid

    def get_protocol_test_sessions(self, protocol_type: str | None = None,
                                   limit: int = 100) -> list[ProtocolTestSession]:
        """获取最近的协议测试会话。protocol_type=None 获取全部。"""
        with self._connect() as conn:
            if protocol_type:
                rows = conn.execute("""
                    SELECT * FROM protocol_test_sessions
                    WHERE protocol_type = ?
                    ORDER BY started_at DESC LIMIT ?
                """, (protocol_type, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM protocol_test_sessions
                    ORDER BY started_at DESC LIMIT ?
                """, (limit,)).fetchall()
            return [ProtocolTestSession(
                id=r["id"], collection_id=r["collection_id"],
                collection_name=r["collection_name"],
                target_id=r["target_id"], protocol_type=r["protocol_type"],
                target_ip=r["target_ip"], target_port=r["target_port"],
                started_at=r["started_at"],
                success=bool(r["success"]),
                request=r["request"], response=r["response"],
                error_msg=r["error_msg"]
            ) for r in rows]

    def get_protocol_test_sessions_by_target(self, target_id: int,
                                             limit: int = 50
                                             ) -> list[ProtocolTestSession]:
        """获取指定目标的协议测试会话。"""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM protocol_test_sessions
                WHERE target_id = ?
                ORDER BY started_at DESC LIMIT ?
            """, (target_id, limit)).fetchall()
            return [ProtocolTestSession(
                id=r["id"], collection_id=r["collection_id"],
                collection_name=r["collection_name"],
                target_id=r["target_id"], protocol_type=r["protocol_type"],
                target_ip=r["target_ip"], target_port=r["target_port"],
                started_at=r["started_at"],
                success=bool(r["success"]),
                request=r["request"], response=r["response"],
                error_msg=r["error_msg"]
            ) for r in rows]

    def delete_protocol_test_session(self, session_id: int) -> None:
        """删除协议测试会话记录。"""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM protocol_test_sessions WHERE id = ?",
                (session_id,)
            )

    def delete_protocol_test_sessions(self, session_ids: list[int]) -> None:
        """批量删除协议测试会话记录。"""
        if not session_ids:
            return
        with self._connect() as conn:
            conn.executemany(
                "DELETE FROM protocol_test_sessions WHERE id = ?",
                [(sid,) for sid in session_ids]
            )

    def clear_protocol_test_sessions(self) -> None:
        """清空全部协议测试会话记录。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM protocol_test_sessions")

    def update_protocol_servers_sort_order(self,
                                           ordered_ids: list[int]) -> None:
        """按传入的 ID 顺序更新服务端排序。"""
        with self._connect() as conn:
            for idx, server_id in enumerate(ordered_ids):
                conn.execute(
                    "UPDATE protocol_servers SET sort_order = ? WHERE id = ?",
                    (idx, server_id)
                )

    # ── 应用设置 ────────────────────────────────────────────

    def get_setting(self, key: str, default: str = "") -> str:
        """读取应用设置。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
            return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        """写入应用设置。"""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                (key, value)
            )
