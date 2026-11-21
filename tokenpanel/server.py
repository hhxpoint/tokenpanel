"""本地 API 服务。只读本地 SQLite，不向外发任何请求。"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse

from . import config, db, ingest, pricing

WEB_DIR = Path(__file__).resolve().parent / "web"
DAY_MS = 86_400_000

app = FastAPI(title="TokenPanel", docs_url=None, redoc_url=None)


def _since(days: int) -> int:
    if days <= 0:
        return 0
    return int((time.time() - days * 86400) * 1000)


def _day_label(day: int) -> str:
    return datetime.fromtimestamp(day * 86400).strftime("%m-%d")


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
                      COUNT(DISTINCT ts/86400000) AS active_days,
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
    return JSONResponse(d)


@app.get("/api/trend")
def trend(days: int = Query(30, ge=0, le=3650)) -> JSONResponse:
    since = _since(days)
    with db.session() as conn:
        rows = conn.execute(
            """SELECT client, ts/86400000 AS day,
                      SUM(input) AS input, SUM(output) AS output,
                      SUM(cache_read) AS cache_read, SUM(cache_write) AS cache_write,
                      SUM(cost_usd) AS cost
               FROM usage_event WHERE ts >= ?
               GROUP BY client, day ORDER BY day""",
            (since,),
        ).fetchall()
    days_set = sorted({r["day"] for r in rows})
    clients = sorted({r["client"] for r in rows})
    series = {c: {"labels": [], "tokens": [], "cost": []} for c in clients}
    index = {(r["client"], r["day"]): r for r in rows}
    for day in days_set:
        for c in clients:
            r = index.get((c, day))
            s = series[c]
            s["labels"].append(_day_label(day))
            if r:
                s["tokens"].append(
                    (r["input"] or 0) + (r["output"] or 0) + (r["cache_read"] or 0) + (r["cache_write"] or 0)
                )
                s["cost"].append(round(r["cost"] or 0, 4))
            else:
                s["tokens"].append(0)
                s["cost"].append(0.0)
    return JSONResponse({"days": days_set, "series": series})


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
        total = conn.execute("SELECT COUNT(*) AS n FROM usage_event").fetchone()["n"]
    return JSONResponse(
        {
            "events": total,
            "sources": [dict(r) for r in last],
            "recent_errors": [dict(r) for r in errs],
            "unpriced_models": unpriced,
            "config": {"db": str(config.DB_PATH), "pricing": str(config.PRICING_PATH)},
        }
    )


@app.post("/api/scan")
def scan(force: bool = False) -> JSONResponse:
    return JSONResponse(ingest.run(force=force))
