"""通用探测器：嵌套查找、脏值收敛、重复行去重。"""

from __future__ import annotations

import json

from tokenpanel import db
from tokenpanel.adapters import base, generic


def test_find_usage_nested():
    node = {"a": [{"b": {"usage": {"input_tokens": 5, "output_tokens": 7}}}]}
    found = generic.find_usage(node)
    assert found is not None


def test_find_usage_requires_both_sides():
    assert generic.find_usage({"input_tokens": 5}) is None


def test_find_usage_depth_limit():
    node: dict = {"input_tokens": 1, "output_tokens": 2}
    for _ in range(8):
        node = {"n": node}
    assert generic.find_usage(node) is None, "过深的结构不应继续下钻"


def test_tokens_clamp():
    assert base._tokens(-100) == 0
    assert base._tokens("abc") == 0
    assert base._tokens(None) == 0
    assert base._tokens(10**15) == base.MAX_TOKENS_PER_CALL
    assert base._tokens("42") == 42


def test_generic_adapter_scans_jsonl(app_home):
    root = app_home / "sessions"
    root.mkdir()
    (root / "s1.jsonl").write_text(
        json.dumps({
            "type": "assistant",
            "timestamp": 1_700_000_000_000,
            "model": "claude-3-5-sonnet",
            "usage": {"input_tokens": 120, "output_tokens": 45},
        }) + "\n",
        encoding="utf-8",
    )

    adapter = generic.GenericAdapter([(root, True)], max_files=10, budget_sec=5)
    conn = db.connect()
    try:
        result = adapter.scan(conn)
    finally:
        conn.close()

    assert result.files >= 1
    assert len(result.events) == 1
    row = result.events[0]
    # (client, session, key, ts, project, model, input, output, ...)
    assert row[6] == 120 and row[7] == 45


def test_generic_adapter_clamps_dirty_values(app_home):
    root = app_home / "dirty"
    root.mkdir()
    (root / "bad.jsonl").write_text(
        json.dumps({"usage": {"input_tokens": -5, "output_tokens": 10**12}}) + "\n",
        encoding="utf-8",
    )
    adapter = generic.GenericAdapter([(root, True)], max_files=10, budget_sec=5)
    conn = db.connect()
    try:
        result = adapter.scan(conn)
    finally:
        conn.close()

    assert result.events, "脏值也应产生一条记录，只是被收敛"
    row = result.events[0]
    assert row[6] == 0
    assert row[7] == base.MAX_TOKENS_PER_CALL


def test_generic_adapter_ignores_broken_lines(app_home):
    root = app_home / "mixed"
    root.mkdir()
    (root / "m.jsonl").write_text(
        "{not json\n"
        + json.dumps({"usage": {"input_tokens": 1, "output_tokens": 2}}) + "\n",
        encoding="utf-8",
    )
    adapter = generic.GenericAdapter([(root, True)], max_files=10, budget_sec=5)
    conn = db.connect()
    try:
        result = adapter.scan(conn)
    finally:
        conn.close()
    assert len(result.events) == 1, "坏行跳过，好行照收"
