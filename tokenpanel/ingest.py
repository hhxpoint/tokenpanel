"""扫描调度：跑完所有 adapter → 去重入库 → 回算成本。"""

from __future__ import annotations

import time

from . import config, db, pricing
from .adapters.base import Adapter
from .adapters.claude import ClaudeAdapter
from .adapters.codex import CodexAdapter
from .adapters.dsh import DshAdapter
from .adapters.opencode import OpencodeAdapter
from .adapters.workbuddy import WorkbuddyAdapter

ADAPTER_CLASSES = [ClaudeAdapter, DshAdapter, CodexAdapter, OpencodeAdapter, WorkbuddyAdapter]


def build() -> list[Adapter]:
    return [cls(config.SOURCES.get(cls.name, config.ROOT)) for cls in ADAPTER_CLASSES]


def run(force: bool = False, recalc_all: bool = False) -> dict:
    """执行一次完整采集。返回每个 adapter 的结果摘要。"""
    started = time.time()
    summary: dict[str, dict] = {}
    with db.session() as conn:
        for adapter in build():
            if not adapter.available():
                summary[adapter.name] = {"status": "skipped", "reason": "数据源不存在", "events": 0}
                continue
            result = adapter.scan(conn, force=force)
            added = db.upsert_events(conn, result.events)
            credits = db.upsert_credits(conn, result.credits)
            db.log_scan(conn, adapter.name, result.files, added, "; ".join(result.errors[:3]))
            summary[adapter.name] = {
                "status": "ok" if not result.errors else "partial",
                "files": result.files,
                "parsed": len(result.events),
                "added": added,
                "credits": credits,
                "errors": result.errors[:5],
            }
        priced = pricing.recalc(conn) if recalc_all else _fill_missing_cost(conn)
    return {"elapsed_ms": int((time.time() - started) * 1000), "priced_rows": priced, "sources": summary}


def _fill_missing_cost(conn) -> int:
    rows = conn.execute(
        """SELECT rowid, model, input, output, cache_read, cache_write
           FROM usage_event WHERE cost_usd IS NULL AND cost_src IS NULL"""
    ).fetchall()
    if not rows:
        return 0
    updates = []
    for rid, model, i, o, cr, cw in rows:
        cost = pricing.cost_of(model, i, o, cr, cw)
        if cost is not None:
            updates.append((cost, rid))
    conn.executemany(
        "UPDATE usage_event SET cost_usd=?, cost_src='priced' WHERE rowid=?", updates
    )
    return len(updates)
