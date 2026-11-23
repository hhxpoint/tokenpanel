"""本地 API 服务。只读本地 SQLite，不向外发任何请求。"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse, Response

from . import __version__, catalog, config, db, discovery, doctor, exporter, feeds, fx, ingest, pricing, runtime, updater

WEB_DIR = config.WEB_DIR
DAY_MS = 86_400_000

app = FastAPI(title="TokenPanel", docs_url=None, redoc_url=None)


def _since(days: int) -> int:
    if days <= 0:
        return 0
    return int((time.time() - days * 86400) * 1000)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", media_type="text/html; charset=utf-8")


@app.get("/api/overview")
def overview(days: int = Query(7, ge=0, le=3650)) -> JSONResponse:
    since = _since(days)
    with db.session() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS calls,
                      COALESCE(SUM(input),0) AS input,
                      COALESCE(SUM(output),0) AS output,
                      COALESCE(SUM(cache_read),0) AS cache_read,
                      COALESCE(SUM(cache_write),0) AS cache_write,
                      COALESCE(SUM(reasoning),0) AS reasoning,
                      COALESCE(SUM(cost_usd),0) AS cost,
                      COUNT(DISTINCT date(ts/1000,'unixepoch','localtime')) AS active_days,
                      COUNT(DISTINCT client) AS clients,
                      COUNT(DISTINCT project) AS projects
               FROM usage_event WHERE ts >= ?""",
            (since,),
        ).fetchone()
        by_client = conn.execute(
            """SELECT client, COUNT(*) AS calls,
                      SUM(input+output+cache_read+cache_write) AS tokens,
                      SUM(cost_usd) AS cost
               FROM usage_event WHERE ts >= ?
               GROUP BY client ORDER BY tokens DESC""",
            (since,),
        ).fetchall()
        credit = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(credits),0) AS credits FROM credit_snapshot"
        ).fetchone()

    d = dict(row)
    total_in = (d["input"] or 0) + (d["cache_read"] or 0)
    d["cache_hit"] = round(d["cache_read"] / total_in, 4) if total_in else 0.0
    d["tokens"] = (d["input"] or 0) + (d["output"] or 0) + (d["cache_read"] or 0) + (d["cache_write"] or 0)
    d["cost"] = round(d["cost"] or 0, 4)
    d["by_client"] = [dict(r) for r in by_client]
    d["workbuddy"] = {"sessions": credit["n"], "credits": round(credit["credits"] or 0, 2)}

    d["prev"] = None
    if days > 0:
        span = days * 86400 * 1000
        prev_since = since - span
        with db.session() as conn:
            prev = conn.execute(
                """SELECT COALESCE(SUM(input),0)+COALESCE(SUM(output),0)
                          +COALESCE(SUM(cache_read),0)+COALESCE(SUM(cache_write),0) AS tokens,
                          COALESCE(SUM(cost_usd),0) AS cost,
                          COUNT(*) AS calls
                   FROM usage_event WHERE ts >= ? AND ts < ?""",
                (prev_since, since),
            ).fetchone()
        d["prev"] = {
            "tokens": prev["tokens"],
            "cost": round(prev["cost"], 4),
            "calls": prev["calls"],
        }
    return JSONResponse(d)


def _heatmap(days: int = 365) -> dict:
    """按本地日期返回连续 N 天的每日消耗，缺失日补 0。"""
    days = max(7, min(days, 730))
    with db.session() as conn:
        rows = conn.execute(
            """SELECT date(ts/1000,'unixepoch','localtime') AS d,
                      SUM(input + output + cache_read + cache_write) AS tokens
               FROM usage_event GROUP BY d"""
        ).fetchall()
    totals = {r["d"]: (r["tokens"] or 0) for r in rows}

    today = datetime.now().date()
    start = today - timedelta(days=days - 1)
    labels: list[str] = []
    values: list[int] = []
    cur = start
    while cur <= today:
        key = cur.isoformat()
        labels.append(key)
        values.append(int(totals.get(key, 0)))
        cur += timedelta(days=1)

    active = sum(1 for v in values if v > 0)
    return {
        "days": labels,
        "values": values,
        "active": active,
        "peak": max(values) if values else 0,
        "total": sum(values),
        "span": days,
    }


@app.get("/api/trend")
def trend(days: int = Query(30, ge=0, le=3650)) -> JSONResponse:
    since = _since(days)
    with db.session() as conn:
        rows = conn.execute(
            """SELECT client,
                      date(ts/1000,'unixepoch','localtime') AS day,
                      SUM(input) AS input, SUM(output) AS output,
                      SUM(cache_read) AS cache_read, SUM(cache_write) AS cache_write,
                      SUM(cost_usd) AS cost
               FROM usage_event WHERE ts >= ?
               GROUP BY client, day ORDER BY day""",
            (since,),
        ).fetchall()
    days_set = sorted({r["day"] for r in rows})
    clients = sorted({r["client"] for r in rows})
    index = {(r["client"], r["day"]): r for r in rows}
    series = {c: {"tokens": [], "cost": []} for c in clients}
    for c in clients:
        for day in days_set:
            r = index.get((c, day))
            series[c]["tokens"].append(
                ((r["input"] or 0) + (r["output"] or 0) + (r["cache_read"] or 0) + (r["cache_write"] or 0))
                if r else 0
            )
            series[c]["cost"].append(round(r["cost"] or 0, 4) if r else 0.0)
    return JSONResponse({"days": days_set, "series": series})


@app.get("/api/discovery")
def discovery_report() -> JSONResponse:
    return JSONResponse(discovery.report())


