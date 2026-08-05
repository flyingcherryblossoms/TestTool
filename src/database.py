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
class Batch:
    id: int
    name: str
    description: str = ""
    created_at: str = ""
    target_count: int = 0  # 非数据库字段，查询时动态计算


@dataclass
class Target:
    id: int
    ip: str
    port: int
    description: str = ""
    batch_id: Optional[int] = None
    batch_name: str = ""  # 非数据库字段，JOIN 时填充
    created_at: str = ""


@dataclass
class TestSession:
    id: int
    batch_id: Optional[int] = None
    batch_name: str = ""
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
    description: str = ""
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
    head_length: int = 0
    ws_path: str = ""
    response_mode: str = "fixed"      # "fixed" | "echo"
    response_message: str = ""
    target_id: int | None = None      # 关联的协议目标
    sort_order: int = 0
    created_at: str = ""


@dataclass
class ProtocolTarget:
    """协议测试集合内的目标 IP:Port，含独立客户端参数。"""
    id: int
    collection_id: int
    ip: str = ""
    port: int = 0
    description: str = ""
    encoding: str = "UTF-8"
    head_length: int = 5              # 0=raw, >0=长度头位数
    timeout: float = 5.0
    ws_path: str = ""                 # WebSocket 路径
    ws_use_ssl: bool = False
    send_message: str = ""            # 发送消息模板
    send_presets: str = "[]"          # JSON 格式多预设报文 [{"name":"...","message":"..."}]
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
    response: str = ""
    error_msg: str = ""


# ── SQL 建表语句 ──────────────────────────────────────────

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS connect_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS connect_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER,
    ip TEXT NOT NULL,
    port INTEGER NOT NULL,
    description TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (batch_id) REFERENCES connect_batches(id) ON DELETE SET NULL
);

-- 兼容旧表: 添加排序列（新表已有则忽略）

CREATE TABLE IF NOT EXISTS connect_test_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER,
    batch_name TEXT DEFAULT '',
    started_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    completed_at TIMESTAMP,
    total_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    FOREIGN KEY (batch_id) REFERENCES connect_batches(id) ON DELETE SET NULL
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

CREATE INDEX IF NOT EXISTS idx_targets_batch ON connect_targets(batch_id);
CREATE INDEX IF NOT EXISTS idx_results_session ON connect_test_results(session_id);
CREATE INDEX IF NOT EXISTS idx_results_status ON connect_test_results(success);

