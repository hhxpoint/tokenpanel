"""技术动态聚合：官方博客 + arXiv 论文。

只抓各家**官方**渠道：官方博客的 RSS / Atom，或官方博客列表页（HTML 提取），
以及 arXiv 的官方 API。不碰第三方转载、聚合站、爬虫镜像。

几条硬规则：
- 只在用户点「抓取最新」或缓存过期时联网，其余时候纯本地读缓存
- 单个源失败不影响别的源；失败的源保留上次抓到的内容，状态里如实标注「离线」
- 抓到的内容与缓存合并（按 URL 去重），不会因为某个源临时抽风就整块清空
- 解析只用标准库，不引入 feedparser / bs4 之类的新依赖
"""

from __future__ import annotations

import html as htmllib
import json
import re
import ssl
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from . import config

# ---------------------------------------------------------------------------
# 源清单
#
# kind=rss   ：标准 RSS / Atom
# kind=html  ：官方博客列表页，用通用链接提取（站点没有 feed 时的兜底）
# optional   ：海外源，国内网络未必连得上，失败只标离线，不影响整体
# prefixes   ：html 源里只认这些路径前缀，避免把导航链接当文章
# exclude    ：html 源里要排除的路径（分类页、标签页）
# ---------------------------------------------------------------------------
FEEDS: list[dict] = [
    # ---- 官方博客 · 有标准 feed ----
    {
        "id": "anthropic", "name": "Anthropic", "kind": "html",
        "url": "https://www.anthropic.com/news",
        "home": "https://www.anthropic.com/news",
        "lang": "en", "prefixes": ["/news/"],
        "exclude": ["/news/announcements", "/news/partnerships", "/news/policy"],
    },
    {
        "id": "kimi", "name": "Kimi / Moonshot", "kind": "html",
        "url": "https://www.kimi.com/blog",
        "home": "https://www.kimi.com/blog",
        "lang": "zh", "prefixes": ["/blog/"], "dedupe_slug": True,
    },
    {
        "id": "minimax", "name": "MiniMax", "kind": "html",
        "url": "https://www.minimax.io/news",
        "home": "https://www.minimax.io/news",
        "lang": "en", "prefixes": ["/news/", "/blog/"],
    },
    {
        "id": "deepseek", "name": "DeepSeek", "kind": "html",
        "url": "https://www.deepseek.com/",
        "home": "https://www.deepseek.com/",
        "lang": "zh", "prefixes": ["/news/"],
    },
    {
        "id": "openai", "name": "OpenAI", "kind": "rss",
        "url": "https://openai.com/news/rss.xml",
        "home": "https://openai.com/news/", "lang": "en",
    },
    {
        "id": "qwen", "name": "Qwen", "kind": "rss",
        "url": "https://qwenlm.github.io/blog/index.xml",
        "home": "https://qwenlm.github.io/blog/", "lang": "en",
    },
    {
        "id": "nvidia", "name": "NVIDIA", "kind": "rss",
        "url": "https://blogs.nvidia.com/feed/",
        "home": "https://blogs.nvidia.com/", "lang": "en",
    },
    {
        "id": "msr", "name": "Microsoft Research", "kind": "rss",
        "url": "https://www.microsoft.com/en-us/research/feed/",
        "home": "https://www.microsoft.com/en-us/research/blog/", "lang": "en",
    },
    {
        "id": "together", "name": "Together AI", "kind": "rss",
        "url": "https://www.together.ai/blog/rss.xml",
        "home": "https://www.together.ai/blog", "lang": "en",
    },
    {
        "id": "mit", "name": "MIT News · AI", "kind": "rss",
        "url": "https://news.mit.edu/rss/topic/artificial-intelligence2",
        "home": "https://news.mit.edu/topic/artificial-intelligence2", "lang": "en",
    },
    {
        "id": "apple", "name": "Apple ML", "kind": "rss",
        "url": "https://machinelearning.apple.com/rss.xml",
        "home": "https://machinelearning.apple.com/", "lang": "en",
    },

    # ---- 官方博客 · 只有网页，走通用提取 ----

    # ---- 论文 ----
    {
        "id": "arxiv-cl", "name": "arXiv cs.CL", "kind": "rss", "timeout": 20,
        "url": ("http://export.arxiv.org/api/query?search_query=cat:cs.CL"
                "&sortBy=submittedDate&sortOrder=descending&max_results=60"),
        "home": "https://arxiv.org/list/cs.CL/recent", "lang": "en", "paper": True,
    },
    {
        "id": "arxiv-lg", "name": "arXiv cs.LG", "kind": "rss", "timeout": 20,
        "url": ("http://export.arxiv.org/api/query?search_query=cat:cs.LG"
                "&sortBy=submittedDate&sortOrder=descending&max_results=60"),
        "home": "https://arxiv.org/list/cs.LG/recent", "lang": "en", "paper": True,
    },
    {
        # 国产模型的论文往往淹没在 cs.CL 每日上千篇里，用型号名单独捞一遍
        "id": "models-cn", "name": "国产模型论文", "kind": "rss", "timeout": 20,
        "url": ("http://export.arxiv.org/api/query?search_query="
                "ti:%22Kimi+K2%22+OR+ti:%22Kimi-K2%22+OR+ti:%22MiniMax-M2%22"
                "+OR+ti:%22DeepSeek-V3%22+OR+ti:%22DeepSeek-R1%22+OR+ti:%22Qwen3%22"
                "+OR+ti:%22Hunyuan%22+OR+ti:%22GLM-4.5%22+OR+ti:%22ERNIE%22"
                "&sortBy=submittedDate&sortOrder=descending&max_results=30"),
        "home": "https://arxiv.org/", "lang": "en", "paper": True,
    },
    {
        "id": "models-en", "name": "海外模型论文", "kind": "rss", "timeout": 20,
        "url": ("http://export.arxiv.org/api/query?search_query="
                "ti:%22Claude%22+OR+ti:%22Gemini%22+OR+ti:%22GPT-5%22"
                "+OR+ti:%22Llama%22+OR+ti:%22Mistral%22+OR+ti:%22Grok-4%22"
                "&sortBy=submittedDate&sortOrder=descending&max_results=30"),
        "home": "https://arxiv.org/", "lang": "en", "paper": True,
    },

    # ---- 海外源：能连就收，连不上标离线（国内网络常见） ----
    {
        "id": "deepmind", "name": "Google DeepMind", "kind": "rss", "optional": True,
        "url": "https://deepmind.google/blog/rss.xml",
        "home": "https://deepmind.google/discover/blog/", "lang": "en",
    },
    {
        "id": "googleai", "name": "Google AI", "kind": "rss", "optional": True,
        "url": "https://blog.google/technology/ai/rss/",
        "home": "https://blog.google/technology/ai/", "lang": "en",
    },
    {
        "id": "meta", "name": "Meta AI", "kind": "rss", "optional": True,
        "url": "https://ai.meta.com/blog/rss/",
        "home": "https://ai.meta.com/blog/", "lang": "en",
    },
    {
        "id": "mistral", "name": "Mistral AI", "kind": "rss", "optional": True,
        "url": "https://mistral.ai/news/feed.xml",
        "home": "https://mistral.ai/news", "lang": "en",
    },
    {
        "id": "xai", "name": "xAI", "kind": "rss", "optional": True,
        "url": "https://x.ai/news/rss.xml",
        "home": "https://x.ai/news", "lang": "en",
    },
    {
        "id": "cohere", "name": "Cohere", "kind": "rss", "optional": True,
        "url": "https://cohere.com/blog/rss.xml",
        "home": "https://cohere.com/blog", "lang": "en",
    },
    {
        "id": "hf", "name": "Hugging Face", "kind": "rss", "optional": True,
        "url": "https://huggingface.co/blog/feed.xml",
        "home": "https://huggingface.co/blog", "lang": "en",
    },
    {
        "id": "hf-papers", "name": "HF Daily Papers", "kind": "rss", "optional": True,
        "url": "https://huggingface.co/papers/feed.xml",
        "home": "https://huggingface.co/papers", "lang": "en", "paper": True,
    },
    {
        "id": "perplexity", "name": "Perplexity", "kind": "rss", "optional": True,
        "url": "https://www.perplexity.ai/hub/blog/rss.xml",
        "home": "https://www.perplexity.ai/hub/blog", "lang": "en",
    },
    {
        "id": "tensorflow", "name": "TensorFlow", "kind": "rss", "optional": True,
        "url": "https://blog.tensorflow.org/feeds/posts/default",
        "home": "https://blog.tensorflow.org/", "lang": "en",
    },
    {
        "id": "aws-ml", "name": "AWS ML", "kind": "rss", "optional": True,
        "url": "https://aws.amazon.com/blogs/machine-learning/feed/",
        "home": "https://aws.amazon.com/blogs/machine-learning/", "lang": "en",
    },
]

