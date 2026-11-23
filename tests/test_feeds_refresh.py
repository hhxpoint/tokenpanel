"""抓取编排：多源合并、失败隔离、硬预算、缓存命中。"""

from __future__ import annotations

import time

from tokenpanel import feeds

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title>Alpha Release</title><link>https://a.example/alpha</link>
  <description>alpha body</description>
  <pubDate>Sun, 22 Nov 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""


def _feeds():
    return [
        {
            "id": "a-rss", "name": "A RSS", "kind": "rss", "order": 0,
            "url": "https://a.example/feed.xml", "home": "https://a.example/", "lang": "en",
        },
        {
            "id": "b-html", "name": "B HTML", "kind": "html", "order": 1,
            "url": "https://b.example/news", "home": "https://b.example/news",
            "lang": "en", "prefixes": ["/news/"],
        },
        {
            "id": "c-bad", "name": "C Bad", "kind": "rss", "order": 2,
            "url": "https://c.example/feed.xml", "home": "https://c.example/", "lang": "en",
        },
    ]


def test_refresh_merges_and_isolates_failures(app_home, monkeypatch):
    monkeypatch.setattr(feeds, "FEEDS", _feeds())
    monkeypatch.setattr(feeds, "_enrich", lambda *a, **k: None)

    def fake_get(url, timeout, *, html, tries=2):
        if "b.example" in url:
            return (
                b'<meta name="description" content="B site description">'
                b'<a href="/news/b-model"><h3>B Model 2.0</h3></a>'
            )
        if "c.example" in url:
            raise urllib_error("boom")
        return RSS

    monkeypatch.setattr(feeds, "_get", fake_get)

    data = feeds.refresh(force=True)
    assert data["stats"]["sources"] == 3
    assert data["stats"]["sources_ok"] == 2, "一个源失败不能拖垮其他源"

    sources = {p["source_id"] for p in data["posts"]}
    assert sources == {"a-rss", "b-html"}

    bad = next(f for f in data["feeds"] if f["id"] == "c-bad")
    assert bad["ok"] is False and "boom" in bad["error"]


def test_refresh_keeps_previous_posts_when_source_fails(app_home, monkeypatch):
    feeds._save({
        "fetched_at": 1.0,
        "feeds": [],
        "posts": [{"title": "Old Post", "url": "https://old/1", "kind": "blog", "source_id": "c-bad"}],
    })
    feeds._cache = None

    monkeypatch.setattr(feeds, "FEEDS", [f for f in _feeds() if f["id"] == "c-bad"])
    monkeypatch.setattr(feeds, "_enrich", lambda *a, **k: None)
    monkeypatch.setattr(feeds, "_get", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))

    data = feeds.refresh(force=True)
    urls = {p["url"] for p in data["posts"]}
    assert "https://old/1" in urls, "失败的源必须保留上次内容"


def test_refresh_reports_offline_source_kept(app_home, monkeypatch):
    feeds._save({
        "fetched_at": 1.0,
        "feeds": [{"id": "x", "name": "X", "ok": True, "count": 7, "last_ok": 123.0}],
        "posts": [],
    })
    feeds._cache = None
    monkeypatch.setattr(feeds, "FEEDS", [{
        "id": "x", "name": "X", "kind": "rss", "order": 0,
        "url": "https://x.example/feed", "home": "", "lang": "en",
    }])
    monkeypatch.setattr(feeds, "_get", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))

    data = feeds.refresh(force=True)
    feed = data["feeds"][0]
    assert feed["ok"] is False
    assert feed["kept"] == 7, "离线时要告诉前端还有几篇旧内容可用"


def test_run_batch_respects_budget(monkeypatch):
    def slow(feed):
        time.sleep(2)
        return {"id": feed["id"], "ok": True, "posts": [], "count": 0, "ms": 0, "error": ""}

    monkeypatch.setattr(feeds, "_fetch_one", slow)
    started = time.time()
    out = feeds._run_batch([(0, {"id": "slow"})], workers=1, budget=0.2)
    elapsed = time.time() - started
    assert elapsed < 1.0, "超出预算必须立刻返回，不能等慢源"
    assert out[0]["ok"] is False
    assert "预算" in out[0]["error"]


def test_run_batch_empty():
    assert feeds._run_batch([], workers=2, budget=1) == {}


def test_stats_counts():
    posts = [
        {"kind": "blog", "source_id": "a"},
        {"kind": "paper", "source_id": "b", "org": "DeepSeek"},
        {"kind": "paper", "source_id": "b", "org": "DeepSeek"},
    ]
    stats = feeds._stats(posts, [{"ok": True}, {"ok": False}])
    assert stats["blogs"] == 1 and stats["papers"] == 2
    assert stats["sources_ok"] == 1
    assert stats["orgs"] == {"DeepSeek": 2}


class urllib_error(Exception):
    pass
