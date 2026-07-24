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


# ── SQL 建表语句 ──────────────────────────────────────────

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER,
    ip TEXT NOT NULL,
    port INTEGER NOT NULL,
    description TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE SET NULL
);

-- 兼容旧表: 添加排序列（新表已有则忽略）

CREATE TABLE IF NOT EXISTS test_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER,
    batch_name TEXT DEFAULT '',
    started_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    completed_at TIMESTAMP,
    total_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS test_results (
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
    FOREIGN KEY (session_id) REFERENCES test_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES targets(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_targets_batch ON targets(batch_id);
CREATE INDEX IF NOT EXISTS idx_results_session ON test_results(session_id);
CREATE INDEX IF NOT EXISTS idx_results_status ON test_results(success);
"""


# ── Database 类 ───────────────────────────────────────────


class Database:
    """SQLite 数据库操作封装。"""

    def __init__(self, db_path: str | Path = ""):
        if not db_path:
            db_path = Path(__file__).parent.parent / "portcheck.db"
        self.db_path = Path(db_path)
        self._init_db()

    # ── 初始化 ─────────────────────────────────────────────

    def _init_db(self) -> None:
        """创建数据库和表结构（含旧表兼容迁移）。"""
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            # 兼容旧表: 如果列不存在则添加
            for table, col in [("batches", "sort_order"), ("targets", "sort_order")]:
                try:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {col} INTEGER DEFAULT 0"
                    )
                except sqlite3.OperationalError:
                    pass  # 列已存在

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
                FROM batches b
                LEFT JOIN targets t ON t.batch_id = b.id
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
                "FROM batches b LEFT JOIN targets t ON t.batch_id = b.id "
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
                "INSERT INTO batches (name, description) VALUES (?, ?)",
                (name, description)
            )
            return cur.lastrowid

    def update_batch(self, batch_id: int, name: str, description: str = "") -> None:
        """更新集合信息。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE batches SET name = ?, description = ? WHERE id = ?",
                (name, description, batch_id)
            )

    def delete_batch(self, batch_id: int) -> None:
        """删除集合（关联目标会因 ON DELETE SET NULL 脱离集合）。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM batches WHERE id = ?", (batch_id,))

    # ── 目标操作 ───────────────────────────────────────────

    def get_targets(self, batch_id: Optional[int] = None) -> list[Target]:
        """获取目标列表。`batch_id=None` 获取全部，`batch_id=0` 获取未分类。"""
        with self._connect() as conn:
            if batch_id is None:
                rows = conn.execute("""
                    SELECT t.*, b.name AS batch_name
                    FROM targets t
                    LEFT JOIN batches b ON t.batch_id = b.id
                    ORDER BY t.sort_order, t.created_at DESC
                """).fetchall()
            elif batch_id == 0:
                rows = conn.execute("""
                    SELECT t.*, b.name AS batch_name
                    FROM targets t
                    LEFT JOIN batches b ON t.batch_id = b.id
                    WHERE t.batch_id IS NULL
                    ORDER BY t.sort_order, t.created_at DESC
                """).fetchall()
            else:
                rows = conn.execute("""
                    SELECT t.*, b.name AS batch_name
                    FROM targets t
                    LEFT JOIN batches b ON t.batch_id = b.id
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
                FROM targets t LEFT JOIN batches b ON t.batch_id = b.id
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
                    "SELECT 1 FROM targets WHERE batch_id = ? AND ip = ? AND port = ? LIMIT 1",
                    (batch_id, ip.strip(), port)
                ).fetchone()
            else:
                r = conn.execute(
                    "SELECT 1 FROM targets WHERE batch_id IS NULL AND ip = ? AND port = ? LIMIT 1",
                    (ip.strip(), port)
                ).fetchone()
            return r is not None

    def find_target_id(self, ip: str, port: int, batch_id: Optional[int] = None) -> Optional[int]:
        """查找指定 (集合, IP, 端口) 的目标 ID，不存在返回 None。"""
        with self._connect() as conn:
            if batch_id is not None:
                r = conn.execute(
                    "SELECT id FROM targets WHERE batch_id = ? AND ip = ? AND port = ? LIMIT 1",
                    (batch_id, ip.strip(), port)
                ).fetchone()
            else:
                r = conn.execute(
                    "SELECT id FROM targets WHERE batch_id IS NULL AND ip = ? AND port = ? LIMIT 1",
                    (ip.strip(), port)
                ).fetchone()
            return r["id"] if r else None

    def add_target(self, ip: str, port: int, description: str = "",
                   batch_id: Optional[int] = None) -> int:
        """添加目标，返回新 ID。"""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO targets (ip, port, description, batch_id) VALUES (?, ?, ?, ?)",
                (ip.strip(), port, description, batch_id)
            )
            return cur.lastrowid

    def add_targets_batch(self, targets: list[tuple[str, int, str, Optional[int]]]) -> int:
        """批量添加目标，返回成功数量。`targets` 为 [(ip, port, desc, batch_id), ...]"""
        with self._connect() as conn:
            count = 0
            for ip, port, desc, batch_id in targets:
                conn.execute(
                    "INSERT INTO targets (ip, port, description, batch_id) VALUES (?, ?, ?, ?)",
                    (ip.strip(), port, desc, batch_id)
                )
                count += 1
            return count

    def update_target(self, target_id: int, ip: str, port: int,
                      description: str = "", batch_id: Optional[int] = None) -> None:
        """更新目标信息。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE targets SET ip = ?, port = ?, description = ?, batch_id = ? WHERE id = ?",
                (ip.strip(), port, description, batch_id, target_id)
            )

    def delete_target(self, target_id: int) -> None:
        """删除单个目标。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM targets WHERE id = ?", (target_id,))

    def delete_targets(self, target_ids: list[int]) -> None:
        """批量删除目标。"""
        with self._connect() as conn:
            conn.executemany("DELETE FROM targets WHERE id = ?", [(tid,) for tid in target_ids])

    def move_targets_to_batch(self, target_ids: list[int], batch_id: Optional[int]) -> None:
        """将目标移动/归类到指定集合。batch_id 为 None 则取消分类。"""
        with self._connect() as conn:
            conn.executemany(
                "UPDATE targets SET batch_id = ? WHERE id = ?",
                [(batch_id, tid) for tid in target_ids]
            )

    # ── 测试会话操作 ───────────────────────────────────────

    def create_test_session(self, batch_id: Optional[int] = None,
                            batch_name: str = "") -> int:
        """创建测试会话，返回会话 ID。"""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO test_sessions (batch_id, batch_name) VALUES (?, ?)",
                (batch_id, batch_name)
            )
            return cur.lastrowid

    def complete_test_session(self, session_id: int, total: int,
                               success: int, fail: int) -> None:
        """标记会话完成并写入统计数据。"""
        with self._connect() as conn:
            conn.execute("""
                UPDATE test_sessions
                SET completed_at = datetime('now', 'localtime'),
                    total_count = ?, success_count = ?, fail_count = ?
                WHERE id = ?
            """, (total, success, fail, session_id))

    def get_test_sessions(self, limit: int = 100) -> list[TestSession]:
        """获取最近的测试会话列表。"""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM test_sessions
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
                "SELECT * FROM test_sessions WHERE id = ?", (session_id,)
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
                INSERT INTO test_results
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
                INSERT INTO test_results
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
                    SELECT * FROM test_results
                    WHERE session_id = ? AND success = 1
                    ORDER BY tested_at
                """, (session_id,)).fetchall()
            elif status_filter == "fail":
                rows = conn.execute("""
                    SELECT * FROM test_results
                    WHERE session_id = ? AND success = 0
                    ORDER BY tested_at
                """, (session_id,)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM test_results
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
            conn.execute("DELETE FROM test_sessions WHERE id = ?", (session_id,))

    def delete_old_sessions(self, keep_count: int = 100) -> int:
        """删除旧测试会话，仅保留最近 N 条。返回删除数量。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM test_sessions ORDER BY started_at DESC LIMIT 1 OFFSET ?",
                (keep_count - 1,)
            ).fetchone()
            if row:
                cur = conn.execute(
                    "DELETE FROM test_sessions WHERE id < ?", (row["id"],)
                )
                return cur.rowcount
            return 0

    # ── 排序操作 ───────────────────────────────────────────

    def update_batches_sort_order(self, ordered_ids: list[int]) -> None:
        """按传入的 ID 顺序更新集合排序。"""
        with self._connect() as conn:
            for idx, batch_id in enumerate(ordered_ids):
                conn.execute(
                    "UPDATE batches SET sort_order = ? WHERE id = ?",
                    (idx, batch_id)
                )

    def update_targets_sort_order(self, ordered_ids: list[int]) -> None:
        """按传入的 ID 顺序更新目标排序。"""
        with self._connect() as conn:
            for idx, target_id in enumerate(ordered_ids):
                conn.execute(
                    "UPDATE targets SET sort_order = ? WHERE id = ?",
                    (idx, target_id)
                )

    # ── 统计查询 ───────────────────────────────────────────

    def get_total_target_count(self) -> int:
        """获取目标总数。"""
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM targets").fetchone()[0]

    def get_last_test_time(self) -> str:
        """获取最近一次测试的时间。"""
        with self._connect() as conn:
            r = conn.execute(
                "SELECT started_at FROM test_sessions ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            return r["started_at"] if r else ""

    def get_target_last_result(self, target_id: int) -> Optional[TestResult]:
        """获取某个目标最近一次的测试结果。"""
        with self._connect() as conn:
            r = conn.execute("""
                SELECT * FROM test_results
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
                SELECT target_id, success FROM test_results
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
