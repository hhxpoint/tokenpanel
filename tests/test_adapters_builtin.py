"""五个内置适配器：真实格式的最小样本，防止解析回归。"""

from __future__ import annotations

import json
import sqlite3

import pytest

from tokenpanel import db
from tokenpanel.adapters.claude import ClaudeAdapter
from tokenpanel.adapters.codex import CodexAdapter
from tokenpanel.adapters.dsh import DshAdapter
from tokenpanel.adapters.opencode import OpencodeAdapter
from tokenpanel.adapters.workbuddy import WorkbuddyAdapter


def _scan(adapter, force=True):
    conn = db.connect()
    try:
        return adapter.scan(conn, force=force)
    finally:
        conn.close()


def test_claude_parses_and_dedupes(app_home):
    root = app_home / "claude" / "proj"
    root.mkdir(parents=True)
    line = {
        "timestamp": "2026-11-20T10:00:00Z",
        "sessionId": "s1",
        "cwd": "/home/me/code/demo",
        "message": {
            "id": "msg-1",
            "model": "claude-3-5-sonnet",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 10,
                "cache_creation_input_tokens": 5,
                "output_tokens_details": {"thinking_tokens": 7},
            },
        },
    }
    # 流式会重复写同一个 message.id，只有最后一行 usage 完整
    partial = json.loads(json.dumps(line))
    partial["message"]["usage"]["output_tokens"] = 20
    (root / "s1.jsonl").write_text(
        json.dumps(line) + "\n" + json.dumps(partial) + "\n", encoding="utf-8"
    )

    res = _scan(ClaudeAdapter(root))
    assert len(res.events) == 1, "同一 message.id 只能计一次"
    row = res.events[0]
    assert row[6] == 100
    assert row[7] == 50, "应保留 output 更完整的那一行"
    assert row[8] == 10 and row[9] == 5 and row[10] == 7
    assert row[2] == "msg-1"
    assert row[4] == "code/demo"


