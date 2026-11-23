"""存储层：幂等写入、旧库升级、损坏隔离。"""

from __future__ import annotations

import sqlite3

from tokenpanel import config, db


def _row(**over):
    base = {
        "client": "claude",
        "session_id": "s1",
        "event_key": "k1",
        "ts": 1_700_000_000_000,
        "project": "demo",
        "model": "claude-3-5-sonnet",
        "input": 100,
        "output": 50,
        "cache_read": 10,
        "cache_write": 5,
        "reasoning": 0,
        "cost_usd": 0.01,
        "cost_src": "table",
        "precision": "exact",
    }
    base.update(over)
    return (
        base["client"], base["session_id"], base["event_key"], base["ts"],
        base["project"], base["model"], base["input"], base["output"],
        base["cache_read"], base["cache_write"], base["reasoning"],
        base["cost_usd"], base["cost_src"], base["precision"],
    )


def test_new_db_has_current_version_and_indexes(app_home):
    conn = db.connect()
    try:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        assert {"idx_ev_ts", "idx_ev_client_ts", "idx_credit_client"} <= indexes
    finally:
        conn.close()


def test_upsert_is_idempotent(app_home):
    conn = db.connect()
    try:
        assert db.upsert_events(conn, [_row()]) == 1
        assert db.upsert_events(conn, [_row()]) == 0, "同一 (client, session, key) 不应重复计数"
        assert conn.execute("SELECT COUNT(*) FROM usage_event").fetchone()[0] == 1
    finally:
        conn.close()


def test_legacy_db_is_migrated_in_place(app_home):
    """模拟 v0 老库：没有 cost_src，也没有 user_version。"""
    config.ensure_dirs()
    raw = sqlite3.connect(config.DB_PATH)
    raw.executescript(
        """
        CREATE TABLE usage_event (
            client TEXT NOT NULL, session_id TEXT NOT NULL, event_key TEXT NOT NULL,
            ts INTEGER NOT NULL, project TEXT, model TEXT,
            input INTEGER NOT NULL DEFAULT 0, output INTEGER NOT NULL DEFAULT 0,
            cache_read INTEGER NOT NULL DEFAULT 0, cache_write INTEGER NOT NULL DEFAULT 0,
            reasoning INTEGER NOT NULL DEFAULT 0, cost_usd REAL,
            precision TEXT NOT NULL DEFAULT 'exact',
            PRIMARY KEY (client, session_id, event_key)
        );
        INSERT INTO usage_event (client, session_id, event_key, ts, input, output)
        VALUES ('claude', 's', 'k', 1700000000000, 10, 20);
        """
    )
    raw.commit()
    raw.close()

    conn = db.connect()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(usage_event)")}
        assert "cost_src" in cols, "老库应被补上 cost_src 列"
        assert db.schema_version(conn) == db.SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM usage_event").fetchone()[0] == 1, "迁移不能丢数据"
    finally:
        conn.close()


def test_corrupt_db_is_quarantined(app_home):
    config.ensure_dirs()
    config.DB_PATH.write_bytes(b"this is definitely not sqlite\n" * 200)

    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM usage_event").fetchone()[0] == 0
    finally:
        conn.close()

    leftovers = list(config.DATA_DIR.glob("tokenpanel.corrupt-*.db"))
    assert leftovers, "损坏的库应被改名保留，而不是直接删掉"


def test_migrate_is_idempotent(app_home):
    for _ in range(3):
        conn = db.connect()
        conn.close()
    conn = db.connect()
    try:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM usage_event").fetchone()[0] == 0
    finally:
        conn.close()
