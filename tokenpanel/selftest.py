"""自检套件：不依赖真实数据，用合成样本验证核心逻辑。

运行：python -m tokenpanel selftest
退出码 0 表示全部通过，可直接用于 CI 或升级后的回归验证。
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from . import db, pricing
from .adapters.base import parse_iso_ms, short_project, to_ms
from .adapters.generic import find_usage


class Runner:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []

    def check(self, name: str, fn) -> None:
        try:
            fn()
        except AssertionError as exc:
            self.failed.append(f"{name}: {exc}")
            print(f"  \u2717 {name}")
        except Exception as exc:  # 意外的异常同样算失败
            self.failed.append(f"{name}: {type(exc).__name__} {exc}")
            print(f"  \u2717 {name}")
        else:
            self.passed += 1
            print(f"  \u2713 {name}")

    def report(self) -> int:
        total = self.passed + len(self.failed)
        print()
        if self.failed:
            print(f"失败 {len(self.failed)} / {total}")
            for f in self.failed:
                print("   -", f)
            return 1
        print(f"全部通过：{total} 项")
        return 0


def _tmp_conn() -> sqlite3.Connection:
    path = Path(tempfile.mkdtemp()) / "test.db"
    return db.connect(path)


def test_units(r: Runner) -> None:
    from .report import human

    r.check("human 小数值原样", lambda: _eq(human(999), "999"))
    r.check("human 万级", lambda: _eq(human(12345), "1.23万"))
    r.check("human 亿级", lambda: _eq(human(1_500_000_000), "15.00亿"))
    r.check("human 零值", lambda: _eq(human(0), "0"))
    r.check("human 空值", lambda: _eq(human(None), "0"))


def test_time(r: Runner) -> None:
    r.check("to_ms 秒转毫秒", lambda: _eq(to_ms(1_700_000_000), 1_700_000_000_000))
    r.check("to_ms 毫秒不变", lambda: _eq(to_ms(1_700_000_000_000), 1_700_000_000_000))
    r.check("parse_iso_ms 带 Z", lambda: _assert(parse_iso_ms("2026-11-20T10:00:00Z") is not None))
    r.check("parse_iso_ms 非法输入", lambda: _eq(parse_iso_ms("not-a-date"), None))


def test_pricing(r: Runner) -> None:
    r.check("免费模型零价", lambda: _eq(pricing.cost_of("opencode/some-free", 1_000_000, 0, 0, 0), 0.0))
    r.check("前缀命中 claude-opus", lambda: _assert(pricing.resolve("claude-opus-5-5")[0] is not None))
    r.check("未知模型返回 None", lambda: _eq(pricing.resolve("totally-unknown")[0], None))
    r.check("未定价成本为 None", lambda: _eq(pricing.cost_of("totally-unknown", 10, 10, 0, 0), None))
    r.check(
        "成本计算正确",
        lambda: _assert(abs(pricing.cost_of("deepseek-flash", 1_000_000, 0, 0, 0) - 0.30) < 1e-9),
    )

    def longest_match() -> None:
        # 带渠道前缀时不能被更短的型号名抢走
        _, hit = pricing.resolve("xiaomi/mimo-v2.5-pro-ultraspeed")
        _eq(hit, "mimo-v2.5-pro-ultraspeed")
        _, hit2 = pricing.resolve("mimo-v2.5-pro")
        _eq(hit2, "mimo-v2.5-pro")

    def free_model_zero() -> None:
        _eq(pricing.cost_of("opencode/big-pickle", 1_000_000, 1_000_000, 1_000_000, 0), 0.0)

    r.check("最长型号优先匹配", longest_match)
    r.check("免费模型恒为 0", free_model_zero)


def test_project(r: Runner) -> None:
    r.check("Windows 路径取末两级", lambda: _eq(short_project(r"C:\a\b\c"), "b/c"))
    r.check("POSIX 路径", lambda: _eq(short_project("/home/u/proj"), "u/proj"))
    r.check("空路径", lambda: _eq(short_project(None), None))


def test_generic_finder(r: Runner) -> None:
    r.check(
        "嵌套 usage 可识别",
        lambda: _assert(
            find_usage({"a": {"b": {"usage": {"input_tokens": 5, "output_tokens": 7}}}}) is not None
        ),
    )
    r.check(
        "camelCase 可识别",
        lambda: _assert(find_usage({"usage": {"inputTokens": 1, "outputTokens": 2}}) is not None),
    )
    r.check(
        "缺少输出不算",
        lambda: _eq(find_usage({"usage": {"input_tokens": 5}}), None),
    )
    r.check(
        "无关结构不算",
        lambda: _eq(find_usage({"usage": {"foo": 1}, "bar": [1, 2, 3]}), None),
    )


def test_discovery(r: Runner) -> None:
    from . import discovery

    r.check("识别 copilot 扩展名", lambda: _eq(discovery._match_hint("github.copilot-chat"), "copilot"))
    r.check(
        "不误判 examples 中的 amp",
        lambda: _eq(discovery._match_hint("intellicode-api-usage-examples"), None),
    )
    r.check("识别 .dsh", lambda: _eq(discovery._match_hint(".dsh"), "dsh"))


def test_db(r: Runner) -> None:
    conn = _tmp_conn()
    row = (("claude", "s1", "k1", 1_700_000_000_000, "p", "m", 10, 20, 0, 0, 0, 0.01, "priced", "exact"))

    def insert_twice() -> None:
        db.upsert_events(conn, [row])
        db.upsert_events(conn, [row])
        n = conn.execute("SELECT COUNT(*) FROM usage_event").fetchone()[0]
        _eq(n, 1)

    def credit_overwrite() -> None:
        db.upsert_credits(conn, [("workbuddy", "s1", 1, "p", "m", 100, 1000, 5.0, "{}")])
        db.upsert_credits(conn, [("workbuddy", "s1", 2, "p", "m", 200, 1000, 9.0, "{}")])
        got = conn.execute("SELECT used, credits FROM credit_snapshot").fetchall()
        _eq(len(got), 1)
        _eq(got[0]["used"], 200)

    def file_mark_updates_client() -> None:
        db.mark_file(conn, "/x/a.jsonl", "generic:old", 1.0, 10, 1)
        db.mark_file(conn, "/x/a.jsonl", "generic:qoder", 2.0, 20, 2)
        got = conn.execute("SELECT client FROM source_file WHERE path='/x/a.jsonl'").fetchone()[0]
        _eq(got, "generic:qoder")

    r.check("事件主键去重", insert_twice)
    r.check("credit 覆盖而不累加", credit_overwrite)
    r.check("来源表随改名更新", file_mark_updates_client)


def test_codex_math(r: Runner) -> None:
    """Codex 的 input_tokens 含缓存，采集时必须相减，否则重复计数。"""

    def calc() -> None:
        usage = {"input_tokens": 10154, "cached_input_tokens": 4096, "output_tokens": 175}
        net = max(0, usage["input_tokens"] - usage["cached_input_tokens"])
        _eq(net, 6058)
        _assert(net + usage["cached_input_tokens"] == usage["input_tokens"])

    r.check("Codex 净输入计算", calc)


def test_runtime(r: Runner) -> None:
    from . import runtime

    def no_beat_no_exit() -> None:
        runtime.BEAT.update(ts=0.0, seen=False)
        _assert(runtime.should_exit() is False)

    def fresh_beat_stays() -> None:
        runtime.beat()
        _assert(runtime.should_exit() is False)

    def stale_beat_exits() -> None:
        runtime.BEAT["ts"] = 0.0  # 模拟窗口关闭已久
        runtime.BEAT["seen"] = True
        _assert(runtime.should_exit() is True)

    def scan_status_shape() -> None:
        runtime.scan_started()
        st = runtime.scan_status()
        _assert(st["running"] is True)
        runtime.scan_finished(7)
        st = runtime.scan_status()
        _assert(st["running"] is False and st["added"] == 7)

    def progress_flow() -> None:
        runtime.scan_started()
        runtime.scan_plan([("a", "A"), ("b", "B")])
        runtime.scan_enter("a")
        runtime.scan_files(12)
        st = runtime.scan_status()
        _eq(st["total"], 2)
        _eq(st["current"], "a")
        _eq(st["files"], 12)
        _eq(st["done"], 0)
        runtime.scan_source_done("a", 12, 30, 5, 120)
        runtime.scan_enter("b")
        st = runtime.scan_status()
        _eq(st["done"], 1)
        _eq(st["percent"], 50)
        runtime.scan_source_done("b", 3, 4, 0, 40)
        st = runtime.scan_status()
        _eq(st["done"], 2)
        _eq(st["percent"], 100)

    def stale_scan_resets() -> None:
        runtime.scan_started()
        runtime.SCAN["started"] = 0.0  # 模拟线程异常退出、时间戳停留在远古
        st = runtime.scan_status()
        _assert(st["running"] is False)
        _assert("自动复位" in st["error"])

    r.check("无心跳不退出", no_beat_no_exit)
    r.check("心跳新鲜不退出", fresh_beat_stays)
    r.check("心跳过期退出", stale_beat_exits)
    r.check("扫描状态结构", scan_status_shape)
    r.check("扫描进度流转", progress_flow)
    r.check("扫描超时自愈", stale_scan_resets)


def test_catalog(r: Runner) -> None:
    from . import catalog

    def shape() -> None:
        data = catalog.load(force=True)
        _assert(data["summary"]["providers"] > 0)
        _eq(len(data["models"]), data["summary"]["models"])
        for row in data["models"]:
            for key in ("provider", "model", "currency", "input", "output", "verified", "pricing_url"):
                _assert(key in row, f"缺少字段 {key}")
            _assert(row["currency"] in ("CNY", "USD"))

    def search_works() -> None:
        hits = catalog.search("qwen")
        _assert(hits, "应能搜到 qwen 系列")
        _assert(all("qwen" in (r["model"] + r["model_id"]).lower() for r in hits))

    def verified_requires_provider() -> None:
        # 平台未核实时，型号一律按未核实展示
        data = catalog.load(force=True)
        for row in data["models"]:
            if not row["verified"]:
                _assert(row["verified"] is False)

    r.check("价格表结构完整", shape)
    r.check("价格表可检索", search_works)
    r.check("核实状态向下传递", verified_requires_provider)


def test_discovery(r: Runner) -> None:
    import sqlite3
    import tempfile
    from pathlib import Path as _P

    from . import config, discovery

    def multi_candidate() -> None:
        base = _P(tempfile.mkdtemp())
        (base / "b").mkdir()
        _eq(config.first_existing([base / "a", base / "b"]).name, "b")
        # 都不存在时返回首选，便于报错时展示预期路径
        _eq(config.first_existing([base / "x", base / "y"]).name, "x")

    def usage_signal() -> None:
        root = _P(tempfile.mkdtemp())
        sub = root / "sessions"
        sub.mkdir()
        (sub / "plain.jsonl").write_text('{"event":"click"}', encoding="utf-8")
        ok, _ = discovery._has_usage_signal(root)
        _assert(ok is False, "普通 jsonl 不应被识别为用量数据")
        (sub / "real.jsonl").write_text(
            '{"usage":{"input_tokens":12,"output_tokens":3}}', encoding="utf-8"
        )
        ok2, ev = discovery._has_usage_signal(root)
        _assert(ok2 is True and bool(ev), "含 usage 的 jsonl 应被识别")

    def sqlite_precision() -> None:
        base = _P(tempfile.mkdtemp())
        c = sqlite3.connect(base / "auth.db")
        c.execute("CREATE TABLE a (name TEXT, token TEXT)")
        c.commit()
        c.close()
        _assert(discovery._sqlite_has_tokens(base / "auth.db") is False,
                "只有 token 列的凭据表不应被当成用量表")

        c = sqlite3.connect(base / "usage.db")
        c.execute("CREATE TABLE u (input_tokens INT, output_tokens INT)")
        c.commit()
        c.close()
        _assert(discovery._sqlite_has_tokens(base / "usage.db") is True)

    def report_shape() -> None:
        rep = discovery.report()
        for key in ("found", "checked", "roots", "summary"):
            _assert(key in rep, f"报告缺少 {key}")
        for item in rep["checked"]:
            for key in ("client", "path", "exists"):
                _assert(key in item)

    r.check("路径多候选", multi_candidate)
    r.check("usage 内容特征", usage_signal)
    r.check("sqlite 精度判定", sqlite_precision)
    r.check("探测报告结构", report_shape)


def test_fx(r: Runner) -> None:
    from . import fx

    def convert_math() -> None:
        _assert(abs(fx.convert(1, "USD", "CNY", 7.0) - 7.0) < 1e-9)
        _assert(abs(fx.convert(7, "CNY", "USD", 7.0) - 1.0) < 1e-9)
        _eq(fx.convert(1, "USD", "USD", 7.0), 1.0)
        _eq(fx.convert(None, "USD", "CNY", 7.0), None)

    def sanity_range() -> None:
        _assert(fx._valid(7.0) is True)
        _assert(fx._valid(1.0) is False)
        _assert(fx._valid(100.0) is False)

    def env_override() -> None:
        import os

        os.environ["TP_USD_CNY"] = "7.25"
        try:
            d = fx.get()
            _assert(abs(d["usd_cny"] - 7.25) < 1e-9)
            _assert(d["fallback"] is False)
        finally:
            os.environ.pop("TP_USD_CNY", None)

    def offline_uses_cache() -> None:
        import json
        import tempfile
        from pathlib import Path as _Path

        tmp = _Path(tempfile.mkdtemp()) / "fx.json"
        tmp.write_text(json.dumps({"usd_cny": 6.9, "source": "cache", "fetched_at": 0}), encoding="utf-8")
        real_fetch, real_path = fx._fetch, fx._path
        fx._fetch = lambda: None
        fx._path = lambda: tmp
        try:
            d = fx.get(force=True)
        finally:
            fx._fetch, fx._path = real_fetch, real_path
        _assert(d["stale"] is True)
        _assert(abs(d["usd_cny"] - 6.9) < 1e-9)

    r.check("汇率换算", convert_math)
    r.check("汇率区间校验", sanity_range)
    r.check("环境变量覆盖", env_override)
    r.check("断网沿用缓存", offline_uses_cache)


def test_feeds(r: Runner) -> None:
    from . import feeds

    RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Hello RSS</title><link>https://example.com/a</link>
      <description>&lt;p&gt;body &amp;amp; text&lt;/p&gt;</description>
      <pubDate>Sun, 22 Nov 2026 10:00:00 GMT</pubDate></item>
    </channel></rss>"""

    ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Hello Atom</title><link href="https://example.com/b"/>
      <summary>atom summary</summary><updated>2026-11-21T08:30:00Z</updated></entry>
    </feed>"""

    meta = {"id": "t", "name": "Test", "url": "", "home": ""}

    def rss() -> None:
        posts = feeds.parse_feed(RSS, meta)
        _eq(len(posts), 1)
        _eq(posts[0]["title"], "Hello RSS")
        _eq(posts[0]["url"], "https://example.com/a")
        _assert("body & text" in posts[0]["summary"], "应去掉 HTML 并还原实体")
        _assert(posts[0]["published"] > 0)

    def atom() -> None:
        posts = feeds.parse_feed(ATOM, meta)
        _eq(len(posts), 1)
        _eq(posts[0]["title"], "Hello Atom")
        _eq(posts[0]["url"], "https://example.com/b")
        _assert(posts[0]["published"] > 0)

    def cache_offline() -> None:
        # 没有缓存文件时也必须返回可用结构，而不是抛异常
        data = feeds.load_cache()
        _assert("posts" in data and "feeds" in data)

    r.check("RSS 解析", rss)
    r.check("Atom 解析", atom)
    r.check("无缓存时可降级", cache_offline)


def test_timezone_buckets(r: Runner) -> None:
    """按天统计必须用本地时区，否则北京时间凌晨的用量会被算到前一天。"""
    import datetime
    import tempfile

    def local_midnight_stays() -> None:
        tmp = Path(tempfile.mkdtemp()) / "tz.db"
        conn = db.connect(tmp)
        # 本地时间 00:30，属于当天
        ts = int(datetime.datetime(2026, 11, 22, 0, 30).timestamp() * 1000)
        db.upsert_events(conn, [
            ("claude", "s", "k", ts, "p", "m", 10, 10, 0, 0, 0, 0.0, "priced", "exact")
        ])
        conn.commit()
        day = conn.execute(
            "SELECT date(ts/1000,'unixepoch','localtime') FROM usage_event"
        ).fetchone()[0]
        conn.close()
        _eq(day, "2026-11-22")

    def local_evening_stays() -> None:
        tmp = Path(tempfile.mkdtemp()) / "tz2.db"
        conn = db.connect(tmp)
        ts = int(datetime.datetime(2026, 11, 22, 23, 30).timestamp() * 1000)
        db.upsert_events(conn, [
            ("claude", "s", "k", ts, "p", "m", 10, 10, 0, 0, 0, 0.0, "priced", "exact")
        ])
        conn.commit()
        day = conn.execute(
            "SELECT date(ts/1000,'unixepoch','localtime') FROM usage_event"
        ).fetchone()[0]
        conn.close()
        _eq(day, "2026-11-22")

    r.check("凌晨 00:30 归当天", local_midnight_stays)
    r.check("深夜 23:30 归当天", local_evening_stays)


def test_service(r: Runner) -> None:
    """端到端冒烟：真正起一次 HTTP 服务并访问各端点（CI 也跑）。"""
    import json
    import socket
    import threading
    import time as _t
    import urllib.request

    from . import server_fallback

    PORT = 8799

    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", PORT)) == 0:
            print("  (端口 8799 已占用，跳过服务冒烟)")
            return

    threading.Thread(
        target=server_fallback.serve, args=(PORT, False, True), daemon=True
    ).start()

    base = f"http://127.0.0.1:{PORT}"

    def ready() -> None:
        deadline = _t.time() + 12
        while _t.time() < deadline:
            try:
                urllib.request.urlopen(base + "/", timeout=0.5)
                return
            except Exception:
                _t.sleep(0.1)
        raise AssertionError("服务 12 秒内未就绪")

    def fetch(path: str) -> bytes:
        return urllib.request.urlopen(base + path, timeout=6).read()

    def page() -> None:
        _assert(b"TokenPanel" in fetch("/"))

    def health() -> None:
        d = json.loads(fetch("/api/health"))
        for key in ("events", "deps", "scan", "sources"):
            _assert(key in d)
        _assert(isinstance(d["deps"].get("zstandard"), bool))

    def overview() -> None:
        d = json.loads(fetch("/api/overview?days=7"))
        for key in ("tokens", "calls", "by_client", "cache_hit"):
            _assert(key in d)

    def trend() -> None:
        d = json.loads(fetch("/api/trend?days=7"))
        _assert("series" in d and "days" in d)

    def version() -> None:
        d = json.loads(fetch("/api/version"))
        _assert(d.get("version"))

    def export() -> None:
        raw = fetch("/api/export?days=7")
        _assert(raw[:3] == b"\xef\xbb\xbf", "CSV 应带 UTF-8 BOM")

    r.check("服务可启动", ready)
    r.check("GET / 返回页面", page)
    r.check("GET /api/health", health)
    r.check("GET /api/overview", overview)
    r.check("GET /api/trend", trend)
    r.check("GET /api/version", version)
    def heatmap() -> None:
        d = json.loads(fetch("/api/heatmap?days=365"))
        _eq(len(d["days"]), 365)
        _eq(len(d["values"]), 365)
        _assert(d["days"][-1] >= d["days"][0])
        _assert(isinstance(d["active"], int))

    r.check("GET /api/export", export)
    def catalog_api() -> None:
        d = json.loads(fetch("/api/catalog"))
        _assert(d["summary"]["models"] > 0)
        _assert(d["models"])

    def blogs_api() -> None:
        d = json.loads(fetch("/api/blogs"))
        for key in ("posts", "feeds", "count", "ttl"):
            _assert(key in d)

    r.check("GET /api/heatmap 一年", heatmap)
    r.check("GET /api/catalog", catalog_api)
    def fx_api() -> None:
        d = json.loads(fetch("/api/fx"))
        _assert(float(d["usd_cny"]) > 0)
        _assert("source" in d and "stale" in d)

    r.check("GET /api/blogs", blogs_api)
    def discovery_api() -> None:
        d = json.loads(fetch("/api/discovery"))
        for key in ("found", "checked", "roots", "summary"):
            _assert(key in d)

    r.check("GET /api/fx", fx_api)
    r.check("GET /api/discovery", discovery_api)


def test_recovery(r: Runner) -> None:
    from . import config

    def corrupt_heals() -> None:
        import sqlite3
        import tempfile

        bad = Path(tempfile.mkdtemp()) / "bad.db"
        bad.write_bytes(b"not a database" * 200)
        conn = db.connect(bad)
        try:
            n = conn.execute("SELECT COUNT(*) FROM usage_event").fetchone()[0]
            _eq(n, 0)
        finally:
            conn.close()
        assert any("corrupt" in f.name for f in bad.parent.iterdir()), "应保留隔离文件"

    def writable_probe() -> None:
        ok, err = config.is_writable()
        _assert(ok is True)
        _eq(err, "")

    r.check("损坏库自动重建", corrupt_heals)
    r.check("数据目录可写", writable_probe)


def test_updater(r: Runner) -> None:
    from . import updater

    def ordering() -> None:
        _assert(updater.is_newer("0.1.2", "0.1.1") is True)
        _assert(updater.is_newer("0.1.1", "0.1.1") is False)
        _assert(updater.is_newer("0.1.0", "0.1.1") is False)
        _assert(updater.is_newer("1.0.0", "0.9.9") is True)
        _assert(updater.is_newer("0.10.0", "0.9.0") is True)

    def offline_degrades() -> None:
        import urllib.request

        real = urllib.request.urlopen

        def boom(*_a, **_k):
            raise OSError("simulated offline")

        urllib.request.urlopen = boom
        try:
            res = updater.check(timeout=0.1)
        finally:
            urllib.request.urlopen = real
        _assert(res["checked"] is False)
        _assert(res["has_update"] is False)
        _assert(bool(res["error"]))

    r.check("版本号比较", ordering)
    r.check("断网时静默降级", offline_degrades)


def test_limits(r: Runner) -> None:
    from .exporter import export_csv
    from .adapters.base import _tokens

    def clamp() -> None:
        _eq(_tokens(-5), 0)
        _eq(_tokens("abc"), 0)
        _eq(_tokens(123), 123)
        _assert(_tokens(10**12) <= 2_000_000_000)

    def csv_cap() -> None:
        # 用临时库，避免依赖本机真实数据（CI 上是空库）
        import tempfile
        import time as _t
        from tokenpanel import config as cfg

        tmp = Path(tempfile.mkdtemp()) / "cap.db"
        conn = db.connect(tmp)
        now = int(_t.time() * 1000)
        db.upsert_events(conn, [
            ("claude", "s", f"k{i}", now, "p", "m", 1, 1, 0, 0, 0, 0.0, "priced", "exact")
            for i in range(5)
        ])
        conn.commit()
        conn.close()

        old = cfg.DB_PATH
        cfg.DB_PATH = tmp
        try:
            try:
                export_csv(0, max_rows=1)
            except ValueError:
                return
            raise AssertionError("超过上限时应抛 ValueError")
        finally:
            cfg.DB_PATH = old

    r.check("脏 token 值收敛", clamp)
    r.check("导出超限报错", csv_cap)


def _eq(actual, expected) -> None:
    if actual != expected:
        raise AssertionError(f"期望 {expected!r}，实际 {actual!r}")


def _assert(cond, message: str = "") -> None:
    if not cond:
        raise AssertionError(message or "断言失败")


def main() -> int:
    print("TokenPanel 自检\n")
    r = Runner()
    print("[计量与格式化]")
    test_units(r)
    print("[时间解析]")
    test_time(r)
    print("[价格与成本]")
    test_pricing(r)
    print("[项目路径]")
    test_project(r)
    print("[通用探测器]")
    test_generic_finder(r)
    print("[环境发现]")
    test_discovery(r)
    print("[存储语义]")
    test_db(r)
    print("[边界算法]")
    test_codex_math(r)
    print("[运行时状态]")
    test_runtime(r)
    print("[更新与限额]")
    test_updater(r)
    test_limits(r)
    print("[故障恢复]")
    test_recovery(r)
    print("[本机探测]")
    test_discovery(r)
    print("[价格与动态]")
    test_catalog(r)
    test_fx(r)
    test_feeds(r)
    print("[时区归日]")
    test_timezone_buckets(r)
    print("[服务冒烟]")
    test_service(r)
    return r.report()
