"""技术博客聚合。

只抓各家官方博客的 RSS / Atom，不爬第三方转载站。抓取结果缓存到本地
data/blogs.json，断网时照常展示上一份缓存，并如实标注「上次更新于」。

原则：
- 只在用户点击「刷新」或缓存超过 TTL 时才联网，其余时候纯本地读取
- 单个源失败不影响其他源，错误逐条记录
- 解析只用标准库，不引入 feedparser 之类的新依赖
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config

# 只收录已验证可访问的官方源；新增源只需往这里加一项
FEEDS: list[dict] = [
    {
        "id": "openai",
        "name": "OpenAI",
        "url": "https://openai.com/news/rss.xml",
        "home": "https://openai.com/news/",
        "lang": "en",
    },
    {
        "id": "qwen",
        "name": "Qwen",
        "url": "https://qwenlm.github.io/blog/index.xml",
        "home": "https://qwenlm.github.io/blog/",
        "lang": "en",
    },
    {
        "id": "nvidia",
        "name": "NVIDIA",
        "url": "https://blogs.nvidia.com/feed/",
        "home": "https://blogs.nvidia.com/",
        "lang": "en",
    },
    {
        "id": "bair",
        "name": "BAIR",
        "url": "https://bair.berkeley.edu/blog/feed.xml",
        "home": "https://bair.berkeley.edu/blog/",
        "lang": "en",
    },
]

TTL_SEC = 1800          # 半小时内不重复联网
TIMEOUT = 9
MAX_POSTS = 60
PER_FEED = 18          # 每个源最多取这么多，保证来源多样性
SUMMARY_CHARS = 220
_UA = "TokenPanel/0.1 (+https://github.com/hhxpoint/tokenpanel)"

_cache: dict | None = None


def _strip_html(text: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&apos;", "'")
    )
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > SUMMARY_CHARS:
        text = text[:SUMMARY_CHARS].rstrip() + "…"
    return text


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _text(node) -> str:
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def _parse_date(raw: str) -> float:
    """RSS 用 RFC822，Atom 用 ISO8601，两条路径都覆盖。"""
    raw = (raw or "").strip()
    if not raw:
        return 0.0
    try:
        from email.utils import parsedate_to_datetime
        from datetime import timezone

        dt = parsedate_to_datetime(raw)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
    except Exception:
        pass
    try:
        from datetime import datetime

        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def parse_feed(xml_bytes: bytes, feed: dict) -> list[dict]:
    """同时兼容 RSS 2.0 与 Atom，命名空间一律忽略。"""
    root = ET.fromstring(xml_bytes)
    posts: list[dict] = []

    for node in root.iter():
        kind = _local(node.tag)
        if kind not in ("item", "entry"):
            continue

        title = link = summary = published = ""
        for child in node:
            name = _local(child.tag)
            if name == "title":
                title = _text(child)
            elif name == "link":
                href = child.get("href")
                link = href or _text(child)
            elif name in ("description", "summary", "content"):
                if not summary:
                    summary = _text(child)
            elif name in ("pubdate", "published", "updated", "date"):
                if not published:
                    published = _text(child)

        if not title:
            continue
        posts.append(
            {
                "title": title.strip(),
                "url": link.strip(),
                "summary": _strip_html(summary),
                "published": _parse_date(published),
                "published_raw": published,
                "source": feed["name"],
                "source_id": feed["id"],
            }
        )

    return posts


def _fetch_one(feed: dict) -> dict:
    req = urllib.request.Request(feed["url"], headers={"User-Agent": _UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read(4 * 1024 * 1024)
        posts = parse_feed(raw, feed)
        return {
            "id": feed["id"],
            "name": feed["name"],
            "home": feed["home"],
            "ok": True,
            "posts": posts,
            "count": len(posts),
            "ms": int((time.time() - started) * 1000),
            "error": "",
        }
    except Exception as exc:
        return {
            "id": feed["id"],
            "name": feed["name"],
            "home": feed["home"],
            "ok": False,
            "posts": [],
            "count": 0,
            "ms": int((time.time() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _cache_path() -> Path:
    return Path(config.BLOG_CACHE)


def load_cache() -> dict:
    global _cache
    if _cache is not None:
        return _cache

    path = _cache_path()
    if not path.exists():
        _cache = {"fetched_at": 0, "feeds": [], "posts": []}
        return _cache
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        data.setdefault("fetched_at", 0)
        data.setdefault("feeds", [])
        data.setdefault("posts", [])
        _cache = data
    except Exception:
        _cache = {"fetched_at": 0, "feeds": [], "posts": [], "error": "缓存文件损坏，已忽略"}
    return _cache


def _save(data: dict) -> None:
    global _cache
    _cache = data
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def refresh(force: bool = False) -> dict:
    """抓取所有源；非强制且缓存新鲜时直接返回缓存。"""
    cached = load_cache()
    age = time.time() - float(cached.get("fetched_at") or 0)
    if not force and cached.get("posts") and age < TTL_SEC:
        return {**cached, "from_cache": True, "age": int(age)}

    with ThreadPoolExecutor(max_workers=min(6, len(FEEDS))) as pool:
        results = list(pool.map(_fetch_one, FEEDS))

    posts: list[dict] = []
    for res in results:
        # 单个源可能一次吐出上千条（OpenAI 的 feed 就是），限量取最新
        posts.extend(sorted(res["posts"], key=lambda p: p["published"], reverse=True)[:PER_FEED])

    # 同一篇可能被多个源转载，按 URL 去重
    seen: set[str] = set()
    unique = []
    for post in sorted(posts, key=lambda p: p["published"], reverse=True):
        key = post["url"] or post["title"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(post)

    data = {
        "fetched_at": time.time(),
        "feeds": [
            {k: res[k] for k in ("id", "name", "home", "ok", "count", "ms", "error")}
            for res in results
        ],
        "posts": unique[:MAX_POSTS],
        "from_cache": False,
        "age": 0,
    }
    _save(data)
    return data


def summary() -> dict:
    data = load_cache()
    return {
        "fetched_at": data.get("fetched_at", 0),
        "feeds": data.get("feeds", []),
        "count": len(data.get("posts", [])),
        "ttl": TTL_SEC,
    }
