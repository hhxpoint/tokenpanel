"""扫描调度：跑完所有 adapter → 去重入库 → 回算成本。"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from . import config, db, discovery, pricing, runtime
from .adapters.base import Adapter
from .adapters.claude import ClaudeAdapter
from .adapters.codex import CodexAdapter
from .adapters.dsh import DshAdapter
from .adapters.generic import GenericAdapter
from .adapters.opencode import OpencodeAdapter
from .adapters.workbuddy import WorkbuddyAdapter

ADAPTER_CLASSES = [ClaudeAdapter, DshAdapter, CodexAdapter, OpencodeAdapter, WorkbuddyAdapter]

SOURCE_LABELS = {
    "claude": "Claude Code",
    "codex": "Codex",
    "dsh": "dsh",
    "opencode": "OpenCode",
    "workbuddy": "WorkBuddy",
    "generic": "通用探测器",
}


def generic_roots() -> list[tuple[Path, bool]]:
    """通用探测器的扫描范围：(目录, 是否递归)。

    只对「结构化会话目录」递归；日志目录只扫一层，
    避免在 Trae 这类安装目录上做深度遍历拖慢整体扫描。
    """
    candidates: list[tuple[Path, bool]] = []
    extra = os.environ.get("TP_GENERIC_DIRS", "")
    candidates.extend((Path(p), True) for p in extra.split(os.pathsep) if p.strip())
    try:
        for h in discovery.discover():
            if not h.supported:
                candidates.append((Path(h.path), h.kind == "jsonl_dir"))
    except Exception:
        pass

    seen: set[str] = set()
    out: list[tuple[Path, bool]] = []
    for p, recursive in candidates:
        key = str(p)
        if key not in seen and p.exists():
            seen.add(key)
            out.append((p, recursive))
    return out


def build(include_generic: bool = True) -> list[Adapter]:
    adapters: list[Adapter] = [
        cls(config.SOURCES.get(cls.name, config.ROOT)) for cls in ADAPTER_CLASSES
    ]
    if include_generic and not config.DISABLE_GENERIC:
        roots = generic_roots()
        if roots:
            adapters.append(GenericAdapter(roots))
    return adapters


_RUN_LOCK = threading.Lock()


def run(
    force: bool = False,
    recalc_all: bool = False,
    include_generic: bool = True,
    prepared: bool = False,
) -> dict:
    """执行一次完整采集。

    并发保护用非阻塞锁：第二次请求立即返回 busy，而不是排队等上十几秒，
    否则界面会看起来「点了没反应」。
    """
    if not _RUN_LOCK.acquire(blocking=False):
        return {"status": "busy", "elapsed_ms": 0, "priced_rows": 0, "sources": {}}
    try:
        return _run_locked(
            force=force,
            recalc_all=recalc_all,
            include_generic=include_generic,
            prepared=prepared,
        )
    finally:
        _RUN_LOCK.release()


def _run_locked(
    force: bool = False,
    recalc_all: bool = False,
    include_generic: bool = True,
    prepared: bool = False,
) -> dict:
    started = time.time()
    summary: dict[str, dict] = {}
    adapters = build(include_generic=include_generic)

    if not prepared:
        runtime.scan_started()
    runtime.scan_plan(
        [(a.name, SOURCE_LABELS.get(a.name, a.name)) for a in adapters]
    )
    runtime.scan_note(f"计划扫描 {len(adapters)} 个数据源")

    total_added = 0
    with db.session() as conn:
        for adapter in adapters:
            label = SOURCE_LABELS.get(adapter.name, adapter.name)
            runtime.scan_enter(adapter.name)

            if not adapter.available():
                reason = adapter.unavailable_reason()
                runtime.scan_skipped(adapter.name, reason)
                runtime.scan_note(f"{label}：跳过（{reason}）")
                summary[adapter.name] = {
                    "status": "skipped",
                    "reason": reason,
                    "events": 0,
                }
                continue

            t0 = time.time()
            adapter.progress = runtime.scan_files
            try:
                result = adapter.scan(conn, force=force)
            finally:
                adapter.progress = None

            added = db.upsert_events(conn, result.events)
            credits = db.upsert_credits(conn, result.credits)
            db.log_scan(conn, adapter.name, result.files, added, "; ".join(result.errors[:3]))
            ms = int((time.time() - t0) * 1000)
            total_added += added

            runtime.scan_source_done(
                adapter.name,
                result.files,
                len(result.events),
                added,
                ms,
                "done" if not result.errors else "partial",
            )
            runtime.scan_note(
                f"{label}：{result.files} 个文件 · 解析 {len(result.events)} 条 · 新增 {added} 条 · {ms} ms"
            )

            summary[adapter.name] = {
                "status": "ok" if not result.errors else "partial",
                "files": result.files,
                "parsed": len(result.events),
                "added": added,
                "credits": credits,
                "errors": result.errors[:5],
            }

        runtime.scan_phase("pricing")
        runtime.scan_note("按价格表回算成本…")
        priced = pricing.recalc(conn) if recalc_all else _fill_missing_cost(conn)

    runtime.scan_note(f"完成：共新增 {total_added} 条")
    runtime.scan_finished(total_added)
    return {
        "status": "ok",
        "elapsed_ms": int((time.time() - started) * 1000),
        "priced_rows": priced,
        "sources": summary,
    }


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
