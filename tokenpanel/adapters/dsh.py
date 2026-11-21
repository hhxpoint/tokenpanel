"""dsh（DeepSeek Harness）：~/.dsh/sessions/<项目>/<会话>/session.v4.jsonl.zstd

文件是 zstd 多帧拼接，必须用 stream_reader 解压，一次性 decompress 只能拿到第一帧。
事件流里 model 由 request/context 事件携带，需要跨行维护当前模型。
"""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

from .. import db
from .base import Adapter, ScanResult, iter_files, short_project, to_ms

try:
    import zstandard as zstd
except ImportError:  # pragma: no cover - 运行环境缺依赖时降级
    zstd = None  # type: ignore[assignment]


class DshAdapter(Adapter):
    name = "dsh"

    def available(self) -> bool:
        return super().available() and zstd is not None

    def unavailable_reason(self) -> str:
        if self.root.exists() and zstd is None:
            return "缺少 zstandard 依赖，运行 setup.bat 后重试"
        return "数据源不存在"

    def scan(self, conn: sqlite3.Connection, force: bool = False) -> ScanResult:
        res = ScanResult()
        if not self.available():
            if self.root.exists():
                res.errors.append("缺少 zstandard 依赖，已跳过 dsh")
            return res
        for path in iter_files(self.root, (".jsonl.zst", ".jsonl.zstd")):
            try:
                stat = path.stat()
                res.files += 1
                if not force and not db.should_scan(conn, str(path), stat.st_mtime, stat.st_size):
                    continue
                added = self._scan_file(path, res)
                db.mark_file(conn, str(path), self.name, stat.st_mtime, stat.st_size, added)
            except Exception as exc:
                res.errors.append(f"{path.parent.name}/{path.name}: {exc}")
        return res

    def _lines(self, path: Path):
        raw = path.read_bytes()
        with zstd.ZstdDecompressor().stream_reader(io.BytesIO(raw)) as reader:
            for line in io.TextIOWrapper(reader, encoding="utf-8", errors="replace"):
                line = line.strip()
                if line:
                    yield line

    def _scan_file(self, path: Path, res: ScanResult) -> int:
        session_id = path.parent.name
        project = None
        model = None
        provider = None
        seq = 0
        count = 0

        for line in self._lines(path):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = rec.get("type")
            data = rec.get("data")

            if kind == "session":
                # session 事件把 id / cwd 放在顶层，不在 data 里
                meta = data if isinstance(data, dict) else {}
                session_id = rec.get("id") or meta.get("id") or session_id
                project = short_project(rec.get("cwd") or meta.get("cwd"))
                continue

            if kind in ("request/context",) and isinstance(data, dict):
                model = data.get("model") or model
                provider = data.get("provider") or provider
                continue

            if kind == "request/header" and isinstance(data, dict):
                cfg = data.get("config") or {}
                model = cfg.get("model") or model
                provider = cfg.get("provider") or provider
                continue

            if kind != "assistant/message" or not isinstance(data, dict):
                continue

            usage = data.get("usage")
            if not isinstance(usage, dict):
                continue
            seq += 1
            msg = data.get("message") or {}
            key = msg.get("id") or f"{session_id}:{seq}"
            ts = to_ms(rec.get("time"))
            if ts is None:
                continue

            res.events.append(
                self.event(
                    client=self.name,
                    session_id=session_id,
                    event_key=str(key),
                    ts_ms=ts,
                    project=project,
                    model=(f"{provider}/{model}" if provider and model else model),
                    input_tokens=usage.get("inputTokens") or 0,
                    output_tokens=usage.get("outputTokens") or 0,
                    cache_read=usage.get("cacheReadTokens") or 0,
                    cache_write=usage.get("cacheWriteTokens") or 0,
                )
            )
            count += 1
        return count
