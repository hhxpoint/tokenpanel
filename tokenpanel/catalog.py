"""模型价格总表。

数据来源是仓库根目录的 catalog.json，按「平台 → 模型」组织，每条带
官网来源链接与核实状态。这里只做读取、校验、扁平化与检索，不内置任何价格，
避免代码里出现两套互相矛盾的数字。
"""

from __future__ import annotations

import json
from pathlib import Path

from . import config

CURRENCY_SYMBOL = {"USD": "$", "CNY": "¥"}
CURRENCY_NAME = {"USD": "美元", "CNY": "人民币"}

_cache: dict | None = None
_cache_mtime: float = 0.0


def _read(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "providers" not in raw:
        raise ValueError("catalog.json 结构不正确：缺少 providers")
    return raw


def load(force: bool = False) -> dict:
    """读取并缓存；文件改动后自动失效。"""
    global _cache, _cache_mtime

    path = Path(config.CATALOG_PATH)
    if not path.exists():
        return {
            "meta": {"updated": "", "note": "未找到 catalog.json"},
            "providers": [],
            "models": [],
            "summary": {"providers": 0, "models": 0, "verified_models": 0, "priced_models": 0},
        }

    mtime = path.stat().st_mtime
    if _cache is not None and not force and mtime == _cache_mtime:
        return _cache

    data = _read(path)
    providers = data.get("providers", [])
    rows: list[dict] = []

    for prov in providers:
        currency = str(prov.get("currency") or "USD").upper()
        for model in prov.get("models", []):
            row = {
                "provider_id": prov.get("id", ""),
                "provider": prov.get("name", prov.get("id", "")),
                "pricing_url": prov.get("pricing_url", ""),
                "currency": currency,
                "symbol": CURRENCY_SYMBOL.get(currency, currency),
                "model_id": model.get("id", ""),
                "model": model.get("name", model.get("id", "")),
                "context": model.get("context") or "—",
                "input": _num(model.get("input")),
                "output": _num(model.get("output")),
                "cache_read": _num(model.get("cache_read")),
                "cache_write": _num(model.get("cache_write")),
                "note": model.get("note") or "",
                # 平台与型号任一未核实，就按未核实展示
                "verified": bool(model.get("verified")) and bool(prov.get("verified")),
                "checked_at": prov.get("checked_at", ""),
            }
            rows.append(row)

    priced = [r for r in rows if r["input"] is not None or r["output"] is not None]
    summary = {
        "providers": len(providers),
        "models": len(rows),
        "priced_models": len(priced),
        "verified_models": sum(1 for r in rows if r["verified"] and r["input"] is not None),
        "pending_models": sum(1 for r in rows if not r["verified"]),
        "currencies": sorted({r["currency"] for r in rows}),
    }

    result = {
        "meta": data.get("_meta", {}),
        "providers": [
            {
                "id": p.get("id", ""),
                "name": p.get("name", ""),
                "currency": str(p.get("currency") or "USD").upper(),
                "pricing_url": p.get("pricing_url", ""),
                "verified": bool(p.get("verified")),
                "checked_at": p.get("checked_at", ""),
                "note": p.get("note", ""),
                "models": sum(1 for m in p.get("models", [])),
            }
            for p in providers
        ],
        "models": rows,
        "summary": summary,
    }

    _cache = result
    _cache_mtime = mtime
    return result


def _num(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def search(query: str = "", provider: str = "") -> list[dict]:
    data = load()
    rows = data["models"]
    if provider:
        rows = [r for r in rows if r["provider_id"] == provider]
    if query:
        q = query.strip().lower()
        rows = [
            r for r in rows
            if q in r["model"].lower() or q in r["model_id"].lower() or q in r["provider"].lower()
        ]
    return rows


def cheapest(currency: str, limit: int = 5) -> list[dict]:
    """按输出价排序，给「同等预算下谁更便宜」一个直观参考。"""
    rows = [
        r for r in load()["models"]
        if r["currency"] == currency and r["output"] is not None and r["output"] > 0
    ]
    return sorted(rows, key=lambda r: r["output"])[:limit]
