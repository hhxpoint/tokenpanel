"""标准库服务端：核心接口必须始终返回结构化 JSON，且不因缺数据而 500。"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from io import BytesIO

import pytest

from tokenpanel import db, feeds, server_fallback


@pytest.fixture
def server(app_home):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_fallback.Handler)
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(url: str):
    with urllib.request.urlopen(url, timeout=15) as resp:
        return resp.status, resp.read(), resp.headers.get("Content-Type", "")


def test_index_served(server):
    code, body, ctype = _get(server + "/")
    assert code == 200
    assert "text/html" in ctype
    assert "TokenPanel" in body.decode("utf-8")


def test_health_and_version(server):
    code, body, _ = _get(server + "/api/health")
    assert code == 200
    health = json.loads(body)
    assert "events" in health and "scan" in health

    code, body, _ = _get(server + "/api/version")
    assert code == 200
    assert json.loads(body)["version"]


def test_overview_empty_db_is_valid(server):
    code, body, _ = _get(server + "/api/overview?days=7")
    assert code == 200
    data = json.loads(body)
    assert data["calls"] == 0
    assert data["by_client"] == []
    assert data["clients"] == 0


def test_trend_and_heatmap(server):
    for path in ("/api/trend?days=30", "/api/heatmap?days=365"):
        code, body, _ = _get(server + path)
        assert code == 200
        json.loads(body)


def test_blogs_without_cache(server):
    code, body, _ = _get(server + "/api/blogs")
    assert code == 200
    data = json.loads(body)
    assert data["posts"] == [] and "feeds" in data


def test_unknown_path_returns_404_json(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(server + "/api/does-not-exist")
    assert exc.value.code == 404
    assert json.loads(exc.value.read())["error"]


def test_diagnostics_download_is_zip(server):
    with urllib.request.urlopen(server + "/api/diagnostics", timeout=15) as resp:
        assert resp.status == 200
        assert "zip" in resp.headers.get("Content-Type", "")
        assert "attachment" in resp.headers.get("Content-Disposition", "")
        blob = resp.read()
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        assert "summary.json" in zf.namelist()


def test_heartbeat_post(server):
    req = urllib.request.Request(server + "/api/heartbeat", data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 200
        assert json.loads(resp.read())["ok"] is True


def test_refresh_endpoint_does_not_hit_network(server, monkeypatch):
    called = {"n": 0}

    def fake_refresh(force=False):
        called["n"] += 1
        return {"fetched_at": 1.0, "feeds": [], "posts": [], "stats": {}, "count": 0}

    monkeypatch.setattr(feeds, "refresh", fake_refresh)
    req = urllib.request.Request(server + "/api/blogs/refresh", data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        assert resp.status == 200
        assert json.loads(resp.read())["count"] == 0
    assert called["n"] == 1


def test_export_returns_csv_with_bom(server):
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO usage_event (client, session_id, event_key, ts, input, output)"
            " VALUES ('claude','s','k',1700000000000,1,2)"
        )
        conn.commit()
    finally:
        conn.close()

    with urllib.request.urlopen(server + "/api/export?days=3650", timeout=15) as resp:
        assert resp.status == 200
        assert "csv" in resp.headers.get("Content-Type", "")
        assert resp.read().startswith(b"\xef\xbb\xbf")


def test_health_carries_scan_state(server):
    """前端靠 /api/health 里的 scan 字段驱动进度条。"""
    code, body, _ = _get(server + "/api/health")
    assert code == 200
    scan = json.loads(body)["scan"]
    assert "running" in scan and "sources" in scan