for _i, _f in enumerate(FEEDS):
    _f.setdefault("order", _i)

TTL_SEC = 1800          # 半小时内不重复联网
TIMEOUT = 8             # 默认单源超时
OPTIONAL_TIMEOUT = 5    # 海外源快速失败，别拖慢整体
MAX_POSTS = 1000        # 缓存总量（够装下所有源，避免小源被截断）
PER_FEED = 40           # 每个源最多取这么多，保证来源多样性
SUMMARY_CHARS = 240

UA_FEED = "TokenPanel/0.2 (+https://github.com/hhxpoint/tokenpanel)"
UA_HTML = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_ACCEPT_FEED = (
    "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"
)

_cache: dict | None = None

# 论文归属识别：标题或摘要里出现这些词，就认为出自该机构
ORGS: list[tuple[str, tuple[str, ...]]] = [
    ("Anthropic", ("claude", "anthropic")),
    ("Moonshot", ("kimi", "moonshot")),
    # 裸 minimax 会撞上数学里的 minimax，只认型号名
    ("MiniMax", ("minimax-m", "hailuo")),
    ("miHoYo", ("mihoyo", "hoyoverse")),
    ("DeepSeek", ("deepseek",)),
    ("Qwen", ("qwen", "tongyi")),
    ("ByteDance", ("seed-oss", "doubao", "bytedance")),
    ("Zhipu", ("glm-", "zhipu")),
    ("Tencent", ("hunyuan",)),
    ("Baidu", ("ernie",)),
    ("Google", ("gemini", "deepmind")),
    ("OpenAI", ("gpt-", "openai")),
    ("Meta", ("llama",)),
    ("Mistral", ("mistral", "mixtral")),
    ("xAI", ("grok-", "grok 4")),
    ("NVIDIA", ("nvidia",)),
    ("Microsoft", ("phi-",)),
]


