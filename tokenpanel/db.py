"""SQLite 存储层。

两张核心表，刻意分开：
- usage_event    逐次调用的 token 事件（可多次累加）
- credit_snapshot 会话级累计快照（WorkBuddy 的 credit/上下文占用，只能覆盖不能累加）

混在一起会导致重复计数，这是本设计最重要的约束。
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_event (
    client        TEXT    NOT NULL,
    session_id    TEXT    NOT NULL,
    event_key     TEXT    NOT NULL,
    ts            INTEGER NOT NULL,
    project       TEXT,
    model         TEXT,
    input         INTEGER NOT NULL DEFAULT 0,
    output        INTEGER NOT NULL DEFAULT 0,
    cache_read    INTEGER NOT NULL DEFAULT 0,
    cache_write   INTEGER NOT NULL DEFAULT 0,
    reasoning     INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL,
    cost_src      TEXT,
    precision     TEXT    NOT NULL DEFAULT 'exact',
    PRIMARY KEY (client, session_id, event_key)
);
CREATE INDEX IF NOT EXISTS idx_ev_ts ON usage_event(ts);
CREATE INDEX IF NOT EXISTS idx_ev_client_ts ON usage_event(client, ts);
CREATE INDEX IF NOT EXISTS idx_ev_project ON usage_event(project);

CREATE TABLE IF NOT EXISTS credit_snapshot (
    client       TEXT    NOT NULL,
    session_id   TEXT    NOT NULL,
    ts           INTEGER NOT NULL,
    project      TEXT,
    model        TEXT,
    used         INTEGER NOT NULL DEFAULT 0,
    size         INTEGER NOT NULL DEFAULT 0,
    credits      REAL,
    detail_json  TEXT,
    PRIMARY KEY (client, session_id)
);
CREATE INDEX IF NOT EXISTS idx_cs_ts ON credit_snapshot(ts);

CREATE TABLE IF NOT EXISTS source_file (
    path       TEXT PRIMARY KEY,
    client     TEXT,
    mtime      REAL,
    size       INTEGER,
    last_scan  INTEGER,
    events     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scan_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      INTEGER,
    client  TEXT,
    files   INTEGER,
    added   INTEGER,
    errors  TEXT
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    """打开数据库；文件损坏时隔离并重建，避免用户直接卡死。"""
    target = Path(path or config.DB_PATH)
    try:
        return _open(target)
    except sqlite3.DatabaseError:
        quarantine(target)
        return _open(target)


def _open(target: Path) -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(target)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=8000")  # 多进程并发时等待而不是立刻失败
        conn.executescript(SCHEMA)
        _migrate(conn)
        return conn
    except Exception:
        # 必须释放句柄，否则后面无法改名隔离损坏文件
        conn.close()
        raise


def quarantine(target: Path) -> Path | None:
    """把损坏的库改名保留（便于排查），并清掉 wal/shm。"""
    if not target.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest: Path | None = target.with_name(f"{target.stem}.corrupt-{stamp}{target.suffix}")
    try:
        if dest is None:
            return None
        target.rename(dest)
    except OSError:
        try:
            target.unlink()
        except OSError:
            return None
        dest = None
    for suffix in ("-wal", "-shm"):
        side = Path(str(target) + suffix)
        if side.exists():
            try:
                side.unlink()
            except OSError:
                pass
    return dest


SCHEMA_VERSION = 3


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _migrate(conn: sqlite3.Connection) -> None:
    """按 `PRAGMA user_version` 逐级升级，避免用户删库重扫。

    v1：初始四张表（由 SCHEMA 的 CREATE IF NOT EXISTS 保证）
    v2：usage_event 补 cost_src，区分「原生成本」与「按价格表回算」
    v3：补查询索引，热力图与趋势不再全表扫

    每一步都必须幂等：老库可能停在任意版本，也可能被手工改过。
    """
    version = schema_version(conn)

    if version < 2:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(usage_event)")}
        if "cost_src" not in cols:
            conn.execute("ALTER TABLE usage_event ADD COLUMN cost_src TEXT")

    if version < 3:
        # (client, ts) 与 (ts) 已由初始 schema 建好，这里只补缺的那个，
        # 顺手清掉早期试做时的重复索引
        conn.execute("DROP INDEX IF EXISTS idx_usage_ts")
        conn.execute("DROP INDEX IF EXISTS idx_usage_client_ts")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_credit_client ON credit_snapshot(client)")

    if version != SCHEMA_VERSION:
        # PRAGMA 不支持参数绑定，这里是整数常量
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()


@contextmanager
def session(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- 写入

EVENT_COLS = (
    "client, session_id, event_key, ts, project, model, input, output,"
    " cache_read, cache_write, reasoning, cost_usd, cost_src, precision"
)


def upsert_events(conn: sqlite3.Connection, rows: Iterable[tuple]) -> int:
    """返回实际新增条数（重复键被忽略）。"""
    before = conn.total_changes
    conn.executemany(
        f"INSERT OR IGNORE INTO usage_event ({EVENT_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    return conn.total_changes - before


def upsert_credits(conn: sqlite3.Connection, rows: Iterable[tuple]) -> int:
    before = conn.total_changes
    conn.executemany(
        """INSERT INTO credit_snapshot
           (client, session_id, ts, project, model, used, size, credits, detail_json)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(client, session_id) DO UPDATE SET
             ts=excluded.ts, project=excluded.project, model=excluded.model,
             used=excluded.used, size=excluded.size, credits=excluded.credits,
             detail_json=excluded.detail_json""",
        rows,
    )
    return conn.total_changes - before


def mark_file(conn: sqlite3.Connection, path: str, client: str, mtime: float, size: int, events: int) -> None:
    conn.execute(
        """INSERT INTO source_file (path, client, mtime, size, last_scan, events)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(path) DO UPDATE SET
             client=excluded.client,
             mtime=excluded.mtime, size=excluded.size,
             last_scan=excluded.last_scan, events=excluded.events""",
        (path, client, mtime, size, int(time.time()), events),
    )


def should_scan(conn: sqlite3.Connection, path: str, mtime: float, size: int) -> bool:
    row = conn.execute("SELECT mtime, size FROM source_file WHERE path=?", (path,)).fetchone()
    if row is None:
        return True
    # mtime 相同且大小相同视为未变
    return not (abs(row["mtime"] - mtime) < 1e-6 and row["size"] == size)


def log_scan(conn: sqlite3.Connection, client: str, files: int, added: int, errors: str = "") -> None:
    conn.execute(
        "INSERT INTO scan_log (ts, client, files, added, errors) VALUES (?,?,?,?,?)",
        (int(time.time()), client, files, added, errors[:2000]),
    )


# ---------------------------------------------------------------- 读取

def totals_by_client(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT client,
                  COUNT(*)                    AS calls,
                  SUM(input)                  AS input,
                  SUM(output)                 AS output,
                  SUM(cache_read)             AS cache_read,
                  SUM(cache_write)            AS cache_write,
                  SUM(reasoning)              AS reasoning,
                  SUM(cost_usd)               AS cost_usd,
                  MIN(ts) AS first_ts, MAX(ts) AS last_ts
           FROM usage_event
           GROUP BY client ORDER BY SUM(input+output+cache_read+cache_write) DESC"""
    ).fetchall()


