"""汇率：把美元价与人民币价互相换算。

需要联网，所以做成「缓存优先」：
- 缓存 6 小时内直接复用，不重复请求
- 主源失败自动切备用源
- 两个源都失败时退回上一次缓存，并标记 stale
- 连缓存都没有才用内置参考值，且明确标记为 fallback

任何数值都要过区间校验（5 < USD/CNY < 9），异常数据一律丢弃，
宁可显示旧汇率，也不显示一个离谱的数字。
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

from . import config

TTL_SEC = int(os.environ.get("TP_FX_TTL", 6 * 3600))
SANITY_MIN, SANITY_MAX = 5.0, 9.0
class Provider(TypedDict):
    id: str
    name: str
    url: str
    home: str
    parse: Callable[[dict], tuple[float, str]]


FALLBACK_RATE = 7.0         # 仅在两源皆失败且无缓存时使用
_UA = "TokenPanel/0.1 (+https://github.com/hhxpoint/tokenpanel)"

# 依次尝试；主源用市场价，备用源用 ECB 参考价
PROVIDERS: list[Provider] = [
    {
        "id": "er-api",
        "name": "open.er-api.com",
        "url": "https://open.er-api.com/v6/latest/USD",
        "parse": lambda d: (
            float(d["rates"]["CNY"]),
            str(d.get("time_last_update_utc", "")),
        ),
        "home": "https://www.exchangerate-api.com",
    },
    {
        "id": "frankfurter",
        "name": "ECB / frankfurter.app",
        "url": "https://api.frankfurter.app/latest?from=USD&to=CNY",
        "parse": lambda d: (float(d["rates"]["CNY"]), str(d.get("date", ""))),
        "home": "https://frankfurter.app",
    },
]


def _path() -> Path:
    return Path(config.DATA_DIR) / "fx.json"


def _load_cache() -> dict | None:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and float(data.get("usd_cny") or 0) > 0:
            return data
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return None


def _save(data: dict) -> None:
    try:
        _path().parent.mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _valid(rate: float) -> bool:
    return SANITY_MIN < float(rate) < SANITY_MAX


def _fetch() -> tuple[float, str, str, str] | None:
    """返回 (rate, source_name, source_home, as_of)。"""
    for prov in PROVIDERS:
        req = urllib.request.Request(prov["url"], headers={"User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                payload = json.loads(resp.read(512 * 1024).decode("utf-8", "replace"))
            rate, as_of = prov["parse"](payload)
            if not _valid(rate):
                continue
            return rate, prov["name"], prov["home"], as_of
        except Exception:
            continue
    return None


def get(force: bool = False) -> dict:
    env_rate = os.environ.get("TP_USD_CNY", "").strip()
    if env_rate:
        try:
            rate = float(env_rate)
            if _valid(rate):
                return {
                    "usd_cny": rate,
                    "source": "环境变量 TP_USD_CNY",
                    "home": "",
                    "as_of": "",
                    "fetched_at": time.time(),
                    "stale": False,
                    "fallback": False,
                }
        except ValueError:
            pass

    cached = _load_cache()
    age = (time.time() - float(cached.get("fetched_at") or 0)) if cached else 0

    if cached and not force and age < TTL_SEC and _valid(cached["usd_cny"]):
        return {**cached, "stale": False, "fallback": False, "age": int(age)}

    fetched = _fetch()
    if fetched:
        rate, name, home, as_of = fetched
        data = {
            "usd_cny": rate,
            "source": name,
            "home": home,
            "as_of": as_of,
            "fetched_at": time.time(),
            "stale": False,
            "fallback": False,
        }
        _save(data)
        return data

    if cached and _valid(cached["usd_cny"]):
        return {**cached, "stale": True, "fallback": False,
                "note": "本次无法联网，沿用上次汇率"}

    return {
        "usd_cny": FALLBACK_RATE,
        "source": "内置参考值",
        "home": "",
        "as_of": "",
        "fetched_at": 0.0,
        "stale": True,
        "fallback": True,
        "note": "无法获取汇率，使用内置参考值 7.0",
    }


def convert(amount: float | None, from_cur: str, to_cur: str, rate: float) -> float | None:
    if amount is None:
        return None
    from_cur, to_cur = (from_cur or "").upper(), (to_cur or "").upper()
    if from_cur == to_cur:
        return float(amount)
    if not rate or rate <= 0:
        return None
    if from_cur == "USD" and to_cur == "CNY":
        return float(amount) * rate
    if from_cur == "CNY" and to_cur == "USD":
        return float(amount) / rate
    return None