# ---------------------------------------------------------------------------
# 文本清洗
# ---------------------------------------------------------------------------
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
        .replace("&mdash;", "—")
        .replace("&ndash;", "–")
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


_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}


def find_date(text: str) -> float:
    """列表页里的日期格式五花八门，能认几种是几种。"""
    from datetime import datetime

    if not text:
        return 0.0

    m = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).timestamp()
        except ValueError:
            pass

    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).timestamp()
        except ValueError:
            pass

    m = re.search(r"([A-Z][a-z]{2})[a-z]*\s+(\d{1,2}),?\s+(20\d{2})", text)
    if m and m.group(1).lower() in _MONTHS:
        try:
            return datetime(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2))).timestamp()
        except ValueError:
            pass

    m = re.search(r"(\d{1,2})\s+([A-Z][a-z]{2})[a-z]*\s+(20\d{2})", text)
    if m and m.group(2).lower() in _MONTHS:
        try:
            return datetime(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1))).timestamp()
        except ValueError:
            pass

    return 0.0


def _tag_org(text: str) -> str:
    low = text.lower()
    for name, keys in ORGS:
        for key in keys:
            if key in ("glm-", "seed-", "gpt-", "phi-"):
                if key in low:
                    return name
            elif re.search(r"\b" + re.escape(key) + r"\b", low):
                return name
    return ""


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def parse_feed(xml_bytes: bytes, feed: dict) -> list[dict]:
    """同时兼容 RSS 2.0 与 Atom，命名空间一律忽略。"""
    root = ET.fromstring(xml_bytes)
    posts: list[dict] = []
    is_paper = bool(feed.get("paper"))

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
                # arXiv 有多条 link，优先取 alternate 的
                rel = (child.get("rel") or "alternate").lower()
                if href and (not link or rel == "alternate"):
                    link = href
                elif not link:
                    link = _text(child)
            elif name in ("description", "summary", "content"):
                if not summary:
                    summary = _text(child)
            elif name in ("pubdate", "published", "updated", "date"):
                if not published:
                    published = _text(child)

        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            continue

        post = {
            "title": title,
            "url": link.strip(),
            "summary": _strip_html(summary),
            "published": _parse_date(published),
            "published_raw": published,
            "source": feed["name"],
            "source_id": feed["id"],
            "home": feed["home"],
            "kind": "paper" if is_paper else "blog",
        }
        if is_paper:
            post["org"] = _tag_org(title)
            # arXiv 标题里常有换行和结尾标点
            post["title"] = re.sub(r"\s*\.$", "", post["title"])
        posts.append(post)

    return posts


