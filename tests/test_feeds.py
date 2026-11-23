"""技术动态：解析、提取、合并、排序、机构识别、摘要清洗。"""

from __future__ import annotations

from tokenpanel import feeds

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title>Hello RSS</title><link>https://example.com/a</link>
  <description>&lt;p&gt;body &amp;amp; text&lt;/p&gt;</description>
  <pubDate>Sun, 22 Nov 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>Hello Atom</title><link rel="alternate" href="https://example.com/b"/>
  <summary>atom summary</summary><updated>2026-11-21T08:30:00Z</updated></entry>
</feed>"""

META = {"id": "t", "name": "Test", "url": "", "home": "", "order": 0}


def test_parse_rss():
    posts = feeds.parse_feed(RSS, META)
    assert len(posts) == 1
    assert posts[0]["title"] == "Hello RSS"
    assert posts[0]["url"] == "https://example.com/a"
    assert "body & text" in posts[0]["summary"]
    assert posts[0]["published"] > 0
    assert posts[0]["kind"] == "blog"


def test_parse_atom_and_paper_kind():
    feed = dict(META, paper=True)
    posts = feeds.parse_feed(ATOM, feed)
    assert len(posts) == 1
    assert posts[0]["url"] == "https://example.com/b"
    assert posts[0]["kind"] == "paper"


def test_extract_html_filters_nav_and_dedupes_slug():
    page = (
        '<a href="/blog/">博客</a>'
        '<a href="/blog/kimi-k3"><h3>Kimi K3 技术博客</h3><span>2026-10-24</span></a>'
        '<a href="/en/blog/kimi-k3"><h3>Kimi K3</h3></a>'
        '<a href="/about">关于我们</a>'
    )
    feed = {
        "id": "x", "name": "X", "url": "https://ex.com/blog", "home": "",
        "prefixes": ["/blog/"], "dedupe_slug": True,
    }
    got = feeds.extract_html(page, feed)
    assert len(got) == 1
    assert got[0]["title"] == "Kimi K3 技术博客"
    assert got[0]["published"] > 0


def test_extract_html_handles_entity_and_empty_anchor():
    page = (
        '<a href="/news/legal"></a><div class="card"><h3>法务 &amp; 安全</h3></div>'
        '<a href="/news/v4"><div class="card"><h3>DeepSeek V4</h3></div></a>'
    )
    feed = {"id": "d", "name": "D", "url": "https://ex.com/", "home": "",
            "prefixes": ["/news/"]}
    got = feeds.extract_html(page, feed)
    titles = [p["title"] for p in got]
    assert "DeepSeek V4" in titles
    assert all("&amp;" not in t for t in titles)


def test_extract_html_skips_section_home():
    page = '<a href="/news/">全部新闻</a><a href="/news/a-model">A Model Release</a>'
    feed = {"id": "d", "name": "D", "url": "https://ex.com/", "home": "",
            "prefixes": ["/news/"]}
    got = feeds.extract_html(page, feed)
    assert [p["url"] for p in got] == ["https://ex.com/news/a-model"]


def test_merge_keeps_old_and_prefers_blog():
    cached = {"posts": [{"title": "old", "url": "u1", "kind": "blog", "published": 1}]}
    fresh = [
        {"title": "new", "url": "u2", "kind": "blog", "published": 2},
        # 与 u1 同一条，但新结果缺摘要：应沿用旧摘要
        {"title": "old", "url": "u1", "kind": "blog", "published": 1, "summary": ""},
    ]
    cached["posts"][0]["summary"] = "旧摘要"
    out = feeds._merge(cached, fresh)
    assert len(out) == 2
    assert out[0]["url"] == "u2"
    assert {p["url"]: p for p in out}["u1"]["summary"] == "旧摘要"


def test_merge_blog_before_paper_same_day():
    out = feeds._merge({"posts": []}, [
        {"title": "p", "url": "p", "kind": "paper", "published": 1000},
        {"title": "b", "url": "b", "kind": "blog", "published": 900},
    ])
    assert out[0]["kind"] == "blog"


def test_merge_caps_total():
    posts = [
        {"title": f"t{i}", "url": f"u{i}", "kind": "blog", "published": i}
        for i in range(feeds.MAX_POSTS + 50)
    ]
    out = feeds._merge({"posts": []}, posts)
    assert len(out) == feeds.MAX_POSTS


def test_org_tagging_is_precise():
    assert feeds._tag_org("Kimi K3 Technical Report") == "Moonshot"
    assert feeds._tag_org("Claude 4 System Card") == "Anthropic"
    assert feeds._tag_org("Minimax-Optimal Regret Bounds") == "", "数学 minimax 不能误判"
    assert feeds._tag_org("Grokking Can Be Steered") == "", "grokking 不能误判成 xAI"
    assert feeds._tag_org("DeepSeek-V3 Technical Report") == "DeepSeek"


def test_meta_description_prefers_specific():
    html = (
        '<meta name="description" content="Site wide boilerplate description here.">'
        '<meta property="og:description" content="本文讲的是新模型在长上下文上的评测结果与新发现。">'
    )
    assert "长上下文" in feeds._meta_desc(html)
    assert "boilerplate" not in feeds._meta_desc(html)


def test_first_para_skips_navigation():
    html = (
        "<main>"
        "<p>Skip to main content News Research Policy Company Careers</p>"
        "<p>Apr 24, 2026</p>"
        "<p>We are expanding our partnership with the GSA through a new agreement that "
        "brings Claude to federal agencies at every level of government.</p>"
        "</main>"
    )
    para = feeds._first_para(html)
    assert para.startswith("We are expanding")


def test_cache_roundtrip(app_home):
    data = {
        "fetched_at": 1.0,
        "feeds": [{"id": "a", "name": "A", "ok": True, "count": 1}],
        "posts": [{"title": "t", "url": "u", "kind": "blog", "source_id": "a"}],
    }
    feeds._save(data)
    feeds._cache = None
    got = feeds.load_cache()
    assert got["posts"][0]["title"] == "t"
    assert got["posts"][0]["org"] == "", "旧缓存缺字段时应补默认值"


def test_broken_cache_is_ignored(app_home):
    app_home.joinpath("data", "blogs.json").write_text("{broken", encoding="utf-8")
    feeds._cache = None
    got = feeds.load_cache()
    assert got["posts"] == [] and "error" in got


def test_refresh_uses_cache_when_fresh(app_home, monkeypatch):
    feeds._save({
        "fetched_at": __import__("time").time(),
        "feeds": [],
        "posts": [{"title": "cached", "url": "u", "kind": "blog"}],
    })
    feeds._cache = None

    monkeypatch.setattr(feeds, "_run_batch", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("缓存新鲜时不应联网")))

    out = feeds.refresh(force=False)
    assert out["from_cache"] is True
    assert out["posts"][0]["title"] == "cached"
