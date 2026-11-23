"""采集编排：单源异常不能拖垮整体，价格必须回算。"""

from __future__ import annotations

import sqlite3

from tokenpanel import config, db, ingest, pricing
from tokenpanel.adapters import base


class FakeAdapter(base.Adapter):
    name = "fake"

    def __init__(self, events=(), credits=(), *, boom: str = "", ok: bool = True):
        super().__init__(config.APP_HOME)
        self._events = list(events)
        self._credits = list(credits)
        self._boom = boom
        self._ok = ok

    def available(self) -> bool:
        return self._ok

    def unavailable_reason(self) -> str:
        return "测试用：无数据"

    def scan(self, conn: sqlite3.Connection, force: bool = False) -> base.ScanResult:
        if self._boom:
            raise RuntimeError(self._boom)
        res = base.ScanResult()
        res.events = self._events
        res.credits = self._credits
        res.files = 1
        return res


def _event(key: str, tokens: int = 10) -> tuple:
    return base.Adapter.event(
        client="fake", session_id="s", event_key=key,
        ts_ms=1_700_000_000_000, project="p", model="claude-3",
        input_tokens=tokens, output_tokens=tokens,
    )


def test_run_writes_events_and_recalculates(app_home, sample_pricing, monkeypatch):
    adapters = [FakeAdapter([_event("k1"), _event("k2")])]
    monkeypatch.setattr(ingest, "build", lambda include_generic=True: adapters)

    out = ingest.run()
    assert out["status"] == "ok"
    assert out["sources"]["fake"]["added"] == 2

    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM usage_event").fetchone()[0] == 2
        cost = conn.execute("SELECT cost_usd FROM usage_event LIMIT 1").fetchone()[0]
        assert cost and cost > 0, "应按价格表回算出成本"
    finally:
        conn.close()


def test_one_broken_source_does_not_stop_others(app_home, monkeypatch):
    adapters = [
        FakeAdapter([_event("a1")]),
        FakeAdapter(boom="adapter exploded"),
        FakeAdapter([_event("c1")]),
    ]
    monkeypatch.setattr(ingest, "build", lambda include_generic=True: adapters)

    out = ingest.run()
    assert out["status"] == "ok"
    assert out["sources"]["fake"]["status"] in ("ok", "error")

    conn = db.connect()
    try:
        # 两个正常源都写进去了
        keys = {r[0] for r in conn.execute("SELECT event_key FROM usage_event")}
    finally:
        conn.close()
    assert {"a1", "c1"} <= keys


def test_unavailable_source_is_skipped(app_home, monkeypatch):
    monkeypatch.setattr(
        ingest, "build",
        lambda include_generic=True: [FakeAdapter(ok=False)],
    )
    out = ingest.run()
    assert out["status"] == "ok"
    assert out["sources"]["fake"]["status"] == "skipped"


def test_duplicate_events_are_not_double_counted(app_home, monkeypatch):
    adapters = [FakeAdapter([_event("dup"), _event("dup")])]
    monkeypatch.setattr(ingest, "build", lambda include_generic=True: adapters)
    ingest.run()
    ingest.run()

    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM usage_event").fetchone()[0] == 1
    finally:
        conn.close()


def test_pricing_failure_does_not_break_ingest(app_home, monkeypatch):
    adapters = [FakeAdapter([_event("p1")])]
    monkeypatch.setattr(ingest, "build", lambda include_generic=True: adapters)

    def boom(conn):
        raise RuntimeError("pricing down")

    monkeypatch.setattr(pricing, "recalc", boom)
    out = ingest.run()
    assert out["status"] == "ok"
    assert out["priced_rows"] == 0
