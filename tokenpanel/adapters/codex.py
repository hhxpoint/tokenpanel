"""Codex CLI：~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl[.zst]

token 记录在 event_msg → payload.type == "token_count" 事件里。
用 last_token_usage（本回合增量），不要用 total_token_usage（累计值，累加会重复）。
Codex 的 input_tokens 已包含 cached_input_tokens，需相减得到实际新输入。
"""

from __future__ import annotations

import gzip
import io
import json
import sqlite3
from pathlib import Path

from .. import db
from .base import Adapter, ScanResult, iter_files, parse_iso_ms, short_project

try:
    import zstandard as zstd
except ImportError:  # pragma: no cover
    zstd = None  # type: ignore[assignment]


class CodexAdapter(Adapter):
    name = "codex"

    def unavailable_reason(self) -> str:
        if self.root.exists() and zstd is None:
            return "缺少 zstandard 依赖，运行 setup.bat 后重试"
        return "数据源不存在"

    def scan(self, conn: sqlite3.Connection, force: bool = False) -> ScanResult:
        res = ScanResult()
        if not self.available():
            return res
        for path in iter_files(self.root, (".jsonl", ".zst", ".gz"), prefix="rollout-"):
            try:
                stat = path.stat()
                res.files += 1
                if not force and not db.should_scan(conn, str(path), stat.st_mtime, stat.st_size):
                    continue
                added = self._scan_file(path, res)
                db.mark_file(conn, str(path), self.name, stat.st_mtime, stat.st_size, added)
            except Exception as exc:
                res.errors.append(f"{path.name}: {exc}")
        return res

    def _open(self, path: Path):
        if path.suffix == ".zst":
            if zstd is None:
                raise RuntimeError("缺少 zstandard 依赖")
            return io.TextIOWrapper(
                zstd.ZstdDecompressor().stream_reader(io.BytesIO(path.read_bytes())),
                encoding="utf-8",
                errors="replace",
            )
        if path.suffix == ".gz":
            return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace")
        return path.open("r", encoding="utf-8", errors="replace")

    def _scan_file(self, path: Path, res: ScanResult) -> int:
        session_id = path.stem
        project = None
        model = None
        count = 0

        with self._open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = rec.get("type")
                payload = rec.get("payload") or {}

                if kind == "session_meta":
                    session_id = payload.get("id") or payload.get("session_id") or session_id
                    project = short_project(payload.get("cwd")) or project
                    continue

                if kind == "turn_context":
                    model = payload.get("model") or model
                    project = short_project(payload.get("cwd")) or project
                    continue

                if kind != "event_msg" or payload.get("type") != "token_count":
                    continue

                info = payload.get("info") or {}
                usage = info.get("last_token_usage") or info.get("total_token_usage")
                if not isinstance(usage, dict):
                    continue

                ts = parse_iso_ms(rec.get("timestamp", ""))
                if ts is None:
                    continue
                cached = int(usage.get("cached_input_tokens") or 0)
                raw_input = int(usage.get("input_tokens") or 0)
                key = f"{rec.get('ordinal', '')}-{rec.get('timestamp', '')}"

                res.events.append(
                    self.event(
                        client=self.name,
                        session_id=session_id,
                        event_key=key,
                        ts_ms=ts,
                        project=project,
                        model=model,
                        input_tokens=max(0, raw_input - cached),
                        output_tokens=usage.get("output_tokens") or 0,
                        cache_read=cached,
                        cache_write=usage.get("cache_write_input_tokens") or 0,
                        reasoning=usage.get("reasoning_output_tokens") or 0,
                    )
                )
                count += 1
        return count
