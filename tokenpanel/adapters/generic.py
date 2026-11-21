"""通用 JSONL 探测器。

不绑定任何客户端格式：只要日志里有一行 JSON 同时带着「输入 token」和「输出 token」
两类字段，就能被识别并纳入统计。用于覆盖尚未专门适配的 harness。

字段名覆盖常见写法：
  input  : input_tokens / inputTokens / prompt_tokens / promptTokens / input
  output : output_tokens / outputTokens / completion_tokens / completionTokens / output
  cache  : cache_read_input_tokens / cacheReadTokens / cached_tokens / cache_read ...

安全约束（避免重复计数与卡死）：
- 只扫描「已发现但未专门适配」的目录，专用 adapter 的路径天然被排除
- 单目录最多读 max_files 个文件，整体有 deadline
- 去重键 = 相对路径:行号:时间戳，追加日志不会重复
"""

from __future__ import annotations

import gzip
import io
import json
import re
import sqlite3
import time
from pathlib import Path

from .. import config, db
from .base import Adapter, ScanResult, parse_iso_ms, short_project, to_ms

try:
    import zstandard as zstd
except ImportError:  # pragma: no cover
    zstd = None  # type: ignore[assignment]

INPUT_KEYS = ("input_tokens", "inputTokens", "prompt_tokens", "promptTokens", "promptTokenCount")
OUTPUT_KEYS = ("output_tokens", "outputTokens", "completion_tokens", "completionTokens", "candidatesTokenCount")
CACHE_READ_KEYS = (
    "cache_read_input_tokens", "cacheReadTokens", "cached_tokens",
    "cachedContentTokenCount", "cache_read", "cacheRead",
)
CACHE_WRITE_KEYS = ("cache_creation_input_tokens", "cacheWriteTokens", "cache_write", "cacheWrite")
REASONING_KEYS = ("reasoning_tokens", "reasoningTokens", "thinking_tokens", "thoughtsTokenCount")

TS_KEYS = ("timestamp", "ts", "time", "createdAt", "created_at", "date", "datetime")
MODEL_KEYS = ("model", "modelID", "model_id", "modelName", "model_name", "engine")
SESSION_KEYS = ("sessionId", "session_id", "conversationId", "conversation_id", "thread_id", "chatId")
PROJECT_KEYS = ("cwd", "project", "projectPath", "workspace", "directory", "path")

# 前置粗筛：一行里必须同时出现「输入」和「输出」类字段名，才值得解析 JSON。
# 这一层把 qoder 这类「有 token 字样但没有 usage」的日志挡在 json.loads 之外，
# 否则逐行深遍历会让扫描从 0.1s 变成 6s。
_IN_RE = re.compile(r'"(?:input_tokens|inputTokens|prompt_tokens|promptTokens|promptTokenCount)"\s*:')
_OUT_RE = re.compile(
    r'"(?:output_tokens|outputTokens|completion_tokens|completionTokens|candidatesTokenCount)"\s*:'
)


def _pick_int(obj: dict, keys: tuple[str, ...]) -> int | None:
    for k in keys:
        v = obj.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str) and v.strip().isdigit():
            return int(v)
    return None


