"""版本与更新检查。

隐私约束：只有在用户主动点击「检查更新」时才发起一次 HTTPS 请求，
平时不做任何联网行为。请求失败一律静默降级为「无法检查」。
"""

from __future__ import annotations

import json
import urllib.request

from . import __version__

RELEASES_API = "https://api.github.com/repos/hhxpoint/tokenpanel/releases/latest"
RELEASES_PAGE = "https://github.com/hhxpoint/tokenpanel/releases/latest"


def version_info() -> dict:
    import sys

    return {
        "version": __version__,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "frozen": bool(getattr(sys, "frozen", False)),
    }


def _parse(v: str) -> tuple:
    parts = []
    for chunk in str(v).split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(candidate: str, current: str) -> bool:
    return _parse(candidate) > _parse(current)


def check(timeout: float = 6.0) -> dict:
    """查询最新 release。任何异常都转成可展示的错误信息，不抛出。"""
    try:
        req = urllib.request.Request(
            RELEASES_API,
            headers={
                "User-Agent": f"TokenPanel/{__version__}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        latest = str(data.get("tag_name") or "").lstrip("v")
        if not latest:
            raise ValueError("响应缺少 tag_name")
        return {
            "current": __version__,
            "latest": latest,
            "has_update": is_newer(latest, __version__),
            "url": data.get("html_url") or RELEASES_PAGE,
            "checked": True,
            "error": "",
        }
    except Exception as exc:
        return {
            "current": __version__,
            "latest": "",
            "has_update": False,
            "url": RELEASES_PAGE,
            "checked": False,
            "error": f"{type(exc).__name__}",
        }
