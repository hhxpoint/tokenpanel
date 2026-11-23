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
from dataclasses import dataclass
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
    scannable: bool = False   # 是否交给通用探测器尝试读取

    def as_dict(self) -> dict:
        return {
            "client": self.client,
            "path": self.path,
            "supported": self.supported,
            "kind": self.kind,
            "size_mb": round(self.size_mb, 1),
            "evidence": self.evidence,
            "note": self.note,
            "scannable": self.scannable,
        }


# 这些目录不可能是「客户端会话数据」，扫了纯属浪费时间且容易误判
IGNORE_NAMES = {
    ".git", ".github", ".vscode", ".vscode-server", ".vscode-insiders", "node_modules",
    ".cache", ".npm", ".pnpm-store", ".yarn", ".ssh", ".gnupg", ".rustup", ".cargo",
    ".gradle", ".m2", ".conda", ".venv", "venv", "__pycache__", ".idea", ".android",
    "Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos", "OneDrive",
    "AppData", "Library", "Applications", "Program Files", "Program Files (x86)",
}

SESSION_SUBDIRS = ("sessions", "projects", "conversations", "chats", "threads", "history")
SESSION_SUFFIXES = (".jsonl", ".jsonl.zst", ".jsonl.zstd", ".jsonl.gz")


USAGE_MARKERS = (
    b'"usage"', b"input_tokens", b"inputtokens", b"prompt_tokens",
    b"prompttokens", b"cached_input_tokens", b"cache_read_tokens",
    b"total_tokens", b"totaltokens",
)


def _signal_in_file(path: Path, max_bytes: int = 300_000) -> tuple[bool, str]:
    try:
        if not path.name.lower().endswith(SESSION_SUFFIXES):
            return False, ""
        if path.stat().st_size > 200 * 1024 * 1024:
            return False, ""
        with path.open("rb") as fh:
            chunk = fh.read(max_bytes).lower()
    except OSError:
        return False, ""
    if any(marker in chunk for marker in USAGE_MARKERS):
        return True, path.name
    return False, ""


def _has_usage_signal(path: Path, max_files: int = 4, max_bytes: int = 300_000) -> tuple[bool, str]:
    """读前几个 jsonl 文件的开头，确认里面真的写了 usage/token 字段。

    只看「有没有 jsonl」会把 Electron 缓存、网盘客户端全都算进来，
    必须看内容才能把误报压下去。
    """
    if path.is_file():
        return _signal_in_file(path)

    seen = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d.lower() not in IGNORE_NAMES][:12]
        for name in files:
            low = name.lower()
            if not low.endswith(SESSION_SUFFIXES):
                continue
            full = Path(root) / name
            try:
                if full.stat().st_size > 200 * 1024 * 1024:
                    continue
                with full.open("rb") as fh:
                    chunk = fh.read(max_bytes).lower()
            except OSError:
                continue
            if any(marker in chunk for marker in USAGE_MARKERS):
                try:
                    rel = full.relative_to(path).as_posix()
                except ValueError:
                    rel = name
                return True, rel
            seen += 1
            if seen >= max_files:
                return False, ""
    return False, ""


def _looks_like_session_data(path: Path, max_depth: int = 3, cap_files: int = 120) -> tuple[bool, str]:
    """不认识的目录里，靠文件特征判断它是不是会话数据。

    只看三层深度、最多 120 个文件，避免在陌生目录上做全量遍历。
    命中条件：存在 sessions/projects 之类的子目录，且其中有 jsonl（含压缩）文件。
    """
    hit_dir = ""
    for name in SESSION_SUBDIRS:
        sub = path / name
        try:
            if sub.is_dir():
                hit_dir = name
                break
        except OSError:
            continue

    if not hit_dir:
        return False, ""

    seen = 0
    base_depth = len(sub.parts)
    try:
        for root, dirs, files in os.walk(path / hit_dir):
            dirs[:] = [d for d in dirs if d.lower() not in IGNORE_NAMES][:20]
            if len(Path(root).parts) - base_depth > max_depth:
                dirs[:] = []
                continue
            for f in files:
                seen += 1
                if seen > cap_files:
                    return False, ""
                low = f.lower()
                if low.endswith(SESSION_SUFFIXES):
                    return True, f"{hit_dir}/…/{f}"
    except OSError:
        return False, ""
    return False, ""


def scan_roots() -> list[Path]:
    """一级目录扫描的根：HOME + 各平台应用数据目录。"""
    roots = [HOME]
    for extra in (APPDATA, LOCALAPPDATA, APP_SUPPORT, XDG_CONFIG, XDG_DATA):
        try:
            if extra.exists() and extra != HOME:
                roots.append(extra)
        except OSError:
            continue
    return roots


