"""诊断包导出。

出问题时最怕「什么都没有」。这里把排查需要的信息打成一个 zip：

- `summary.json`  版本、运行环境、路径、库统计、各源最近状态
- `logs/tail.log` 最近若干行运行日志
- `logs/crash.log` 原生崩溃记录（如果有）
- `schema.txt`    数据库表结构
- `scan_log.txt`  最近 30 次扫描的摘要与错误

**不包含**任何 token 明细、会话内容、模型调用记录。只给计数与时间范围，
让用户可以在不泄露用量细节的前提下把问题说清楚。
"""

from __future__ import annotations

import io
import json
import os
import platform
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

from . import __version__, config, logsetup

_SENSITIVE = ("key", "token", "secret", "password", "passwd", "auth", "cookie")
_MAX_LOG_LINES = 600


def _mask_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in os.environ.items():
        if not name.startswith(("TP_", "TOKENPANEL_")):
            continue
        if any(word in name.lower() for word in _SENSITIVE):
            out[name] = "***"
        else:
            out[name] = value
    return out


def _db_stats() -> dict:
    path = Path(config.DB_PATH)
    info: dict = {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "ok": False,
    }
    if not path.exists():
        return info
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        try:
            info["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
            info["user_version"] = conn.execute("PRAGMA user_version").fetchone()[0]
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )]
            info["tables"] = tables
            for table in ("usage_event", "credit_snapshot", "source_file", "scan_log"):
                if table in tables:
                    info[f"{table}_rows"] = conn.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
            if "usage_event" in tables:
                row = conn.execute(
                    "SELECT MIN(ts), MAX(ts) FROM usage_event"
                ).fetchone()
                info["first_ts"], info["last_ts"] = row[0], row[1]
                info["clients"] = {
                    r[0]: r[1]
                    for r in conn.execute(
                        "SELECT client, COUNT(*) FROM usage_event GROUP BY client ORDER BY 2 DESC"
                    )
                }
            info["ok"] = True
        finally:
            conn.close()
    except sqlite3.Error as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def _scan_log() -> str:
    path = Path(config.DB_PATH)
    if not path.exists():
        return "数据库不存在"
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        try:
            rows = conn.execute(
                "SELECT ts, client, files, added, errors FROM scan_log ORDER BY ts DESC LIMIT 30"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return f"读取失败：{exc}"
    lines = []
    for ts, client, files, added, errors in rows:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts or 0))
        line = f"{stamp}  {client:<12} files={files:<5} added={added:<5}"
        if errors:
            line += f" err={errors}"
        lines.append(line)
    return "\n".join(lines) or "暂无扫描记录"


def _schema() -> str:
    path = Path(config.DB_PATH)
    if not path.exists():
        return "数据库不存在"
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        try:
            rows = conn.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return f"读取失败：{exc}"
    return "\n\n".join(f"-- {kind} {name}\n{sql}" for kind, name, sql in rows)


def _feeds_state() -> dict:
    try:
        from . import feeds

        cache = feeds.load_cache()
        return {
            "fetched_at": cache.get("fetched_at", 0),
            "posts": len(cache.get("posts", [])),
            "sources": [
                {
                    "id": f.get("id"),
                    "ok": f.get("ok"),
                    "count": f.get("count"),
                    "kept": f.get("kept"),
                    "error": (f.get("error") or "")[:160],
                }
                for f in cache.get("feeds", [])
            ],
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def build_summary() -> dict:
    return {
        "generated_at": time.time(),
        "generated_at_text": time.strftime("%Y-%m-%d %H:%M:%S"),
        "app": {
            "version": __version__,
            "frozen": config.FROZEN,
            "app_home": str(config.APP_HOME),
            "data_dir": str(config.DATA_DIR),
            "log_dir": str(logsetup.log_dir()),
            "db_path": str(config.DB_PATH),
            "pricing_path": str(config.PRICING_PATH),
            "catalog_path": str(config.CATALOG_PATH),
        },
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
            "os": platform.platform(),
            "machine": platform.machine(),
            "pid": os.getpid(),
            "argv": sys.argv[:6],
        },
        "env": _mask_env(),
        "database": _db_stats(),
        "feeds": _feeds_state(),
        "logging": logsetup.summary(),
        "privacy": "诊断包不含 token 明细、会话内容或调用记录，只含计数、路径与日志。",
    }


def build_zip() -> bytes:
    summary = build_summary()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
        zf.writestr("logs/tail.log", logsetup.tail(_MAX_LOG_LINES) or "暂无日志")
        crash = logsetup.log_dir() / logsetup.FAULT_NAME
        if crash.exists():
            try:
                zf.writestr("logs/crash.log", crash.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
        zf.writestr("schema.txt", _schema())
        zf.writestr("scan_log.txt", _scan_log())
    return buffer.getvalue()


def export_zip(path: Path | None = None) -> Path | bytes:
    """写到指定路径，或直接返回字节（供 HTTP 下载）。"""
    data = build_zip()
    if path is None:
        return data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def default_name() -> str:
    return f"tokenpanel-diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.zip"


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="tokenpanel diagnostics", description="导出诊断包")
    parser.add_argument("--out", default="", help="输出路径，默认写到当前目录")
    args = parser.parse_args(argv)

    target = Path(args.out) if args.out else Path.cwd() / default_name()
    export_zip(target)
    size = target.stat().st_size / 1024
    print(f"诊断包已生成：{target}（{size:.1f} KB）")
    print("不含 token 明细与会话内容，可直接发给开发者排查。")
    return 0