_A_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.I | re.S)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)
_TAG_RE = re.compile(r"<[^>]+>")

def _nearby_title(segment: str) -> str:
    """锚点内没有文字时（Next.js 常把标题放在兄弟节点），从后面一段里找第一个像标题的文本块。"""
    segment = re.sub(r"<script.*?</script>", " ", segment, flags=re.S | re.I)
    segment = re.sub(r"<style.*?</style>", " ", segment, flags=re.S | re.I)
    # 截断可能落在标签中间，留一个完整标签边界，否则会捞到 class="..." 的碎片
    cut = segment.rfind(">")
    if cut != -1:
        segment = segment[: cut + 1]
    for chunk in re.split(r"<[^>]+>", segment):
        text = re.sub(r"\s+", " ", chunk).strip(" \u00b7\u2022|\u2013\u2014-")
        if not (6 <= len(text) <= 180):
            continue
        if re.fullmatch(r"[\d\s./:+-]+", text):
            continue
        if any(bad in text for bad in ("class=", "style=", "href=", "{\"", "};")):
            continue
        if text.lower() in _NAV_WORDS:
            continue
        return text
    return ""


_NAV_WORDS = {
    "announcements", "partnerships", "policy", "product", "research", "news",
    "blog", "more", "read more", "learn more", "view all", "see all",
    "所有文章", "查看更多", "更多", "全部", "首页", "博客",
}


def extract_html(html: str, feed: dict) -> list[dict]:
    """从官方列表页提取文章链接。

    不依赖具体站点的 class 名：只认「链接路径前缀 + 像标题的文本」，
    这样站点改版时不会立刻失效。
    """
    base = feed["url"]
    prefixes = tuple(feed.get("prefixes") or ())
    excludes = tuple(feed.get("exclude") or ())
    posts: list[dict] = []
    seen: set[str] = set()

    for match in _A_RE.finditer(html):
        attrs, inner = match.group(1), match.group(2)
        href_m = _HREF_RE.search(attrs)
        if not href_m:
            continue
        raw_href = href_m.group(1).strip()
        if not raw_href or raw_href.startswith(("#", "mailto:", "javascript:")):
            continue
        url = urllib.parse.urljoin(base, raw_href)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            continue
        path = parsed.path
        if prefixes and not any(path.startswith(p) or p in path for p in prefixes):
            continue
        # /news/ 这种栏目主页本身不是文章
        if path.rstrip("/") in {p.rstrip("/") for p in prefixes}:
            continue
        if any(e in path for e in excludes):
            continue
        if url in seen:
            continue

        title = re.sub(r"\s+", " ", _TAG_RE.sub(" ", inner)).strip()
        title = htmllib.unescape(title)
        title = re.sub(r"^[\d\s·•|/–—-]+", "", title).strip()
        # 卡片里常见「标题 + 日期」连在一起，把尾巴上的日期剪掉
        title = re.sub(
            r"\s*(?:20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}"
            r"|20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
            r"|[A-Z][a-z]{2}\s+\d{1,2},?\s+20\d{2})\s*$",
            "", title,
        ).strip()
        if not (6 <= len(title) <= 180):
            title = htmllib.unescape(_nearby_title(html[match.end(): match.end() + 700]))
        if not (6 <= len(title) <= 180):
            continue
        if title.lower() in _NAV_WORDS:
            continue
        # 标题里不该有太多标点，通常是日期或标签串
        if title.count("·") > 2 or title.count("|") > 1:
            continue

        # 日期可能在锚点内部（标题和日期连在一起），也可能紧跟在卡片后面
        window = html[match.start(): match.end() + 600]
        published = find_date(window)

        seen.add(url)
        posts.append(
            {
                "title": title,
                "url": url,
                "summary": "",
                "published": published,
                "published_raw": "",
                "source": feed["name"],
                "source_id": feed["id"],
                "home": feed["home"],
                "kind": "paper" if feed.get("paper") else "blog",
            }
        )

    # 同一篇文章常有中英两个地址，按最后一段 slug 只留一条
    if feed.get("dedupe_slug"):
        seen_slug = set()
        deduped = []
        for post in posts:
            slug = urllib.parse.urlparse(post["url"]).path.rstrip("/").rsplit("/", 1)[-1]
            if slug in seen_slug:
                continue
            seen_slug.add(slug)
            deduped.append(post)
        posts = deduped

    # 列表页往往只渲染首屏，按页面顺序即可；有日期的排前面
    if any(p["published"] for p in posts):
        posts.sort(key=lambda p: p["published"], reverse=True)
    return posts