def daily_by_client(conn: sqlite3.Connection, since_ms: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT client, ts/86400000 AS day,
                  SUM(input) AS input, SUM(output) AS output,
                  SUM(cache_read) AS cache_read, SUM(cache_write) AS cache_write,
                  SUM(cost_usd) AS cost_usd
           FROM usage_event WHERE ts >= ?
           GROUP BY client, day ORDER BY day""",
        (since_ms,),
    ).fetchall()


def by_model(conn: sqlite3.Connection, since_ms: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT client, COALESCE(model,'(unknown)') AS model,
                  COUNT(*) AS calls,
                  SUM(input+output+cache_read+cache_write) AS tokens,
                  SUM(cost_usd) AS cost_usd
           FROM usage_event WHERE ts >= ?
           GROUP BY client, model ORDER BY tokens DESC LIMIT 50""",
        (since_ms,),
    ).fetchall()


def by_project(conn: sqlite3.Connection, since_ms: int, limit: int = 30) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT client, COALESCE(project,'(unknown)') AS project,
                  SUM(input+output+cache_read+cache_write) AS tokens,
                  SUM(cost_usd) AS cost_usd,
                  MAX(ts) AS last_ts
           FROM usage_event WHERE ts >= ?
           GROUP BY client, project ORDER BY tokens DESC LIMIT ?""",
        (since_ms, limit),
    ).fetchall()


def recent_sessions(conn: sqlite3.Connection, since_ms: int, limit: int = 60) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT client, session_id, project, model,
                  MIN(ts) AS start_ts, MAX(ts) AS end_ts, COUNT(*) AS calls,
                  SUM(input+output+cache_read+cache_write) AS tokens,
                  SUM(cost_usd) AS cost_usd
           FROM usage_event WHERE ts >= ?
           GROUP BY client, session_id
           ORDER BY end_ts DESC LIMIT ?""",
        (since_ms, limit),
    ).fetchall()


def credit_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT client, session_id, ts, project, model, used, size, credits
           FROM credit_snapshot ORDER BY ts DESC"""
    ).fetchall()
