"""WorkBuddy：~/.workbuddy/workbuddy.db → session_usage 表

WorkBuddy 记录的是「会话级快照」而不是逐次 token：
- used / size      已用上下文 / 上下文窗口
- credit_json      按模型哈希分组的计费点数

因此写入 credit_snapshot（覆盖语义），绝不放进 usage_event 累加，
否则每次扫描都会把同一个会话重复计一遍。
"""

from __future__ import annotations

import json
import sqlite3

from .. import db
from .base import Adapter, ScanResult, short_project, to_ms


class WorkbuddyAdapter(Adapter):
    name = "workbuddy"

    def available(self) -> bool:
        return self.root.exists() and self.root.is_file()

    def scan(self, conn: sqlite3.Connection, force: bool = False) -> ScanResult:
        res = ScanResult()
        if not self.available():
            return res
        stat = self.root.stat()
        res.files = 1
        if not force and not db.should_scan(conn, str(self.root), stat.st_mtime, stat.st_size):
            return res

        try:
            rows = self._read()
            res.credits.extend(rows)
            db.mark_file(conn, str(self.root), self.name, stat.st_mtime, stat.st_size, len(rows))
        except Exception as exc:
            res.errors.append(f"workbuddy.db: {exc}")
        return res

    def _read(self) -> list[tuple]:
        uri = f"file:{self.root.as_posix()}?mode=ro"
        osc = sqlite3.connect(uri, uri=True, timeout=3)
        osc.row_factory = sqlite3.Row
        try:
            out: list[tuple] = []
            cur = osc.execute(
                """SELECT su.session_id, su.used, su.size, su.updated_at, su.credit_json,
                          s.cwd, s.model
                   FROM session_usage su
                   LEFT JOIN sessions s ON s.id = su.session_id"""
            )
            for row in cur:
                credits = None
                detail = None
                raw = row["credit_json"]
                if raw:
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict) and parsed:
                            credits = float(sum(v for v in parsed.values() if isinstance(v, (int, float))))
                            detail = raw
                    except (json.JSONDecodeError, TypeError, ValueError):
                        pass
                out.append(
                    (
                        self.name,
                        row["session_id"],
                        to_ms(row["updated_at"]) or 0,
                        short_project(row["cwd"]),
                        row["model"],
                        int(row["used"] or 0),
                        int(row["size"] or 0),
                        credits,
                        detail,
                    )
                )
            return out
        finally:
            osc.close()
