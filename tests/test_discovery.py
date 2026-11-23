"""自动发现：内容级识别不能误报，也不能漏报。"""

from __future__ import annotations

import sqlite3

from tokenpanel import discovery


def test_usage_signal_detects_jsonl(tmp_path):
    f = tmp_path / "session.jsonl"
    f.write_text(
        '{"type":"message","usage":{"input_tokens":12,"output_tokens":34}}\n',
        encoding="utf-8",
    )
    ok, evidence = discovery._has_usage_signal(f)
    assert ok is True
    assert evidence


def test_usage_signal_rejects_plain_jsonl(tmp_path):
    f = tmp_path / "notes.jsonl"
    f.write_text('{"title":"hello"}\n{"title":"world"}\n', encoding="utf-8")
    ok, _ = discovery._has_usage_signal(f)
    assert ok is False


def test_usage_signal_rejects_single_sided_tokens(tmp_path):
    """只有 input 没有 output，不能算用量记录。"""
    f = tmp_path / "half.json"
    f.write_text('{"input_tokens": 10}', encoding="utf-8")
    ok, _ = discovery._has_usage_signal(f)
    assert ok is False


def test_looks_like_session_data(tmp_path):
    app = tmp_path / "brandnewai"
    d = app / "sessions"
    d.mkdir(parents=True)
    (d / "2026-11-20.jsonl").write_text("{}\n", encoding="utf-8")
    ok, note = discovery._looks_like_session_data(app)
    assert ok is True and note


def test_looks_like_session_data_rejects_empty(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    ok, _ = discovery._looks_like_session_data(d)
    assert ok is False


def test_sqlite_requires_both_columns(tmp_path):
    good = tmp_path / "good.db"
    conn = sqlite3.connect(good)
    conn.execute("CREATE TABLE u (input_tokens INT, output_tokens INT)")
    conn.commit()
    conn.close()
    assert discovery._sqlite_has_tokens(good) is True

    bad = tmp_path / "bad.db"
    conn = sqlite3.connect(bad)
    conn.execute("CREATE TABLE u (input_tokens INT)")
    conn.commit()
    conn.close()
    assert discovery._sqlite_has_tokens(bad) is False


def test_sqlite_garbage_file_is_false(tmp_path):
    f = tmp_path / "not.db"
    f.write_bytes(b"nope" * 100)
    assert discovery._sqlite_has_tokens(f) is False


def test_summary_splits_supported_and_detected():
    items = [
        discovery.Harness(client="a", path="/tmp/a", kind="jsonl_dir", supported=True),
        discovery.Harness(client="b", path="/tmp/b", kind="log_dir", supported=False),
    ]
    s = discovery.summary(items)
    assert s["supported"] == ["a"]
    assert s["detected_only"] == ["b"]