def _pick_str(obj: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _pick_ts(obj: dict) -> int | None:
    for k in TS_KEYS:
        if k in obj:
            ms = to_ms(obj.get(k))
            if ms:
                return ms
    return None


def find_usage(node, depth: int = 0) -> dict | None:
    """在任意嵌套结构里找到第一个同时含输入和输出 token 的对象。"""
    if depth > 5:
        return None
    if isinstance(node, dict):
        if _pick_int(node, INPUT_KEYS) is not None and _pick_int(node, OUTPUT_KEYS) is not None:
            return node
        for v in node.values():
            found = find_usage(v, depth + 1)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node[:12]:
            found = find_usage(v, depth + 1)
            if found is not None:
                return found
    return None


def _iter_lines(path: Path):
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as fh:
            yield from io.TextIOWrapper(fh, encoding="utf-8", errors="replace")
        return
    if path.suffix in (".zst", ".zstd"):
        if zstd is None:
            return
        with zstd.ZstdDecompressor().stream_reader(io.BytesIO(path.read_bytes())) as reader:
            yield from io.TextIOWrapper(reader, encoding="utf-8", errors="replace")
        return
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        yield from fh


def _walk_files(root: Path, limit: int, deadline: float, recursive: bool = True):
    """惰性遍历，随时可以因数量或时间上限中断，避免深目录卡死。"""
    count = 0
    for pattern in ("*.jsonl", "*.jsonl.zst", "*.jsonl.gz", "*.json.gz"):
        if count >= limit or time.time() > deadline:
            return
        try:
            iterator = root.rglob(pattern) if recursive else root.glob(pattern)
        except OSError:
            continue
        try:
            for path in iterator:
                yield path
                count += 1
                if count >= limit or time.time() > deadline:
                    return
        except OSError:
            continue


class GenericAdapter(Adapter):
    """roots 由外部传入：(目录, 是否递归)。"""

    name = "generic"

    def __init__(self, roots, max_files: int = 1200, budget_sec: float = 6.0):
        self.roots: list[tuple[Path, bool]] = []
        for item in roots:
            if isinstance(item, (tuple, list)):
                path, recursive = Path(item[0]), bool(item[1])
            else:
                path, recursive = Path(item), True
            if path.exists():
                self.roots.append((path, recursive))
        self.root = self.roots[0][0] if self.roots else Path(".")
        self.max_files = max_files
        self.budget_sec = budget_sec

    def available(self) -> bool:
        return bool(self.roots)

    def scan(self, conn: sqlite3.Connection, force: bool = False) -> ScanResult:
        res = ScanResult()
        deadline = time.time() + self.budget_sec
        seen = 0
        for root, recursive in self.roots:
            if time.time() > deadline or seen >= self.max_files:
                break
            # 目录名常常是 projects / sessions，用上一级才能看出是谁的
            label = root.name
            if label.lower() in ("projects", "sessions", "data", "logs") and root.parent.name:
                label = root.parent.name.lstrip(".")
            client = f"generic:{label[:24]}"
            for path in _walk_files(root, self.max_files - seen, deadline, recursive):
                if time.time() > deadline or seen >= self.max_files:
                    break
                seen += 1
                try:
                    stat = path.stat()
                    res.files += 1
                    if stat.st_size > config.MAX_FILE_BYTES:
                        continue  # 超大文件跳过，避免拖死扫描
                    if not force and not db.should_scan(conn, str(path), stat.st_mtime, stat.st_size):
                        continue
                    added = self._scan_file(path, client, root, res, deadline)
                    db.mark_file(conn, str(path), client, stat.st_mtime, stat.st_size, added)
                except Exception as exc:
                    res.errors.append(f"{path.name}: {exc}")
        return res

    def _scan_file(self, path: Path, client: str, root: Path, res: ScanResult, deadline: float) -> int:
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.name
        session_hint = path.parent.name
        count = 0

        for lineno, raw in enumerate(_iter_lines(path), start=1):
            if lineno % 4000 == 0 and time.time() > deadline:
                break  # 大文件也要能被时间上限打断
            line = raw.strip()
            if not line or not _IN_RE.search(line) or not _OUT_RE.search(line):
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            usage = find_usage(rec)
            if usage is None:
                continue

            inp = _pick_int(usage, INPUT_KEYS) or 0
            out = _pick_int(usage, OUTPUT_KEYS) or 0
            if inp + out <= 0:
                continue

            top = rec if isinstance(rec, dict) else {}
            ts = _pick_ts(top) or _pick_ts(usage) or int(path.stat().st_mtime * 1000)
            model = _pick_str(top, MODEL_KEYS) or _pick_str(usage, MODEL_KEYS)
            session = _pick_str(top, SESSION_KEYS) or f"{session_hint}/{path.stem}"
            project = short_project(_pick_str(top, PROJECT_KEYS))

            res.events.append(
                self.event(
                    client=client,
                    session_id=session,
                    event_key=f"{rel}:{lineno}:{ts}",
                    ts_ms=ts,
                    project=project,
                    model=model,
                    input_tokens=inp,
                    output_tokens=out,
                    cache_read=_pick_int(usage, CACHE_READ_KEYS) or 0,
                    cache_write=_pick_int(usage, CACHE_WRITE_KEYS) or 0,
                    reasoning=_pick_int(usage, REASONING_KEYS) or 0,
                    precision="inferred",
                )
            )
            count += 1
        return count