CREATE TABLE IF NOT EXISTS protocol_collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    protocol_type TEXT NOT NULL DEFAULT 'tcp_client',
    description TEXT DEFAULT '',
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
    head_length INTEGER DEFAULT 0,
    ws_path TEXT DEFAULT '',
    response_mode TEXT DEFAULT 'fixed',
    response_message TEXT DEFAULT '',
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
    ip TEXT NOT NULL,
    port INTEGER NOT NULL,
    description TEXT DEFAULT '',
    encoding TEXT DEFAULT 'UTF-8',
    head_length INTEGER DEFAULT 5,
    timeout REAL DEFAULT 5.0,
    ws_path TEXT DEFAULT '',
    ws_use_ssl INTEGER DEFAULT 0,
    send_message TEXT DEFAULT '',
    send_presets TEXT DEFAULT '[]',
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
        """创建数据库和表结构（含旧表兼容迁移）。"""
        with self._connect() as conn:
            # ── 旧表重命名迁移 ──
            old_to_new = {
                "batches": "connect_batches",
                "targets": "connect_targets",
                "test_sessions": "connect_test_sessions",
                "test_results": "connect_test_results",
            }
            for old, new in old_to_new.items():
                try:
                    conn.execute(f"ALTER TABLE {old} RENAME TO {new}")
                except sqlite3.OperationalError:
                    pass  # 表不存在或已迁移

            conn.executescript(SCHEMA_SQL)
            # 兼容旧表: 如果列不存在则添加
            for table, col in [("connect_batches", "sort_order"), ("connect_targets", "sort_order")]:
                try:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {col} INTEGER DEFAULT 0"
                    )
                except sqlite3.OperationalError:
                    pass  # 列已存在

            # ── 协议测试 v2 迁移 ─────────────────────────
            self._migrate_protocol_v2(conn)

    def _migrate_protocol_v2(self, conn: sqlite3.Connection) -> None:
        """协议测试 v2 迁移：目标扩展 + 服务端关联 + 集合精简。"""
        # 1. 为目标表添加新列（已存在则跳过）
        for col, col_type in [
            ("encoding", "TEXT DEFAULT 'UTF-8'"),
            ("head_length", "INTEGER DEFAULT 5"),
            ("timeout", "REAL DEFAULT 5.0"),
            ("ws_path", "TEXT DEFAULT ''"),
            ("ws_use_ssl", "INTEGER DEFAULT 0"),
            ("send_message", "TEXT DEFAULT ''"),
            ("send_presets", "TEXT DEFAULT '[]'"),
        ]:
            try:
                conn.execute(
                    f"ALTER TABLE protocol_targets ADD COLUMN {col} {col_type}"
                )
            except sqlite3.OperationalError:
                pass

        # 2. 为服务端表添加 target_id 列
        try:
            conn.execute(
                "ALTER TABLE protocol_servers ADD COLUMN target_id INTEGER "
                "REFERENCES protocol_targets(id) ON DELETE SET NULL"
            )
        except sqlite3.OperationalError:
            pass

        # 3. 检测是否需要数据迁移（集合表是否还有 target_ip 列）
        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(protocol_collections)"
        ).fetchall()]
        if "target_ip" not in cols:
            return  # 已迁移过

        # 4. 将集合的客户端参数复制到其下的每个目标
        collections = conn.execute(
            "SELECT * FROM protocol_collections"
        ).fetchall()
        for coll in collections:
            cid = coll["id"]
            targets = conn.execute(
                "SELECT id FROM protocol_targets WHERE collection_id = ?",
                (cid,)
            ).fetchall()
            if not targets:
                continue
            for t in targets:
                conn.execute("""
                    UPDATE protocol_targets SET
                        encoding = ?, head_length = ?, timeout = ?,
                        ws_path = ?, ws_use_ssl = ?
                    WHERE id = ?
                """, (coll["encoding"], coll["head_length"], coll["timeout"],
                      coll["ws_path"], coll["ws_use_ssl"], t["id"]))
            # 复制第一条 send 消息到每个目标的 send_message
            send_msg = conn.execute(
                "SELECT message FROM protocol_messages "
                "WHERE collection_id = ? AND direction = 'send' "
                "ORDER BY sort_order LIMIT 1",
                (cid,)
            ).fetchone()
            if send_msg and send_msg["message"]:
                for t in targets:
                    conn.execute(
                        "UPDATE protocol_targets SET send_message = ? WHERE id = ?",
                        (send_msg["message"], t["id"])
                    )

        # 5. 用重建方式移除集合表的客户端参数字段
        conn.execute("""
            CREATE TABLE IF NOT EXISTS protocol_collections_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                protocol_type TEXT NOT NULL DEFAULT 'tcp_client',
                description TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
            )
        """)
        conn.execute("""
            INSERT INTO protocol_collections_v2
                (id, name, protocol_type, description, created_at)
            SELECT id, name, protocol_type, description, created_at
            FROM protocol_collections
        """)
        conn.execute("DROP TABLE protocol_collections")
        conn.execute(
            "ALTER TABLE protocol_collections_v2 RENAME TO protocol_collections"
        )

    def _connect(self) -> sqlite3.Connection:
        """获取数据库连接（启用 WAL 和外键）。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ── 集合操作 ───────────────────────────────────────────

    def get_all_batches(self) -> list[Batch]:
        """获取所有集合，含目标计数。"""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT b.*, COUNT(t.id) AS target_count
                FROM connect_batches b
                LEFT JOIN connect_targets t ON t.batch_id = b.id
                GROUP BY b.id
                ORDER BY b.sort_order, b.created_at DESC
            """).fetchall()
            return [Batch(
                id=r["id"], name=r["name"], description=r["description"],
                created_at=r["created_at"], target_count=r["target_count"]
            ) for r in rows]

    def get_batch(self, batch_id: int) -> Optional[Batch]:
        """获取单个集合。"""
        with self._connect() as conn:
            r = conn.execute(
                "SELECT b.*, COUNT(t.id) AS target_count "
                "FROM connect_batches b LEFT JOIN connect_targets t ON t.batch_id = b.id "
                "WHERE b.id = ? GROUP BY b.id", (batch_id,)
            ).fetchone()
            if r:
                return Batch(
                    id=r["id"], name=r["name"], description=r["description"],
                    created_at=r["created_at"], target_count=r["target_count"]
                )
            return None

    def add_batch(self, name: str, description: str = "") -> int:
        """添加集合，返回新 ID。"""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO connect_batches (name, description) VALUES (?, ?)",
                (name, description)
            )
            return cur.lastrowid

    def update_batch(self, batch_id: int, name: str, description: str = "") -> None:
        """更新集合信息。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE connect_batches SET name = ?, description = ? WHERE id = ?",
                (name, description, batch_id)
            )

    def delete_batch(self, batch_id: int) -> None:
        """删除集合（关联目标会因 ON DELETE SET NULL 脱离集合）。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM connect_batches WHERE id = ?", (batch_id,))

    # ── 目标操作 ───────────────────────────────────────────

    def get_targets(self, batch_id: Optional[int] = None) -> list[Target]:
        """获取目标列表。`batch_id=None` 获取全部，`batch_id=0` 获取未分类。"""
        with self._connect() as conn:
            if batch_id is None:
                rows = conn.execute("""
                    SELECT t.*, b.name AS batch_name
                    FROM connect_targets t
                    LEFT JOIN connect_batches b ON t.batch_id = b.id
                    ORDER BY t.sort_order, t.created_at DESC
                """).fetchall()
            elif batch_id == 0:
                rows = conn.execute("""
                    SELECT t.*, b.name AS batch_name
                    FROM connect_targets t
                    LEFT JOIN connect_batches b ON t.batch_id = b.id
                    WHERE t.batch_id IS NULL
                    ORDER BY t.sort_order, t.created_at DESC
                """).fetchall()
            else:
                rows = conn.execute("""
                    SELECT t.*, b.name AS batch_name
                    FROM connect_targets t
                    LEFT JOIN connect_batches b ON t.batch_id = b.id
                    WHERE t.batch_id = ?
                    ORDER BY t.sort_order, t.created_at DESC
                """, (batch_id,)).fetchall()
            return [Target(
                id=r["id"], ip=r["ip"], port=r["port"],
                description=r["description"], batch_id=r["batch_id"],
                batch_name=r["batch_name"] or "", created_at=r["created_at"]
            ) for r in rows]

    def get_target(self, target_id: int) -> Optional[Target]:
        """获取单个目标。"""
        with self._connect() as conn:
            r = conn.execute("""
                SELECT t.*, b.name AS batch_name
                FROM connect_targets t LEFT JOIN connect_batches b ON t.batch_id = b.id
                WHERE t.id = ?
            """, (target_id,)).fetchone()
            if r:
                return Target(
                    id=r["id"], ip=r["ip"], port=r["port"],
                    description=r["description"], batch_id=r["batch_id"],
                    batch_name=r["batch_name"] or "", created_at=r["created_at"]
                )
            return None

    def target_exists(self, ip: str, port: int, batch_id: Optional[int] = None) -> bool:
        """检查指定 (集合, IP, 端口) 组合是否已存在。"""
        with self._connect() as conn:
            if batch_id is not None:
                r = conn.execute(
                    "SELECT 1 FROM connect_targets WHERE batch_id = ? AND ip = ? AND port = ? LIMIT 1",
                    (batch_id, ip.strip(), port)
                ).fetchone()
            else:
                r = conn.execute(
                    "SELECT 1 FROM connect_targets WHERE batch_id IS NULL AND ip = ? AND port = ? LIMIT 1",
                    (ip.strip(), port)
                ).fetchone()
            return r is not None

    def find_target_id(self, ip: str, port: int, batch_id: Optional[int] = None) -> Optional[int]:
        """查找指定 (集合, IP, 端口) 的目标 ID，不存在返回 None。"""
        with self._connect() as conn:
            if batch_id is not None:
                r = conn.execute(
                    "SELECT id FROM connect_targets WHERE batch_id = ? AND ip = ? AND port = ? LIMIT 1",
                    (batch_id, ip.strip(), port)
                ).fetchone()
            else:
                r = conn.execute(
                    "SELECT id FROM connect_targets WHERE batch_id IS NULL AND ip = ? AND port = ? LIMIT 1",
                    (ip.strip(), port)
                ).fetchone()
            return r["id"] if r else None

    def add_target(self, ip: str, port: int, description: str = "",
                   batch_id: Optional[int] = None) -> int:
        """添加目标，返回新 ID。"""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO connect_targets (ip, port, description, batch_id) VALUES (?, ?, ?, ?)",
                (ip.strip(), port, description, batch_id)
            )
            return cur.lastrowid

    def add_targets_batch(self, targets: list[tuple[str, int, str, Optional[int]]]) -> int:
        """批量添加目标，返回成功数量。`targets` 为 [(ip, port, desc, batch_id), ...]"""
        with self._connect() as conn:
            count = 0
            for ip, port, desc, batch_id in targets:
                conn.execute(
                    "INSERT INTO connect_targets (ip, port, description, batch_id) VALUES (?, ?, ?, ?)",
                    (ip.strip(), port, desc, batch_id)
                )
                count += 1
            return count

    def update_target(self, target_id: int, ip: str, port: int,
                      description: str = "", batch_id: Optional[int] = None) -> None:
        """更新目标信息。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE connect_targets SET ip = ?, port = ?, description = ?, batch_id = ? WHERE id = ?",
                (ip.strip(), port, description, batch_id, target_id)
            )

    def delete_target(self, target_id: int) -> None:
        """删除单个目标。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM connect_targets WHERE id = ?", (target_id,))

    def delete_targets(self, target_ids: list[int]) -> None:
        """批量删除目标。"""
        with self._connect() as conn:
            conn.executemany("DELETE FROM connect_targets WHERE id = ?", [(tid,) for tid in target_ids])

    def move_targets_to_batch(self, target_ids: list[int], batch_id: Optional[int]) -> None:
        """将目标移动/归类到指定集合。batch_id 为 None 则取消分类。"""
        with self._connect() as conn:
            conn.executemany(
                "UPDATE connect_targets SET batch_id = ? WHERE id = ?",
                [(batch_id, tid) for tid in target_ids]
            )

    # ── 测试会话操作 ───────────────────────────────────────

    def create_test_session(self, batch_id: Optional[int] = None,
                            batch_name: str = "") -> int:
        """创建测试会话，返回会话 ID。"""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO connect_test_sessions (batch_id, batch_name) VALUES (?, ?)",
                (batch_id, batch_name)
            )
            return cur.lastrowid

    def complete_test_session(self, session_id: int, total: int,
                               success: int, fail: int) -> None:
        """标记会话完成并写入统计数据。"""
        with self._connect() as conn:
            conn.execute("""
                UPDATE connect_test_sessions
                SET completed_at = datetime('now', 'localtime'),
                    total_count = ?, success_count = ?, fail_count = ?
                WHERE id = ?
            """, (total, success, fail, session_id))

    def get_test_sessions(self, limit: int = 100) -> list[TestSession]:
        """获取最近的测试会话列表。"""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM connect_test_sessions
                ORDER BY started_at DESC LIMIT ?
            """, (limit,)).fetchall()
            return [TestSession(
                id=r["id"], batch_id=r["batch_id"], batch_name=r["batch_name"],
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
                    id=r["id"], batch_id=r["batch_id"], batch_name=r["batch_name"],
                    started_at=r["started_at"], completed_at=r["completed_at"] or "",
                    total_count=r["total_count"], success_count=r["success_count"],
                    fail_count=r["fail_count"]
                )
            return None

    # ── 测试结果操作 ───────────────────────────────────────

    def save_test_result(self, session_id: int, target_id: int,
                          ip: str, port: int, description: str,
                          success: bool, latency_ms: float,
                          error_msg: str = "") -> int:
        """保存单条测试结果，返回结果 ID。"""
        with self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO connect_test_results
                    (session_id, target_id, ip, port, description, success, latency_ms, error_msg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (session_id, target_id, ip, port, description,
                  1 if success else 0, latency_ms, error_msg))
            return cur.lastrowid

    def save_test_results_batch(self, rows: list[tuple]) -> int:
        """批量保存测试结果（使用事务，避免逐条写入的开销和锁竞争）。

        rows 中每条为 (session_id, target_id, ip, port, description, success, latency_ms, error_msg)
        """
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany("""
                INSERT INTO connect_test_results
                    (session_id, target_id, ip, port, description, success, latency_ms, error_msg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            return len(rows)

    def get_test_results(self, session_id: int,
                         status_filter: Optional[str] = None) -> list[TestResult]:
        """获取某次会话的测试结果。`status_filter` 为 'success'/'fail'/None(全部)。"""
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
        """删除测试会话及其结果。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM connect_test_sessions WHERE id = ?", (session_id,))

    def delete_old_sessions(self, keep_count: int = 100) -> int:
        """删除旧测试会话，仅保留最近 N 条。返回删除数量。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM connect_test_sessions ORDER BY started_at DESC LIMIT 1 OFFSET ?",
                (keep_count - 1,)
            ).fetchone()
            if row:
                cur = conn.execute(
                    "DELETE FROM connect_test_sessions WHERE id < ?", (row["id"],)
                )
                return cur.rowcount
            return 0

    # ── 排序操作 ───────────────────────────────────────────

    def update_batches_sort_order(self, ordered_ids: list[int]) -> None:
        """按传入的 ID 顺序更新集合排序。"""
        with self._connect() as conn:
            for idx, batch_id in enumerate(ordered_ids):
                conn.execute(
                    "UPDATE connect_batches SET sort_order = ? WHERE id = ?",
                    (idx, batch_id)
                )

    def update_targets_sort_order(self, ordered_ids: list[int]) -> None:
        """按传入的 ID 顺序更新目标排序。"""
        with self._connect() as conn:
            for idx, target_id in enumerate(ordered_ids):
                conn.execute(
                    "UPDATE connect_targets SET sort_order = ? WHERE id = ?",
                    (idx, target_id)
                )

    # ── 统计查询 ───────────────────────────────────────────

    def get_total_target_count(self) -> int:
        """获取目标总数。"""
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM connect_targets").fetchone()[0]

    def get_last_test_time(self) -> str:
        """获取最近一次测试的时间。"""
        with self._connect() as conn:
            r = conn.execute(
                "SELECT started_at FROM connect_test_sessions ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            return r["started_at"] if r else ""

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
        """批量获取多个目标的最新测试结果（一次查询）。

        Returns:
            {target_id: success_bool, ...}  — 仅包含有测试记录的目标
        """
        if not target_ids:
            return {}
        placeholders = ",".join("?" * len(target_ids))
        with self._connect() as conn:
            rows = conn.execute(f"""
                SELECT target_id, success FROM connect_test_results
                WHERE target_id IN ({placeholders})
                ORDER BY tested_at DESC
            """, target_ids).fetchall()
        # 按 tested_at DESC 排序，第一条遇到的就是最新的
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
                    ORDER BY created_at DESC
                """, (protocol_type,)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM protocol_collections
                    ORDER BY created_at DESC
                """).fetchall()
            return [ProtocolCollection(
                id=r["id"], name=r["name"],
                protocol_type=r["protocol_type"],
                description=r["description"], created_at=r["created_at"]
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
                    protocol_type=r["protocol_type"],
                    description=r["description"], created_at=r["created_at"]
                )
            return None

    def add_protocol_collection(self, name: str, protocol_type: str,
                                description: str = "") -> int:
        """添加协议测试集合，返回新 ID。"""
        with self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO protocol_collections (name, protocol_type, description)
                VALUES (?, ?, ?)
            """, (name, protocol_type, description))
            return cur.lastrowid

    def update_protocol_collection(self, collection_id: int, name: str,
                                   protocol_type: str,
                                   description: str = "") -> None:
        """更新协议测试集合。"""
        with self._connect() as conn:
            conn.execute("""
                UPDATE protocol_collections SET
                    name = ?, protocol_type = ?, description = ?
                WHERE id = ?
            """, (name, protocol_type, description, collection_id))

    def delete_protocol_collection(self, collection_id: int) -> None:
        """删除协议测试集合（关联消息会因 ON DELETE CASCADE 自动删除）。"""
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
                head_length=r["head_length"], ws_path=r["ws_path"],
                response_mode=r["response_mode"],
                response_message=r["response_message"],
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
                    head_length=r["head_length"], ws_path=r["ws_path"],
                    response_mode=r["response_mode"],
                    response_message=r["response_message"],
                    target_id=r["target_id"],
                    sort_order=r["sort_order"], created_at=r["created_at"]
                )
            return None

    def add_protocol_server(self, name: str, server_type: str,
                            ip: str = "0.0.0.0", port: int = 0,
                            encoding: str = "UTF-8",
                            head_length: int = 0,
                            ws_path: str = "",
                            response_mode: str = "fixed",
                            response_message: str = "",
                            target_id: int | None = None) -> int:
        """添加协议服务端配置，返回新 ID。"""
        with self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO protocol_servers
                    (name, server_type, ip, port, encoding, head_length,
                     ws_path, response_mode, response_message, target_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (name, server_type, ip, port, encoding, head_length,
                  ws_path, response_mode, response_message, target_id))
            return cur.lastrowid

    def update_protocol_server(self, server_id: int, name: str,
                               server_type: str, ip: str = "0.0.0.0",
                               port: int = 0, encoding: str = "UTF-8",
                               head_length: int = 0,
                               ws_path: str = "",
                               response_mode: str = "fixed",
                               response_message: str = "",
                               target_id: int | None = None) -> None:
        """更新协议服务端配置。"""
        with self._connect() as conn:
            conn.execute("""
                UPDATE protocol_servers SET
                    name = ?, server_type = ?, ip = ?, port = ?,
                    encoding = ?, head_length = ?, ws_path = ?,
                    response_mode = ?, response_message = ?,
                    target_id = ?
                WHERE id = ?
            """, (name, server_type, ip, port, encoding, head_length,
                  ws_path, response_mode, response_message, target_id,
                  server_id))

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
            return [ProtocolTarget(
                id=r["id"], collection_id=r["collection_id"],
                ip=r["ip"], port=r["port"],
                description=r["description"],
                encoding=r["encoding"], head_length=r["head_length"],
                timeout=r["timeout"], ws_path=r["ws_path"],
                ws_use_ssl=bool(r["ws_use_ssl"]),
                send_message=r["send_message"],
                send_presets=r["send_presets"],
                sort_order=r["sort_order"], created_at=r["created_at"]
            ) for r in rows]

    def add_protocol_target(self, collection_id: int, ip: str, port: int,
                            description: str = "",
                            encoding: str = "UTF-8",
                            head_length: int = 5,
                            timeout: float = 5.0,
                            ws_path: str = "",
                            ws_use_ssl: bool = False,
                            send_message: str = "",
                            send_presets: str = "[]") -> int:
        """添加协议目标，返回新 ID。"""
        with self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO protocol_targets
                    (collection_id, ip, port, description, encoding,
                     head_length, timeout, ws_path, ws_use_ssl,
                     send_message, send_presets)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (collection_id, ip.strip(), port, description, encoding,
                  head_length, timeout, ws_path, 1 if ws_use_ssl else 0,
                  send_message, send_presets))
            return cur.lastrowid

    def add_protocol_targets_batch(self, targets: list[tuple]) -> int:
        """批量添加协议目标。targets 为 [(collection_id, ip, port, desc), ...]"""
        with self._connect() as conn:
            count = 0
            for cid, ip, port, desc in targets:
                conn.execute(
                    "INSERT INTO protocol_targets (collection_id, ip, port, description) VALUES (?, ?, ?, ?)",
                    (cid, ip.strip(), port, desc)
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
                return ProtocolTarget(
                    id=r["id"], collection_id=r["collection_id"],
                    ip=r["ip"], port=r["port"],
                    description=r["description"],
                    encoding=r["encoding"], head_length=r["head_length"],
                    timeout=r["timeout"], ws_path=r["ws_path"],
                    ws_use_ssl=bool(r["ws_use_ssl"]),
                    send_message=r["send_message"],
                send_presets=r["send_presets"],
                    sort_order=r["sort_order"], created_at=r["created_at"]
                )
            return None

    def update_protocol_target(self, target_id: int, ip: str, port: int,
                               description: str = "",
                               encoding: str = "UTF-8",
                               head_length: int = 5,
                               timeout: float = 5.0,
                               ws_path: str = "",
                               ws_use_ssl: bool = False,
                               send_message: str = "",
                               send_presets: str = "[]") -> None:
        """更新协议目标（含客户端参数）。"""
        with self._connect() as conn:
            conn.execute("""
                UPDATE protocol_targets SET
                    ip = ?, port = ?, description = ?,
                    encoding = ?, head_length = ?, timeout = ?,
                    ws_path = ?, ws_use_ssl = ?, send_message = ?,
                    send_presets = ?
                WHERE id = ?
            """, (ip.strip(), port, description, encoding,
                  head_length, timeout, ws_path, 1 if ws_use_ssl else 0,
                  send_message, send_presets, target_id))

    # ── 协议测试会话操作 ────────────────────────────────────

    def add_protocol_test_session(self, collection_id: int | None,
                                  collection_name: str,
                                  target_id: int | None,
                                  protocol_type: str,
                                  target_ip: str, target_port: int,
                                  success: bool, response: str,
                                  error_msg: str = "") -> int:
        """记录一次协议测试，返回会话 ID。"""
        with self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO protocol_test_sessions
                    (collection_id, collection_name, target_id, protocol_type,
                     target_ip, target_port, success, response, error_msg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (collection_id, collection_name, target_id, protocol_type,
                  target_ip, target_port, 1 if success else 0, response, error_msg))
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
                success=bool(r["success"]), response=r["response"],
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
                success=bool(r["success"]), response=r["response"],
                error_msg=r["error_msg"]
            ) for r in rows]

    def delete_protocol_test_session(self, session_id: int) -> None:
        """删除协议测试会话记录。"""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM protocol_test_sessions WHERE id = ?",
                (session_id,)
            )

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
