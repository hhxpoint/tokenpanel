"""计价：最长前缀匹配、免费模型、成本回算、未定价识别。"""

from __future__ import annotations

import pytest

from tokenpanel import db, pricing


def test_longest_prefix_wins(sample_pricing):
    price, key = pricing.resolve("claude-3-5-sonnet-20241022")
    assert key == "claude-3-5-sonnet", "更长的键必须优先，不能被 claude-3 抢走"
    assert price["output"] == 15.0


def test_exact_match(sample_pricing):
    price, key = pricing.resolve("gpt-4o")
    assert key == "gpt-4o" and price["input"] == 2.5


def test_case_insensitive(sample_pricing):
    _, key = pricing.resolve("GPT-4O")
    assert key == "gpt-4o"


def test_free_model_is_zero(sample_pricing):
    price, key = pricing.resolve("qwen3-coder:free")
    assert key == "__free__"
    assert price["input"] == 0 and price["output"] == 0
    assert pricing.cost_of("qwen3-coder:free", 1_000_000, 1_000_000, 0, 0) == 0


def test_unknown_model_returns_none(sample_pricing):
    assert pricing.resolve("totally-unknown-model") == (None, None)
    assert pricing.cost_of("totally-unknown-model", 1, 1, 0, 0) is None


def test_cost_math(sample_pricing):
    # 1M input @3 + 1M output @15 + 1M cache_read @0.3 + 1M cache_write @3.75
    got = pricing.cost_of("claude-3", 1_000_000, 1_000_000, 1_000_000, 1_000_000)
    assert got == pytest.approx(3.0 + 15.0 + 0.3 + 3.75)


def test_missing_pricing_file_degrades(app_home):
    """没有价格表时不能抛异常，只能返回未定价。"""
    assert pricing.load(force=True) == {"models": {}}
    assert pricing.resolve("gpt-4o") == (None, None)


def test_broken_pricing_file_degrades(app_home):
    (app_home / "pricing.json").write_text("{not json", encoding="utf-8")
    assert pricing.load(force=True) == {"models": {}}


def test_recalc_only_touches_table_rows(app_home, sample_pricing):
    conn = db.connect()
    try:
        conn.executemany(
            "INSERT INTO usage_event (client, session_id, event_key, ts, model, input, output,"
            " cost_usd, cost_src) VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ("claude", "s", "native", 1, "claude-3", 1_000_000, 0, 9.99, "native"),
                ("claude", "s", "table", 2, "claude-3", 1_000_000, 0, 0.0, "priced"),
            ],
        )
        pricing.recalc(conn)
        rows = dict(conn.execute("SELECT event_key, cost_usd FROM usage_event").fetchall())
        assert rows["native"] == pytest.approx(9.99), "原生成本不能被价格表覆盖"
        assert rows["table"] == pytest.approx(3.0)
    finally:
        conn.close()


def test_unpriced_and_unnamed(app_home, sample_pricing):
    conn = db.connect()
    try:
        conn.executemany(
            "INSERT INTO usage_event (client, session_id, event_key, ts, model, input, output)"
            " VALUES (?,?,?,?,?,?,?)",
            [
                ("a", "s", "1", 1, "mystery-model", 5, 5),
                ("a", "s", "2", 1, "", 3, 3),
            ],
        )
        assert pricing.unpriced_models(conn) == ["mystery-model"]
        stats = pricing.unnamed_stats(conn)
        assert stats["count"] == 1 and stats["tokens"] == 6
    finally:
        conn.close()