# ---------------------------------------------------------------------------
# 抓取
# ---------------------------------------------------------------------------
def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


_CTX = _ssl_ctx()


def _get(url: str, timeout: int, *, html: bool, tries: int = 2) -> bytes:
    headers = {
        "User-Agent": UA_HTML if html else UA_FEED,
        "Accept": "text/html,application/xhtml+xml,*/*" if html else _ACCEPT_FEED,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    last: Exception | None = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
                return resp.read(5 * 1024 * 1024)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt + 1 < tries:
                time.sleep(0.6 + attempt * 0.5)
    raise last  # type: ignore[misc]


_META_PATTERNS = (
    r'<meta[^>]+(?:property|name)=["\'](?:og:description|twitter:description|description)["\'][^>]*content=["\']([^"\']*)["\']',
    r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\'](?:og:description|twitter:description|description)["\']',
)


def _meta_desc(html: str) -> str:
    for pat in _META_PATTERNS:
        m = re.search(pat, html, re.I)
        if m and len(m.group(1)) >= 30:
            return _strip_html(htmllib.unescape(m.group(1)))
    return ""


_NAV_HINTS = (
    "skip to main content", "skip to content", "all rights reserved", "sign in",
    "subscribe", "newsletter", "cookie", "we use cookies", "privacy policy",
)


def _first_para(html: str) -> str:
    body = re.search(r"<(?:article|main)[^>]*>(.*?)</(?:article|main)>", html, re.S | re.I)
    scope = body.group(1) if body else html
    for chunk in re.findall(r"<p[^>]*>(.*?)</p>", scope, re.S | re.I):
        text = _strip_html(chunk)
        if len(text) < 60:
            continue
        low = text.lower()
        if any(hint in low for hint in _NAV_HINTS):
            continue
        return text
    return ""


def _run_tasks(tasks, fn, workers: int, budget: float) -> None:
    """同样的硬预算，用于补全文章摘要这类次要请求。"""
    import queue
    import threading

    if not tasks:
        return
    q: queue.Queue = queue.Queue()
    slots = threading.BoundedSemaphore(workers)

    def worker(idx, item):
        slots.acquire()
        try:
            fn(item)
        except Exception:  # noqa: BLE001
            pass
        finally:
            slots.release()
        q.put(idx)

    for idx, item in enumerate(tasks):
        threading.Thread(target=worker, args=(idx, item), daemon=True).start()

    done = 0
    deadline = time.time() + budget
    while done < len(tasks):
        left = deadline - time.time()
        if left <= 0:
            break
        try:
            q.get(timeout=left)
        except queue.Empty:
            break
        done += 1


def _enrich(posts: list[dict], site_descs: dict[str, str], budget: float = 6.0) -> None:
    """官方列表页往往不给摘要，补一轮详情页描述，最多每源 6 篇。"""
    picked: list[dict] = []
    per_source: dict[str, int] = {}
    for post in posts:
        if post.get("summary") or post.get("kind") != "blog":
            continue
        sid = post.get("source_id", "")
        if per_source.get(sid, 0) >= 4:
            continue
        per_source[sid] = per_source.get(sid, 0) + 1
        picked.append(post)

    def fill(post: dict) -> None:
        html = _get(post["url"], 5, html=True, tries=1).decode("utf-8", "replace")
        desc = _meta_desc(html)
        # 站点通用描述（每篇文章都一样）不能当摘要
        if not desc or desc == site_descs.get(post.get("source_id", "")):
            desc = _first_para(html)
        if desc:
            post["summary"] = desc

    _run_tasks(picked, fill, workers=8, budget=budget)


def _failed(feed: dict, error: str, started: float) -> dict:
    return {
        "id": feed["id"],
        "name": feed["name"],
        "home": feed["home"],
        "kind": feed.get("kind", "rss"),
        "paper": bool(feed.get("paper")),
        "optional": bool(feed.get("optional")),
        "order": int(feed.get("order", 99)),
        "site_desc": "",
        "ok": False,
        "posts": [],
        "count": 0,
        "ms": int((time.time() - started) * 1000),
        "error": error[:160],
    }


def _run_batch(
    feeds: list[tuple[int, dict]], workers: int, budget: float
) -> dict[int, dict]:
    """带硬预算的抓取。

    线程池的 shutdown 会等所有任务跑完，慢源能把整次刷新拖到 40 秒以上。
    这里用守护线程 + 队列 + 总预算：到点就收工，没回来的源标记跳过，
    后台线程自己结束，不会阻塞程序退出。
    """
    import queue
    import threading

    if not feeds:
        return {}

    q: queue.Queue = queue.Queue()
    slots = threading.BoundedSemaphore(workers)

    def worker(idx: int, feed: dict) -> None:
        started = time.time()
        slots.acquire()
        try:
            res = _fetch_one(feed)
        except Exception as exc:  # noqa: BLE001
            res = _failed(feed, f"{type(exc).__name__}: {exc}", started)
        finally:
            slots.release()
        q.put((idx, res))

    for idx, feed in feeds:
        threading.Thread(target=worker, args=(idx, feed), daemon=True).start()

    out: dict[int, dict] = {}
    deadline = time.time() + budget
    while len(out) < len(feeds):
        left = deadline - time.time()
        if left <= 0:
            break
        try:
            idx, res = q.get(timeout=left)
        except queue.Empty:
            break
        out[idx] = res

    for idx, feed in feeds:
        if idx not in out:
            out[idx] = _failed(feed, "本次超出等待预算，已跳过", time.time() - budget)
    return out


def _fetch_one(feed: dict) -> dict:
    started = time.time()
    timeout = int(feed.get("timeout") or (OPTIONAL_TIMEOUT if feed.get("optional") else TIMEOUT))
    tries = 1
    try:
        site_desc = ""
        if feed["kind"] == "html":
            raw = _get(feed["url"], timeout, html=True, tries=tries)
            html = raw.decode("utf-8", "replace")
            posts = extract_html(html, feed)
            site_desc = _meta_desc(html)
        else:
            raw = _get(feed["url"], timeout, html=False, tries=tries)
            posts = parse_feed(raw, feed)
        return {
            "id": feed["id"],
            "name": feed["name"],
            "home": feed["home"],
            "kind": feed.get("kind", "rss"),
            "paper": bool(feed.get("paper")),
            "optional": bool(feed.get("optional")),
            "order": int(feed.get("order", 99)),
            "site_desc": site_desc,
            "ok": True,
            "posts": posts,
            "count": len(posts),
            "ms": int((time.time() - started) * 1000),
            "error": "",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "id": feed["id"],
            "name": feed["name"],
            "home": feed["home"],
            "kind": feed.get("kind", "rss"),
            "paper": bool(feed.get("paper")),
            "optional": bool(feed.get("optional")),
            "order": int(feed.get("order", 99)),
            "site_desc": "",
            "ok": False,
            "posts": [],
            "count": 0,
            "ms": int((time.time() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}"[:160],
        }


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
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
            raise ValueError("not a dict")
        data.setdefault("fetched_at", 0)
        data.setdefault("feeds", [])
        data.setdefault("posts", [])
        # 老缓存没有 kind / org 字段，补上默认值，避免前端拿到 undefined
        for post in data["posts"]:
            post.setdefault("kind", "blog")
            post.setdefault("org", "")
        _cache = data
    except Exception:
        _cache = {"fetched_at": 0, "feeds": [], "posts": [], "error": "缓存文件损坏，已忽略"}
    return _cache


def _save(data: dict) -> None:
    global _cache
    _cache = data
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _merge(cached: dict, fresh: list[dict]) -> list[dict]:
    """新内容并进旧内容，按 URL 去重，谁也不丢。"""
    merged: dict[str, dict] = {}
    for post in cached.get("posts", []):
        url = (post.get("url") or "").strip() or post.get("title", "")
        if url:
            merged[url] = post

    for post in fresh:
        url = (post.get("url") or "").strip() or post.get("title", "")
        if not url:
            continue
        old = merged.get(url)
        if old:
            # 新结果缺摘要/日期时，沿用旧值
            if not post.get("summary"):
                post["summary"] = old.get("summary", "")
            if not post.get("published"):
                post["published"] = old.get("published", 0)
            if not post.get("org"):
                post["org"] = old.get("org", "")
        merged[url] = post

    def rank(post: dict) -> tuple:
        ts = float(post.get("published") or 0)
        # 先按天分组，同一天内官方博客排在论文前面，
        # 否则首屏会被 arXiv 的日更论文淹没，看不到厂商动向
        day = int(ts // 86400) if ts else 0
        return (day, 1 if post.get("kind") == "blog" else 0, ts, post.get("title", ""))

    posts = sorted(merged.values(), key=rank, reverse=True)
    return posts[:MAX_POSTS]


def _stats(posts: list[dict], feeds: list[dict]) -> dict:
    papers = sum(1 for p in posts if p.get("kind") == "paper")
    blogs = len(posts) - papers
    orgs: dict[str, int] = {}
    for post in posts:
        org = post.get("org") or ""
        if org and post.get("kind") == "paper":
            orgs[org] = orgs.get(org, 0) + 1
    return {
        "total": len(posts),
        "blogs": blogs,
        "papers": papers,
        "sources": len(feeds),
        "sources_ok": sum(1 for f in feeds if f.get("ok")),
        "orgs": orgs,
    }


def refresh(force: bool = False) -> dict:
    """抓取所有源；非强制且缓存新鲜时直接返回缓存。"""
    cached = load_cache()
    age = time.time() - float(cached.get("fetched_at") or 0)
    if not force and cached.get("posts") and age < TTL_SEC:
        data = dict(cached)
        data["from_cache"] = True
        data["age"] = int(age)
        return data

    required = [(i, f) for i, f in enumerate(FEEDS) if not f.get("optional")]
    optional = [(i, f) for i, f in enumerate(FEEDS) if f.get("optional")]

    # 主源优先，给足 13 秒；海外源单独再给 5 秒，失败只标离线
    picked = _run_batch(required, workers=8, budget=13)
    picked.update(_run_batch(optional, workers=4, budget=5))
    results = [picked[i] for i in range(len(FEEDS))]

    # 必需源失败多半是瞬时抖动：用更短的超时补一轮
    retry = [(i, dict(FEEDS[i], timeout=5)) for i, res in enumerate(results)
             if not res["ok"] and not FEEDS[i].get("optional")]
    if retry:
        again = _run_batch(retry, workers=4, budget=6)
        for i, res in again.items():
            if res["ok"]:
                results[i] = res

    fresh: list[dict] = []
    for res in results:
        # 单个源可能一次吐出上千条（OpenAI 的 feed 就是），限量取最新
        fresh.extend(
            sorted(res["posts"], key=lambda p: p["published"], reverse=True)[:PER_FEED]
        )

    site_descs = {res["id"]: res.get("site_desc", "") for res in results if res.get("site_desc")}
    _enrich(fresh, site_descs, budget=6.0)

    prev_feeds = {f.get("id"): f for f in cached.get("feeds", [])}
    feeds_out = []
    for res in results:
        old = prev_feeds.get(res["id"], {})
        if not res["ok"]:
            res["last_ok"] = old.get("last_ok") or old.get("fetched_at") or 0
            res["kept"] = old.get("count", 0)
        else:
            res["last_ok"] = time.time()
            res["kept"] = res["count"]
        feeds_out.append({k: res[k] for k in (
            "id", "name", "home", "kind", "paper", "optional", "order",
            "ok", "count", "ms", "error", "last_ok", "kept",
        )})

    feeds_out.sort(key=lambda f: (f["order"], not f["ok"]))

    posts = _merge(cached, fresh)
    data = {
        "fetched_at": time.time(),
        "feeds": feeds_out,
        "posts": posts,
        "stats": _stats(posts, feeds_out),
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
        "stats": data.get("stats") or _stats(data.get("posts", []), data.get("feeds", [])),
        "ttl": TTL_SEC,
    }
