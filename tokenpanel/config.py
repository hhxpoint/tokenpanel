"""路径与运行参数。

两种运行形态：
- 源码运行：以项目根目录为家目录
- PyInstaller 打包：以 exe 所在目录为家目录（可写、可携带），资源从 _MEIPASS 读

所有路径都允许用环境变量覆盖，方便测试与迁移。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

HOME = Path.home()
FROZEN = bool(getattr(sys, "frozen", False))

# 打包后资源解压到 _MEIPASS；源码运行时就是项目根目录
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
# 可写目录：打包后放在 exe 旁边，源码运行时就是项目根
APP_HOME = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
ROOT = APP_HOME  # 兼容旧引用

WEB_DIR = (BUNDLE / "tokenpanel" / "web") if FROZEN else (Path(__file__).resolve().parent / "web")

DATA_DIR = Path(os.environ.get("TOKENPANEL_DATA", APP_HOME / "data"))
DB_PATH = DATA_DIR / "tokenpanel.db"
PRICING_PATH = Path(os.environ.get("TOKENPANEL_PRICING", APP_HOME / "pricing.json"))
CATALOG_PATH = Path(os.environ.get("TOKENPANEL_CATALOG", APP_HOME / "catalog.json"))
BLOG_CACHE = DATA_DIR / "blogs.json"

# 各客户端数据源位置。用环境变量覆盖是为了测试时能指向样本目录。
SOURCES = {
    "claude": Path(os.environ.get("TP_CLAUDE_DIR", HOME / ".claude" / "projects")),
    "dsh": Path(os.environ.get("TP_DSH_DIR", HOME / ".dsh" / "sessions")),
    "codex": Path(os.environ.get("TP_CODEX_DIR", HOME / ".codex" / "sessions")),
    "opencode": Path(
        os.environ.get("TP_OPENCODE_DB", HOME / ".local" / "share" / "opencode" / "opencode.db")
    ),
    "workbuddy": Path(
        os.environ.get("TP_WORKBUDDY_DB", HOME / ".workbuddy" / "workbuddy.db")
    ),
}

# 全量重扫的阈值：单文件超过这个大小就跳过（防止误读巨大文件卡死）。
MAX_FILE_BYTES = int(os.environ.get("TP_MAX_FILE_BYTES", 512 * 1024 * 1024))

# 关闭通用探测器（只用专用 adapter）。设置 TP_DISABLE_GENERIC=1 生效。
DISABLE_GENERIC = os.environ.get("TP_DISABLE_GENERIC", "").strip().lower() in ("1", "true", "yes")


def is_writable() -> tuple[bool, str]:
    """真实写入探测。os.access 在 Windows 上不可靠，必须实际落盘一次。"""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe = DATA_DIR / ".write-probe"
        probe.write_bytes(b"ok")
        probe.unlink()
        return True, ""
    except OSError as exc:
        return False, str(exc)


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    bundled = BUNDLE / "pricing.json"
    if bundled.exists() and bundled.resolve() == PRICING_PATH.resolve():
        return
    if not PRICING_PATH.exists():
        # 首次运行：把内置价格表复制到可写目录供用户修改
        try:
            shutil.copyfile(bundled, PRICING_PATH)
        except OSError:
            pass
        _sync_catalog()
        return
    # 已存在：只补充新版本新增的模型，绝不覆盖用户改过的价格
    _merge_pricing(bundled, PRICING_PATH)
    _sync_catalog()


def _sync_catalog() -> None:
    """价格总表随版本更新：内置版本更新时覆盖，否则保留本地。

    与 pricing.json 不同，catalog.json 是只读参考数据，不存在「用户自定义价格」，
    所以按 _meta.updated 比较新旧，新版本直接覆盖，保证价格不会停留在旧版。
    """
    import json

    bundled = BUNDLE / "catalog.json"
    if not bundled.exists() or bundled.resolve() == CATALOG_PATH.resolve():
        return
    target = Path(CATALOG_PATH)
    try:
        if not target.exists():
            shutil.copyfile(bundled, target)
            return
        new_meta = json.loads(bundled.read_text(encoding="utf-8")).get("_meta", {})
        old_meta = json.loads(target.read_text(encoding="utf-8")).get("_meta", {})
        if str(new_meta.get("updated", "")) > str(old_meta.get("updated", "")):
            shutil.copyfile(bundled, target)
    except (OSError, json.JSONDecodeError):
        pass


def _merge_pricing(bundled: Path, user: Path) -> None:
    import json

    try:
        base = json.loads(bundled.read_text(encoding="utf-8"))
        current = json.loads(user.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    changed = False
    models = current.setdefault("models", {})
    for name, price in base.get("models", {}).items():
        if name not in models:
            models[name] = price
            changed = True

    sources = current.setdefault("_sources", {})
    for name, note in base.get("_sources", {}).items():
        if name not in sources:
            sources[name] = note
            changed = True

    if changed:
        try:
            user.write_text(
                json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except OSError:
            pass
