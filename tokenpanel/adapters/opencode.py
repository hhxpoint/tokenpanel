"""OpenCode：~/.local/share/opencode/opencode.db（SQLite）

message 表的 data 列是 JSON，assistant 消息带 tokens / cost / modelID。
数据库可能 300MB+，以只读模式打开，避免干扰正在运行的 OpenCode。
同一会话会产生大量 tokens 全为 0 的失败消息，直接跳过。
"""

from __future__ import annotations

import json
import sqlite3

from .. import db
from .base import Adapter, ScanResult, short_project, to_ms


class OpencodeAdapter(Adapter):
    name = "opencode"

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
            rows, added = self._read()
            res.events.extend(rows)
            db.mark_file(conn, str(self.root), self.name, stat.st_mtime, stat.st_size, added)
        except Exception as exc:
            res.errors.append(f"opencode.db: {exc}")
        return res

    def _read(self) -> tuple[list[tuple], int]:
        uri = f"file:{self.root.as_posix()}?mode=ro"
        osc = sqlite3.connect(uri, uri=True, timeout=3)
        osc.row_factory = sqlite3.Row
        try:
            dirs = {
                r["id"]: r["directory"]
                for r in osc.execute("SELECT id, directory FROM session")
            }
            out: list[tuple] = []
            cur = osc.execute(
                "SELECT id, session_id, time_created, data FROM message "
                "WHERE data LIKE '%\"tokens\"%'"
            )
            for row in cur:
                try:
                    data = json.loads(row["data"])
                except (json.JSONDecodeError, TypeError):
                    continue
                tokens = data.get("tokens") or {}
                if not isinstance(tokens, dict):
                    continue
                cache = tokens.get("cache") or {}
                inp = int(tokens.get("input") or 0)
                outp = int(tokens.get("output") or 0)
                cr = int(cache.get("read") or 0)
                cw = int(cache.get("write") or 0)
                rea = int(tokens.get("reasoning") or 0)
                if inp + outp + cr + cw == 0:
                    continue  # 失败或空消息

                model = data.get("modelID")
                provider = data.get("providerID")
                sid = row["session_id"]
                ts = to_ms(row["time_created"]) or 0
                raw_cost = data.get("cost")
                cost = float(raw_cost) if isinstance(raw_cost, (int, float)) else None
                out.append(
                    self.event(
                        client=self.name,
                        session_id=sid,
                        event_key=row["id"],
                        ts_ms=ts,
                        project=short_project(dirs.get(sid)),
                        model=(f"{provider}/{model}" if provider and model else model),
                        input_tokens=inp,
                        output_tokens=outp,
                        cache_read=cr,
                        cache_write=cw,
                        reasoning=rea,
                        cost_usd=cost,
                    )
                )
            return out, len(out)
        finally:
            osc.close()
