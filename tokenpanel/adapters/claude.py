"""Claude Code：~/.claude/projects/<项目>/<会话>.jsonl

一行一条消息。带 usage 的是 assistant 消息。
注意：流式响应会把同一 message.id 写成多行，usage 只在收尾那行完整，
因此按 message.id 去重并取最完整的一条，否则会重复计数。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .. import db
from .base import Adapter, ScanResult, parse_iso_ms, short_project


class ClaudeAdapter(Adapter):
    name = "claude"

    def scan(self, conn: sqlite3.Connection, force: bool = False) -> ScanResult:
        res = ScanResult()
        if not self.available():
            return res
        for path in sorted(self.root.rglob("*.jsonl")):
            try:
                stat = path.stat()
                res.files += 1
                if not force and not db.should_scan(conn, str(path), stat.st_mtime, stat.st_size):
                    continue
                added = self._scan_file(path, res)
                db.mark_file(conn, str(path), self.name, stat.st_mtime, stat.st_size, added)
            except Exception as exc:  # 单文件失败不影响整体
                res.errors.append(f"{path.name}: {exc}")
        return res

    def _scan_file(self, path: Path, res: ScanResult) -> int:
        best: dict[str, tuple] = {}
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or '"usage"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = rec.get("message")
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue

                ts = parse_iso_ms(rec.get("timestamp", ""))
                if ts is None:
                    continue
                key = msg.get("id") or rec.get("uuid")
                if not key:
                    continue
                detail = usage.get("output_tokens_details") or {}
                row = self.event(
                    client=self.name,
                    session_id=rec.get("sessionId") or rec.get("session_id") or path.stem,
                    event_key=str(key),
                    ts_ms=ts,
                    project=short_project(rec.get("cwd")),
                    model=msg.get("model"),
                    input_tokens=usage.get("input_tokens") or 0,
                    output_tokens=usage.get("output_tokens") or 0,
                    cache_read=usage.get("cache_read_input_tokens") or 0,
                    cache_write=usage.get("cache_creation_input_tokens") or 0,
                    reasoning=detail.get("thinking_tokens") or 0,
                )
                prev = best.get(str(key))
                # 同一 message 多次出现时取输出最完整的一行
                if prev is None or row[7] >= prev[7]:
                    best[str(key)] = row

        res.events.extend(best.values())
        return len(best)