def _dir_size_mb(path: Path, cap_files: int = 4000) -> float:
    """估算目录体积，最多看 cap_files 个文件，避免卡死。"""
    total = 0
    seen = 0
    try:
        for root, _dirs, files in os.walk(path):
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
                token_cols = [c for c in cols if "token" in c]
                has_in = any(("input" in c or "prompt" in c) for c in token_cols)
                has_out = any(("output" in c or "completion" in c) for c in token_cols)
                # 必须成对出现才算用量表；只有一个 token 列的多半是 API Key 之类的凭据表
                if has_in and has_out:
                    return True
                if len(token_cols) >= 2 and any("cache" in c for c in token_cols):
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
    for known, path, kind, evidence in known_paths():
        if not path.exists():
            continue
        if known in found:
            continue
        found[known] = Harness(
            client=known,
            path=str(path),
            supported=known in SUPPORTED,
            kind=kind,
            size_mb=_dir_size_mb(path) if deep else 0.0,
            evidence=evidence,
            note="" if known in SUPPORTED else UNSUPPORTED_REASON.get(known, "暂无对应采集器"),
            scannable=(known not in SUPPORTED and kind in ("jsonl_dir", "sqlite")),
        )

    # 2. 一级目录扫描（HOME 下的点目录 + 各平台的应用数据根）
    # 已被「已知位置」覆盖的目录不再重复收录（含其父目录与编辑器目录）
    covered: set[str] = set()
    for _, p, _, _ in known_paths():
        covered.add(str(p))
        try:
            covered.add(str(p.parent))
        except OSError:
            pass
    for base in (APPDATA, APP_SUPPORT, XDG_CONFIG):
        for editor in ("Code", "Cursor", "Trae", "Windsurf", "Code - Insiders"):
            covered.add(str(base / editor))

    generic_found: list[tuple[float, Harness]] = []

    for root in scan_roots():
        if not root.exists():
            continue
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                continue
            if entry.name in IGNORE_NAMES or str(entry) in covered:
                continue

            client: str | None = _match_hint(entry.name)
            if client and client in found:
                continue

            # AppData 里大量同名程序目录，要求有进一步证据才收录
            kind, evidence = _fingerprint(entry)

            if not client:
                # 名字不认识：用「有没有会话文件」这个特征兜底，
                # 这样新出现的客户端也能被发现，而不是必须等我写适配器
                if kind == "sqlite" and "含 token 字段" in evidence:
                    # 通用探测器只读 jsonl，sqlite 需要专门适配器，这里只如实报告
                    pass
                elif kind in ("unknown", "missing", "jsonl_dir"):
                    hit, ev = _looks_like_session_data(entry)
                    if not hit:
                        continue
                    # 再确认内容里确实有 usage 字段，避免把 Electron 缓存当成客户端
                    # 注意：_has_usage_signal 返回 (bool, evidence)，必须解包后再判断
                    has_usage, _ = _has_usage_signal(entry)
                    if not has_usage:
                        continue
                    kind, evidence = "jsonl_dir", ev
                else:
                    # log_dir 之类的陌生目录不采信
                    continue
                key = f"generic:{entry.name}"
                if key in found:
                    continue
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    mtime = 0.0
                generic_found.append((
                    mtime,
                    Harness(
                        client=key,
                        path=str(entry),
                        supported=False,
                        scannable=(kind == "jsonl_dir"),
                        kind=kind,
                        size_mb=_dir_size_mb(entry) if deep else 0.0,
                        evidence=evidence,
                        note="未识别的客户端，已交给通用探测器尝试读取",
                    ),
                ))
                continue

            if kind in ("unknown", "missing"):
                continue
            found[client] = Harness(
                client=client,
                path=str(entry),
                supported=False,
                scannable=(kind in ("jsonl_dir", "sqlite")),
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

    # 陌生来源最多保留 8 个，按最近活动时间排序，避免扫描面上铺得太大
    for _, harness in sorted(generic_found, key=lambda x: x[0], reverse=True)[:8]:
        found.setdefault(harness.client, harness)

    items = list(found.values())
    items.sort(key=lambda h: (not h.supported, h.client))
    return items


def summary(items: list[Harness]) -> dict:
    return {
        "total": len(items),
        "supported": [h.client for h in items if h.supported],
        "detected_only": [h.client for h in items if not h.supported],
    }


def report(deep: bool = False) -> dict:
    """给界面用的完整探测报告：找到了什么、查过哪些位置、为什么没有数据。"""
    items = discover(deep=deep)

    checked: list[dict] = []
    seen: set[str] = set()
    for client, path, kind, evidence in known_paths():
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            exists = path.exists()
        except OSError:
            exists = False
        checked.append({
            "client": client,
            "path": key,
            "exists": exists,
            "kind": kind,
            "evidence": evidence,
        })

    return {
        "found": [h.as_dict() for h in items],
        "checked": checked,
        "roots": [str(p) for p in scan_roots()],
        "summary": summary(items),
        "platform": sys.platform,
    }
