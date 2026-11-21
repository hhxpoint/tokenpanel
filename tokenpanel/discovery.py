"""本机 harness 自动检索。

不依赖写死清单：先按已知特征快速定位，再对 HOME / AppData 做一级目录扫描，
用目录结构与文件指纹判断这是不是一个 AI 客户端数据目录，以及能否量化 token。

设计原则：
- 只做浅层探测（列出目录、读 sqlite 表名），不递归遍历大目录；
- 任何异常都吞掉，探测不能影响主流程；
- 明确区分「可量化」「已发现但不可量化」，不假装有数据。
"""

from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()
APPDATA = Path(os.environ.get("APPDATA", HOME / "AppData" / "Roaming"))
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local"))
APP_SUPPORT = HOME / "Library" / "Application Support"  # macOS
XDG_CONFIG = HOME / ".config"                           # Linux
XDG_DATA = HOME / ".local" / "share"

# 名字关键词 → 客户端标识。用于在 AppData 里认出目标。
NAME_HINTS: dict[str, str] = {
    "claude": "claude",
    "codex": "codex",
    "opencode": "opencode",
    "qoder": "qoder",
    "trae": "trae",
    "windsurf": "windsurf",
    "cursor": "cursor",
    "gemini": "gemini",
    "aider": "aider",
    "cline": "cline",
    "roo": "roo",
    "kilo": "kilo",
    "copilot": "copilot",
    "lingma": "lingma",
    "continue": "continue",
    "dsh": "dsh",
    "workbuddy": "workbuddy",
    "codebuddy": "codebuddy",
    "amp": "amp",
    "crush": "crush",
    "kiro": "kiro",
    "marvis": "marvis",
    "zed": "zed",
}

# 已实现 adapter 的客户端。其余只报告存在。
SUPPORTED = {"claude", "codex", "dsh", "opencode", "workbuddy"}

# 已知无法在本地拿到逐次 token 的客户端及原因，用于给出准确解释。
UNSUPPORTED_REASON = {
    "qoder": "部分会话含 usage 字段，已由通用探测器自动纳入；其余需在 Qoder 后台查看",
    "trae": "用量在云端，本地仅缓存配置",
    "cursor": "用量在云端 Dashboard",
    "windsurf": "用量在云端 Dashboard",
    "copilot": "本地只有会话索引，无 token 记录",
    "lingma": "本地无 token 记录",
    "codebuddy": "本地日志未写入 usage",
}


@dataclass
class Harness:
    client: str
    path: str
    supported: bool
    kind: str            # jsonl_dir / sqlite / log_dir / unknown
    size_mb: float = 0.0
    evidence: str = ""
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "client": self.client,
            "path": self.path,
            "supported": self.supported,
            "kind": self.kind,
            "size_mb": round(self.size_mb, 1),
            "evidence": self.evidence,
            "note": self.note,
        }


def _dir_size_mb(path: Path, cap_files: int = 4000) -> float:
    """估算目录体积，最多看 cap_files 个文件，避免卡死。"""
    total = 0
    seen = 0
    try:
        for root, dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
                seen += 1
                if seen >= cap_files:
                    return total / 1_048_576
    except OSError:
        pass
    return total / 1_048_576


def _fingerprint(path: Path) -> tuple[str, str]:
    """返回 (kind, evidence)。只做浅层检查。"""
    if not path.exists():
        return "missing", ""
    try:
        names = {p.name for p in path.iterdir()}
    except OSError:
        return "unknown", ""

    if "projects" in names or "sessions" in names:
        # 各家的会话目录命名
        for sub in ("projects", "sessions"):
            d = path / sub
            if not d.is_dir():
                continue
            try:
                sample = next(d.rglob("*.jsonl"), None) or next(d.rglob("*.zst"), None)
            except (OSError, StopIteration):
                # Python 3.10 的 rglob 在超长路径上会抛 FileNotFoundError
                sample = None
            if sample:
                return "jsonl_dir", f"{sub}/{sample.suffix.lstrip('.')}"

    dbs = []
    try:
        dbs = [p for p in path.glob("*.db") if p.is_file()]
    except OSError:
        pass
    if dbs:
        for db in dbs[:3]:
            if _sqlite_has_tokens(db):
                return "sqlite", f"{db.name} 含 token 字段"
        return "sqlite", f"{dbs[0].name}（无 token 字段）"

    if "logs" in names:
        return "log_dir", "logs/"

    return "unknown", ""


def _sqlite_has_tokens(db: Path) -> bool:
    try:
        uri = f"file:{db.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=0.8)
        try:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for t in tables[:25]:
                try:
                    cols = [r[1].lower() for r in conn.execute(f'PRAGMA table_info("{t}")')]
                except sqlite3.Error:
                    continue
                if any("token" in c or "usage" in c or "credit" in c for c in cols):
                    return True
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return False


def _match_hint(name: str) -> str | None:
    """按分隔符切词匹配，避免 examples 里的 amp 这类误判。"""
    low = name.lower()
    parts = [p for p in low.replace("_", "-").replace(".", "-").split("-") if p]
    for hint, client in NAME_HINTS.items():
        if hint in parts or low == hint:
            return client
    return None


