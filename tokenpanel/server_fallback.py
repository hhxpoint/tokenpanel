"""零依赖降级服务（Python 标准库）。

FastAPI 装不上或未安装时自动使用这一套，接口与 server.py 完全一致。
本地单人使用，ThreadingHTTPServer 足够。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, config, db, doctor, exporter, ingest, pricing, runtime, updater

WEB_DIR = config.WEB_DIR
DAY_MS = 86_400_000


def _since(days: int) -> int:
    return 0 if days <= 0 else int((time.time() - days * 86400) * 1000)


def _label(day: int) -> str:
    return datetime.fromtimestamp(day * 86400).strftime("%m-%d")


def _overview(days: int) -> dict:
    since = _since(days)
    with db.session() as conn:
        d = dict(
            conn.execute(
                """SELECT COUNT(*) AS calls, COALESCE(SUM(input),0) AS input,
                          COALESCE(SUM(output),0) AS output, COALESCE(SUM(cache_read),0) AS cache_read,
                          COALESCE(SUM(cache_write),0) AS cache_write, COALESCE(SUM(reasoning),0) AS reasoning,
                          COALESCE(SUM(cost_usd),0) AS cost,
                          COUNT(DISTINCT ts/86400000) AS active_days,
                          COUNT(DISTINCT client) AS clients,
                          COUNT(DISTINCT project) AS projects
                   FROM usage_event WHERE ts >= ?""",
                (since,),
            ).fetchone()
        )
        d["by_client"] = [
            dict(r)
            for r in conn.execute(
                """SELECT client, COUNT(*) AS calls,
                          SUM(input+output+cache_read+cache_write) AS tokens, SUM(cost_usd) AS cost
                   FROM usage_event WHERE ts >= ? GROUP BY client ORDER BY tokens DESC""",
                (since,),
            )
        ]
        c = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(credits),0) AS credits FROM credit_snapshot"
        ).fetchone()
    d["tokens"] = d["input"] + d["output"] + d["cache_read"] + d["cache_write"]
    tin = d["input"] + d["cache_read"]
    d["cache_hit"] = round(d["cache_read"] / tin, 4) if tin else 0.0
    d["cost"] = round(d["cost"], 4)
    d["workbuddy"] = {"sessions": c["n"], "credits": round(c["credits"], 2)}
    return d


def _trend(days: int) -> dict:
    since = _since(days)
    with db.session() as conn:
        rows = conn.execute(
            """SELECT client, ts/86400000 AS day, SUM(input) AS input, SUM(output) AS output,
                      SUM(cache_read) AS cache_read, SUM(cache_write) AS cache_write,
                      SUM(cost_usd) AS cost
               FROM usage_event WHERE ts >= ? GROUP BY client, day ORDER BY day""",
            (since,),
        ).fetchall()
    days_set = sorted({r["day"] for r in rows})
    clients = sorted({r["client"] for r in rows})
    index = {(r["client"], r["day"]): r for r in rows}
    series = {}
    for c in clients:
        s = {"labels": [], "tokens": [], "cost": []}
        for day in days_set:
            r = index.get((c, day))
            s["labels"].append(_label(day))
            s["tokens"].append(
                ((r["input"] or 0) + (r["output"] or 0) + (r["cache_read"] or 0) + (r["cache_write"] or 0))
                if r else 0
            )
            s["cost"].append(round(r["cost"] or 0, 4) if r else 0.0)
        series[c] = s
    return {"days": days_set, "series": series}


def _models(days: int) -> list:
    with db.session() as conn:
        return [
            dict(r)
            for r in conn.execute(
                """SELECT client, COALESCE(model,'(未知)') AS model, COUNT(*) AS calls,
                          SUM(input+output+cache_read+cache_write) AS tokens, SUM(cost_usd) AS cost
                   FROM usage_event WHERE ts >= ? GROUP BY client, model
                   ORDER BY tokens DESC LIMIT 40""",
                (_since(days),),
            )
        ]


def _projects(days: int) -> list:
    with db.session() as conn:
        return [
            dict(r)
            for r in conn.execute(
                """SELECT client, COALESCE(project,'(未知)') AS project,
                          SUM(input+output+cache_read+cache_write) AS tokens,
                          SUM(cost_usd) AS cost, MAX(ts) AS last_ts, COUNT(*) AS calls
                   FROM usage_event WHERE ts >= ? GROUP BY client, project
                   ORDER BY tokens DESC LIMIT 40""",
                (_since(days),),
            )
        ]


def _sessions(days: int, limit: int = 40) -> list:
    with db.session() as conn:
        return [
            dict(r)
            for r in conn.execute(
                """SELECT client, session_id, project, model, MIN(ts) AS start_ts, MAX(ts) AS end_ts,
                          COUNT(*) AS calls, SUM(input+output+cache_read+cache_write) AS tokens,
                          SUM(cost_usd) AS cost
                   FROM usage_event WHERE ts >= ? GROUP BY client, session_id
                   ORDER BY end_ts DESC LIMIT ?""",
                (_since(days), limit),
            )
        ]


def _credits() -> list:
    with db.session() as conn:
        return [
            dict(r)
            for r in conn.execute(
                """SELECT client, session_id, ts, project, model, used, size, credits
                   FROM credit_snapshot ORDER BY ts DESC"""
            )
        ]


def _health() -> dict:
    with db.session() as conn:
        sources = [
            dict(r)
            for r in conn.execute(
                "SELECT client, MAX(last_scan) AS ts, SUM(events) AS events "
                "FROM source_file GROUP BY client"
            )
        ]
        errors = [
            dict(r)
            for r in conn.execute(
                "SELECT ts, client, errors FROM scan_log WHERE errors != '' ORDER BY ts DESC LIMIT 10"
            )
        ]
        total = conn.execute("SELECT COUNT(*) AS n FROM usage_event").fetchone()["n"]
        unpriced = pricing.unpriced_models(conn)
    return {
        "events": total,
        "sources": sources,
        "recent_errors": errors,
        "unpriced_models": unpriced,
        "version": __version__,
        "deps": doctor.dependency_status(),
        "scan": runtime.scan_status(),
        "config": {"db": str(config.DB_PATH), "pricing": str(config.PRICING_PATH)},
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "TokenPanel/0.1"

    def log_message(self, fmt, *args):  # 静默默认访问日志
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8",
              extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data) -> None:
        self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        q = parse_qs(parsed.query)

        def int_q(name: str, default: int) -> int:
            try:
                return int(q.get(name, [default])[0])
            except (TypeError, ValueError):
                return default

        try:
            if path in ("/", "/index.html"):
                body = (WEB_DIR / "index.html").read_bytes()
                return self._send(200, body, "text/html; charset=utf-8")
            if path == "/api/overview":
                return self._json(_overview(int_q("days", 7)))
            if path == "/api/trend":
                return self._json(_trend(int_q("days", 30)))
            if path == "/api/models":
                return self._json(_models(int_q("days", 30)))
            if path == "/api/projects":
                return self._json(_projects(int_q("days", 30)))
            if path == "/api/sessions":
                return self._json(_sessions(int_q("days", 30), int_q("limit", 40)))
            if path == "/api/credits":
                return self._json(_credits())
            if path == "/api/health":
                return self._json(_health())
            if path == "/api/version":
                return self._json(updater.version_info())
            if path == "/api/update":
                return self._json(updater.check())
            if path == "/api/export":
                days = int_q("days", 30)
                try:
                    body = exporter.export_csv(days)
                except ValueError as exc:
                    return self._send(400, json.dumps(
                        {"error": str(exc)}, ensure_ascii=False).encode("utf-8"))
                stamp = time.strftime("%Y%m%d-%H%M")
                return self._send(
                    200, body, "text/csv; charset=utf-8",
                    {"Content-Disposition": f'attachment; filename="tokenpanel-{stamp}.csv"'},
                )
            self._send(404, b'{"error":"not found"}')
        except Exception as exc:  # 服务不因单请求异常退出
            self._send(500, json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/heartbeat":
            runtime.beat()
            return self._json({"ok": True})
        if parsed.path == "/api/scan":
            force = parse_qs(parsed.query).get("force", ["false"])[0].lower() in ("1", "true", "yes")
            # 同步执行：面板需要立即知道结果，扫描通常 1–15 秒
            try:
                runtime.scan_started()
                result = ingest.run(force=force)
                added = sum(v.get("added", 0) for v in result["sources"].values())
                runtime.scan_finished(added)
                return self._json(result)
            except Exception as exc:
                runtime.scan_finished(0, str(exc))
                return self._send(500, json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"))
        self._send(404, b'{"error":"not found"}')


def _watchdog() -> None:
    """desktop 模式下：页面关闭、心跳停止后自动退出进程。

    首次心跳到达前不判定（浏览器可能启动较慢）；
    一旦收到过心跳，就按超时窗口判断，避免关窗后残留进程。
    """
    import os

    deadline = time.time() + 45
    while not runtime.BEAT["seen"] and time.time() < deadline:
        time.sleep(1)
    while True:
        time.sleep(3)
        if runtime.should_exit():
            os._exit(0)


def serve(port: int = 8765, watch: bool = False) -> None:
    import threading

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        print(f"端口 {port} 无法绑定：{exc}")
        print(f"可能已有实例在运行；如需换端口：python -m tokenpanel serve --port {port + 1}")
        raise SystemExit(1)
    if watch:
        threading.Thread(target=_watchdog, daemon=True).start()
        print(f"面板地址: http://127.0.0.1:{port}  (标准库模式 · 窗口关闭后自动退出)")
    else:
        print(f"面板地址: http://127.0.0.1:{port}  (标准库模式)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