def test_codex_subtracts_cached_input(app_home):
    root = app_home / "codex" / "2026" / "11" / "20"
    root.mkdir(parents=True)
    rows = [
        {"type": "session_meta", "payload": {"id": "sess1", "cwd": "/w/demo"}},
        {"type": "turn_context", "payload": {"model": "gpt-5-codex"}},
        {
            "type": "event_msg",
            "timestamp": "2026-11-20T10:00:00Z",
            "ordinal": 1,
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 40,
                        "output_tokens": 20,
                        "reasoning_output_tokens": 5,
                    }
                },
            },
        },
    ]
    (root / "rollout-1.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )

    res = _scan(CodexAdapter(app_home / "codex"))
    assert len(res.events) == 1
    row = res.events[0]
    assert row[1] == "sess1"
    assert row[5] == "gpt-5-codex"
    assert row[6] == 60, "input 必须减去 cached，否则重复计数"
    assert row[7] == 20 and row[8] == 40 and row[10] == 5


def test_dsh_reads_multi_frame_zstd(app_home):
    zstd = pytest.importorskip("zstandard")
    root = app_home / "dsh" / "proj" / "sess1"
    root.mkdir(parents=True)

    part1 = "\n".join([
        json.dumps({"type": "session", "id": "dsh-1", "cwd": "/p/q"}),
        json.dumps({"type": "request/context", "data": {"model": "v4", "provider": "deepseek"}}),
    ]) + "\n"
    part2 = json.dumps({
        "type": "assistant/message",
        "time": 1_700_000_000_000,
        "data": {
            "message": {"id": "m1"},
            "usage": {
                "inputTokens": 10, "outputTokens": 20,
                "cacheReadTokens": 3, "cacheWriteTokens": 1,
            },
        },
    }) + "\n"

    cctx = zstd.ZstdCompressor()
    blob = cctx.compress(part1.encode()) + cctx.compress(part2.encode())
    (root / "session.v4.jsonl.zstd").write_bytes(blob)

    res = _scan(DshAdapter(app_home / "dsh"))
    assert len(res.events) == 1, "多帧压缩必须全部读出"
    row = res.events[0]
    assert row[1] == "dsh-1"
    assert row[5] == "deepseek/v4"
    assert (row[6], row[7], row[8], row[9]) == (10, 20, 3, 1)


def test_opencode_reads_sqlite_and_native_cost(app_home):
    path = app_home / "opencode" / "opencode.db"
    path.parent.mkdir(parents=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT);
        CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT);
        """
    )
    conn.execute("INSERT INTO session VALUES ('s1', '/work/demo')")
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?)",
        ("m1", "s1", 1_700_000_000_000, json.dumps({
            "modelID": "claude-3", "providerID": "anthropic", "cost": 0.42,
            "tokens": {"input": 100, "output": 50, "reasoning": 5,
                       "cache": {"read": 10, "write": 0}},
        })),
    )
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?)",
        ("m2", "s1", 1_700_000_001_000, json.dumps({
            "modelID": "claude-3", "tokens": {"input": 0, "output": 0, "cache": {}},
        })),
    )
    conn.commit()
    conn.close()

    res = _scan(OpencodeAdapter(path))
    assert len(res.events) == 1, "全 0 的失败消息要跳过"
    row = res.events[0]
    assert row[4] == "work/demo"
    assert row[5] == "anthropic/claude-3"
    assert row[11] == pytest.approx(0.42)
    assert row[12] == "native", "源端成本要标成 native，避免被价格表覆盖"


def test_workbuddy_writes_credit_snapshot(app_home):
    path = app_home / "wb" / "workbuddy.db"
    path.parent.mkdir(parents=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, cwd TEXT, model TEXT);
        CREATE TABLE session_usage (session_id TEXT, used INTEGER, size INTEGER,
                                    updated_at INTEGER, credit_json TEXT);
        """
    )
    conn.execute("INSERT INTO sessions VALUES ('s1', '/proj/demo', 'wb-model')")
    conn.execute(
        "INSERT INTO session_usage VALUES ('s1', 12000, 200000, 1700000000000, ?)",
        (json.dumps({"a": 1.5, "b": 2.5}),),
    )
    conn.commit()
    conn.close()

    res = _scan(WorkbuddyAdapter(path))
    assert res.events == [], "WorkBuddy 是快照语义，绝不能进 usage_event"
    assert len(res.credits) == 1
    credit = res.credits[0]
    assert credit[5] == 12000 and credit[6] == 200000
    assert credit[7] == pytest.approx(4.0), "credit_json 要按数值求和"
    assert credit[3] == "proj/demo"


def test_missing_root_is_not_an_error(app_home):
    for cls in (ClaudeAdapter, CodexAdapter, DshAdapter):
        res = _scan(cls(app_home / "nope"))
        assert res.events == [] and res.errors == []


def test_opencode_requires_file(app_home):
    d = app_home / "dir-as-db"
    d.mkdir()
    adapter = OpencodeAdapter(d)
    assert adapter.available() is False
    assert _scan(adapter).events == []


def test_codex_marks_files_for_incremental(app_home):
    root = app_home / "codex2"
    root.mkdir()
    (root / "rollout-a.jsonl").write_text(
        json.dumps({
            "type": "event_msg",
            "timestamp": "2026-11-20T10:00:00Z",
            "ordinal": 1,
            "payload": {"type": "token_count", "info": {"last_token_usage": {
                "input_tokens": 5, "output_tokens": 5}}},
        }) + "\n",
        encoding="utf-8",
    )
    conn = db.connect()
    try:
        adapter = CodexAdapter(root)
        first = adapter.scan(conn, force=True)
        second = adapter.scan(conn, force=False)
    finally:
        conn.close()
    assert len(first.events) == 1
    assert second.events == [], "没变更的文件不应重复解析"