def known_paths() -> list[tuple[str, Path, str, str]]:
    """跨平台的已知位置。返回 (客户端, 路径, 类型, 证据)。"""
    items: list[tuple[str, Path, str, str]] = [
        ("claude", HOME / ".claude" / "projects", "jsonl_dir", "projects/<项目>/<会话>.jsonl"),
        ("codex", HOME / ".codex" / "sessions", "jsonl_dir", "sessions/日期/rollout-*.jsonl.zst"),
        ("dsh", HOME / ".dsh" / "sessions", "jsonl_dir", "sessions/<项目>/<会话>/session.v4.jsonl.zstd"),
        ("opencode", XDG_DATA / "opencode", "sqlite", "opencode.db 含 tokens/cost"),
        ("workbuddy", HOME / ".workbuddy", "sqlite", "workbuddy.db → session_usage"),
        ("qoder", HOME / ".qoder" / "projects", "jsonl_dir", "projects/*.jsonl（无 usage 字段）"),
        ("gemini", HOME / ".gemini", "unknown", "Gemini CLI 数据目录"),
    ]
    if sys.platform == "win32":
        items.append(("trae", APPDATA / "Trae", "log_dir", "仅配置与缓存"))
    elif sys.platform == "darwin":
        for client, rel in (
            ("trae", "Trae"),
            ("cursor", "Cursor"),
            ("windsurf", "Windsurf"),
            ("claude", "Claude"),
        ):
            items.append((client, APP_SUPPORT / rel, "unknown", "macOS 应用数据"))
    else:
        for client, rel in (("cursor", "Cursor"), ("windsurf", "Windsurf")):
            items.append((client, XDG_CONFIG / rel, "unknown", "Linux 配置目录"))
    return items


def discover(deep: bool = False) -> list[Harness]:
    """返回本机发现的 harness 列表，按是否可量化排序。"""
    found: dict[str, Harness] = {}

    # 1. 已知位置
    for client, path, kind, evidence in known_paths():
        if not path.exists():
            continue
        if client in found:
            continue
        found[client] = Harness(
            client=client,
            path=str(path),
            supported=client in SUPPORTED,
            kind=kind,
            size_mb=_dir_size_mb(path) if deep else 0.0,
            evidence=evidence,
            note="" if client in SUPPORTED else UNSUPPORTED_REASON.get(client, "暂无对应采集器"),
        )

    # 2. 一级目录扫描（HOME 下的点目录 + 各平台的应用数据根）
    roots = [HOME]
    for extra_root in (APPDATA, LOCALAPPDATA, APP_SUPPORT, XDG_CONFIG, XDG_DATA):
        if extra_root.exists() and extra_root != HOME:
            roots.append(extra_root)
    for root in roots:
        if not root.exists():
            continue
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir():
                continue
            client = _match_hint(entry.name)
            if not client or client in found:
                continue
            # AppData 里大量同名程序目录，要求有进一步证据才收录
            kind, evidence = _fingerprint(entry)
            if kind in ("unknown", "missing"):
                continue
            found[client] = Harness(
                client=client,
                path=str(entry),
                supported=False,
                kind=kind,
                size_mb=_dir_size_mb(entry) if deep else 0.0,
                evidence=evidence,
                note=UNSUPPORTED_REASON.get(client, "检测到目录，但未确认 token 字段"),
            )

    # 3. VS Code / Cursor 系扩展的 globalStorage（按平台选取应用数据根）
    bases = [p for p in (APPDATA, APP_SUPPORT, XDG_CONFIG) if p.exists()]
    for base in bases:
        for editor in ("Code", "Cursor", "Trae", "Windsurf", "Code - Insiders"):
            gs = base / editor / "User" / "globalStorage"
            if not gs.is_dir():
                continue
            try:
                exts = [p for p in gs.iterdir() if p.is_dir()]
            except OSError:
                continue
            for ext in exts:
                client = _match_hint(ext.name)
                if not client:
                    continue
                if client in found:
                    # 已由更权威的位置收录，仅补充说明
                    if "扩展" not in found[client].evidence:
                        found[client].evidence += f" · 另有 {editor} 扩展"
                    continue
                kind, evidence = _fingerprint(ext)
                found[client] = Harness(
                    client=client,
                    path=str(ext),
                    supported=False,
                    kind=kind,
                    size_mb=_dir_size_mb(ext) if deep else 0.0,
                    evidence=f"{editor} 扩展 · {evidence}" if evidence else f"{editor} 扩展",
                    note=UNSUPPORTED_REASON.get(client, "扩展数据中未发现 token 字段"),
                )

    items = list(found.values())
    items.sort(key=lambda h: (not h.supported, h.client))
    return items


def summary(items: list[Harness]) -> dict:
    return {
        "total": len(items),
        "supported": [h.client for h in items if h.supported],
        "detected_only": [h.client for h in items if not h.supported],
    }
