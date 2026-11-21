"""Adapter 公共契约。

每个客户端一个 adapter，职责只有一件：把本地原始记录翻译成两种统一记录之一。

- 事件（可累加）：一次模型调用产生一行 → 进 usage_event
- 快照（覆盖）：会话累计值 → 进 credit_snapshot

adapter 不允许抛异常打断整体扫描；出错记进 errors，继续处理下一个文件。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ScanResult:
    events: list[tuple] = field(default_factory=list)
    credits: list[tuple] = field(default_factory=list)
    files: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "ScanResult") -> None:
        self.events.extend(other.events)
        self.credits.extend(other.credits)
        self.files += other.files
        self.errors.extend(other.errors)


class Adapter:
    name = "base"

    def __init__(self, root: Path):
        self.root = Path(root)

    def available(self) -> bool:
        return self.root.exists()

    def scan(self, conn: sqlite3.Connection, force: bool = False) -> ScanResult:
        raise NotImplementedError

    # ---------------------------------------------------------- 工具

    @staticmethod
    def event(
        client: str,
        session_id: str,
        event_key: str,
        ts_ms: int,
        project: str | None,
        model: str | None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read: int = 0,
        cache_write: int = 0,
        reasoning: int = 0,
        cost_usd: float | None = None,
        precision: str = "exact",
    ) -> tuple:
        return (
            client,
            session_id or "(unknown)",
            event_key,
            int(ts_ms),
            project,
            model,
            int(input_tokens),
            int(output_tokens),
            int(cache_read),
            int(cache_write),
            int(reasoning),
            cost_usd,  # 源端自带成本时直接写入，None 则由 pricing 回算
            "native" if cost_usd is not None else None,
            precision,
        )


def parse_iso_ms(value: str) -> int | None:
    """解析 ISO8601（含 Z）为毫秒时间戳。失败返回 None。"""
    if not value:
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def to_ms(value: Any) -> int | None:
    """兼容秒 / 毫秒 / ISO 字符串三种时间戳表达。"""
    if value is None:
        return None
    if isinstance(value, str):
        if value.isdigit():
            n = int(value)
            return n * 1000 if n < 10**11 else n
        return parse_iso_ms(value)
    if isinstance(value, (int, float)):
        n = int(value)
        if n <= 0:
            return None
        return n * 1000 if n < 10**11 else n
    return None


def short_project(path: str | None) -> str | None:
    """把 Windows 长路径压成可读的项目名，保留末两级。"""
    if not path:
        return None
    p = str(path).replace("\\", "/").rstrip("/")
    if not p:
        return None
    parts = [x for x in p.split("/") if x]
    if not parts:
        return None
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[-1]