@app.get("/api/fx")
def fx_rate(refresh: bool = False) -> JSONResponse:
    return JSONResponse(fx.get(force=refresh))


@app.get("/api/catalog")
def catalog_api() -> JSONResponse:
    return JSONResponse(catalog.load())


@app.get("/api/blogs")
def blogs() -> JSONResponse:
    data = feeds.load_cache()
    return JSONResponse({
        "fetched_at": data.get("fetched_at", 0),
        "feeds": data.get("feeds", []),
        "posts": data.get("posts", []),
        "count": len(data.get("posts", [])),
        "stats": data.get("stats", {}),
        "ttl": feeds.TTL_SEC,
    })


@app.post("/api/blogs/refresh")
def blogs_refresh() -> JSONResponse:
    try:
        data = feeds.refresh(force=True)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse({
        "fetched_at": data.get("fetched_at", 0),
        "feeds": data.get("feeds", []),
        "posts": data.get("posts", []),
        "count": len(data.get("posts", [])),
        "stats": data.get("stats", {}),
        "ttl": feeds.TTL_SEC,
    })


@app.get("/api/heatmap")
def heatmap(days: int = Query(365, ge=7, le=730)) -> JSONResponse:
    return JSONResponse(_heatmap(days))


@app.get("/api/models")
def models(days: int = Query(30, ge=0, le=3650)) -> JSONResponse:
    since = _since(days)
    with db.session() as conn:
        rows = conn.execute(
            """SELECT client, COALESCE(model,'(未知)') AS model, COUNT(*) AS calls,
                      SUM(input+output+cache_read+cache_write) AS tokens, SUM(cost_usd) AS cost
               FROM usage_event WHERE ts >= ?
               GROUP BY client, model ORDER BY tokens DESC LIMIT 40""",
            (since,),
        ).fetchall()
    return JSONResponse([dict(r) for r in rows])


@app.get("/api/projects")
def projects(days: int = Query(30, ge=0, le=3650)) -> JSONResponse:
    since = _since(days)
    with db.session() as conn:
        rows = conn.execute(
            """SELECT client, COALESCE(project,'(未知)') AS project,
                      SUM(input+output+cache_read+cache_write) AS tokens,
                      SUM(cost_usd) AS cost, MAX(ts) AS last_ts, COUNT(*) AS calls
               FROM usage_event WHERE ts >= ?
               GROUP BY client, project ORDER BY tokens DESC LIMIT 40""",
            (since,),
        ).fetchall()
    return JSONResponse([dict(r) for r in rows])


@app.get("/api/sessions")
def sessions(days: int = Query(30, ge=0, le=3650), limit: int = Query(40, ge=1, le=300)) -> JSONResponse:
    since = _since(days)
    with db.session() as conn:
        rows = conn.execute(
            """SELECT client, session_id, project, model,
                      MIN(ts) AS start_ts, MAX(ts) AS end_ts, COUNT(*) AS calls,
                      SUM(input+output+cache_read+cache_write) AS tokens, SUM(cost_usd) AS cost
               FROM usage_event WHERE ts >= ?
               GROUP BY client, session_id ORDER BY end_ts DESC LIMIT ?""",
            (since, limit),
        ).fetchall()
    return JSONResponse([dict(r) for r in rows])


@app.get("/api/version")
def version() -> JSONResponse:
    return JSONResponse(updater.version_info())


@app.get("/api/update")
def update() -> JSONResponse:
    return JSONResponse(updater.check())


@app.get("/api/credits")
def credits() -> JSONResponse:
    with db.session() as conn:
        rows = conn.execute(
            """SELECT client, session_id, ts, project, model, used, size, credits
               FROM credit_snapshot ORDER BY ts DESC"""
        ).fetchall()
    return JSONResponse([dict(r) for r in rows])


@app.get("/api/health")
def health() -> JSONResponse:
    with db.session() as conn:
        last = conn.execute(
            "SELECT client, MAX(last_scan) AS ts, SUM(events) AS events FROM source_file GROUP BY client"
        ).fetchall()
        errs = conn.execute(
            "SELECT ts, client, errors FROM scan_log WHERE errors != '' ORDER BY ts DESC LIMIT 10"
        ).fetchall()
        unpriced = pricing.unpriced_models(conn)
        unnamed = pricing.unnamed_stats(conn)
        total = conn.execute("SELECT COUNT(*) AS n FROM usage_event").fetchone()["n"]
    return JSONResponse(
        {
            "events": total,
            "sources": [dict(r) for r in last],
            "recent_errors": [dict(r) for r in errs],
            "unpriced_models": unpriced,
            "unnamed": unnamed,
            "version": __version__,
            "deps": doctor.dependency_status(),
            "scan": runtime.scan_status(),
            "config": {"db": str(config.DB_PATH), "pricing": str(config.PRICING_PATH)},
        }
    )


@app.post("/api/heartbeat")
def heartbeat() -> JSONResponse:
    runtime.beat()
    return JSONResponse({"ok": True})


@app.get("/api/export")
def export(days: int = Query(30, ge=0, le=3650)):
    stamp = time.strftime("%Y%m%d-%H%M")
    try:
        body = exporter.export_csv(days)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="tokenpanel-{stamp}.csv"'},
    )


@app.post("/api/scan")
def scan(force: bool = False) -> JSONResponse:
    runtime.scan_started()
    try:
        result = ingest.run(force=force)
        added = sum(v.get("added", 0) for v in result["sources"].values())
        runtime.scan_finished(added)
        return JSONResponse(result)
    except Exception as exc:
        runtime.scan_finished(0, str(exc))
        return JSONResponse({"error": str(exc)}, status_code=500)
