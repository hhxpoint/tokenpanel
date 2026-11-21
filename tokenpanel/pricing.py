"""成本回算。

原则：宁可显示「未定价」，也不臆造金额。
匹配顺序：免费模型 → 精确 → 前缀 → 包含。全部未命中则返回 None，
由面板标成「未定价」，并在 report 里提示补价格表。
"""

from __future__ import annotations

import json
import sqlite3

from . import config

FREE = {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0}

_cache: dict | None = None


def load(force: bool = False) -> dict:
    global _cache
    if _cache is not None and not force:
        return _cache
    try:
        _cache = json.loads(config.PRICING_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _cache = {"models": {}}
    return _cache


def resolve(model: str | None) -> tuple[dict | None, str | None]:
    """返回 (单价, 命中的键)；未命中返回 (None, None)。"""
    table = load()
    models: dict = table.get("models", {})
    if not model:
        return None, None
    name = model.lower()
    # OpenCode 等渠道的免费模型，成本恒为 0
    if "free" in name:
        return FREE, "__free__"
    if name in models:
        return models[name], name
    for key, price in models.items():
        if name.startswith(key):
            return price, key
    for key, price in models.items():
        if key in name:
            return price, key
    return None, None


def cost_of(model: str | None, inp: int, out: int, cache_read: int, cache_write: int) -> float | None:
    p, _ = resolve(model)
    if p is None:
        return None
    return (
        inp * float(p.get("input", 0))
        + out * float(p.get("output", 0))
        + cache_read * float(p.get("cache_read", 0))
        + cache_write * float(p.get("cache_write", 0))
    ) / 1_000_000


def unpriced_models(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT model FROM usage_event WHERE model IS NOT NULL AND model != ''"
    ).fetchall()
    misses = [m for (m,) in rows if resolve(m)[0] is None]
    return sorted(misses)


def recalc(conn: sqlite3.Connection) -> int:
    """按当前价格表重算；源端原生成本（cost_src=native）不动，未定价模型保持 NULL。"""
    rows = conn.execute(
        """SELECT rowid, model, input, output, cache_read, cache_write
           FROM usage_event
           WHERE cost_src IS NULL OR cost_src = 'priced'"""
    ).fetchall()
    updates = []
    for rowid, model, inp, outp, cr, cw in rows:
        cost = cost_of(model, inp, outp, cr, cw)
        if cost is not None:
            updates.append((cost, rowid))
    conn.executemany(
        "UPDATE usage_event SET cost_usd=?, cost_src='priced' WHERE rowid=?", updates
    )
    return len(updates)
