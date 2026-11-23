"""基础工具、目录数据、体检、报告格式与桌面外壳的纯函数部分。"""

from __future__ import annotations

import json

import pytest

from tokenpanel import catalog, config, desktop, doctor, report
from tokenpanel.adapters import base


# ---------------------------------------------------------------- 适配器工具
def test_event_clamps_and_marks_native():
    row = base.Adapter.event(
        client="c", session_id="s", event_key="k", ts_ms=1,
        project="p", model="m", input_tokens=-5, output_tokens=10**12, cost_usd=0.5,
    )
    assert row[6] == 0
    assert row[7] == base.MAX_TOKENS_PER_CALL
    assert row[12] == "native"

    priced = base.Adapter.event("c", "s", "k2", 1, None, None, 10, 10)
    assert priced[12] is None, "没有原生成本时要留给价格表回算"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1_700_000_000_000, 1_700_000_000_000),
        (1_700_000_000, 1_700_000_000_000),
        ("1700000000", 1_700_000_000_000),
        ("2026-11-20T10:00:00Z", 1_795_168_800_000),
        (None, None),
        (0, None),
        ("not-a-date", None),
    ],
)
def test_to_ms(raw, expected):
    got = base.to_ms(raw)
    if expected is None:
        assert got is None
    else:
        assert got == expected


def test_short_project_keeps_two_levels():
    assert base.short_project(r"C:\Users\me\code\demo") == "code/demo"
    assert base.short_project("/a/b") == "a/b"
    assert base.short_project("demo") == "demo"
    assert base.short_project("") is None
    assert base.short_project(None) is None


def test_iter_files_skips_noise(tmp_path):
    (tmp_path / "keep.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "skip.txt").write_text("x", encoding="utf-8")
    junk = tmp_path / "node_modules"
    junk.mkdir()
    (junk / "deep.jsonl").write_text("{}\n", encoding="utf-8")

    found = [p.name for p in base.iter_files(tmp_path, (".jsonl",))]
    assert found == ["keep.jsonl"]


# ---------------------------------------------------------------- 目录
def _write_catalog(path, providers):
    path.write_text(json.dumps({"meta": {"updated": "2026-11-20"}, "providers": providers}),
                    encoding="utf-8")


def test_catalog_missing_file_is_safe(app_home):
    data = catalog.load(force=True)
    assert data["providers"] == []
    assert data["summary"]["models"] == 0


def test_catalog_flattens_models(app_home):
    _write_catalog(config.CATALOG_PATH, [
        {
            "id": "openai", "name": "OpenAI", "currency": "USD", "verified": True,
            "pricing_url": "https://openai.com/pricing",
            "models": [
                {"id": "gpt-5", "name": "GPT-5", "input": 1.25, "output": 10,
                 "verified": True},
            ],
        },
        {
            "id": "deepseek", "name": "DeepSeek", "currency": "CNY",
            "models": [{"id": "v4", "name": "V4", "input": 4, "output": 12}],
        },
    ])
    data = catalog.load(force=True)
    assert data["summary"]["models"] == 2
    assert data["summary"]["verified_models"] == 1
    row = data["models"][0]
    assert row["currency"] == "USD" and row["symbol"] == "$"

    assert [m["model_id"] for m in catalog.search(query="gpt")] == ["gpt-5"]
    assert [m["model_id"] for m in catalog.search(provider="deepseek")] == ["v4"]


def test_catalog_cheapest(app_home):
    _write_catalog(config.CATALOG_PATH, [
        {"id": "a", "name": "A", "currency": "USD",
         "models": [{"id": "x", "name": "X", "input": 5, "output": 5}]},
        {"id": "b", "name": "B", "currency": "USD",
         "models": [{"id": "y", "name": "Y", "input": 1, "output": 1}]},
    ])
    catalog.load(force=True)
    top = catalog.cheapest("USD", limit=2)
    assert [m["model_id"] for m in top] == ["y", "x"]


def test_catalog_rejects_broken_shape(app_home):
    config.CATALOG_PATH.write_text('{"meta": {}}', encoding="utf-8")
    with pytest.raises(ValueError):
        catalog._read(config.CATALOG_PATH)


# ---------------------------------------------------------------- 体检
def test_dependency_status_shape():
    status = doctor.dependency_status()
    assert {"zstandard", "fastapi", "uvicorn"} <= set(status)
    assert all(isinstance(v, bool) for v in status.values())


def test_doctor_checks_return_tuples():
    ok, detail, _advice = doctor._check_python()
    assert isinstance(ok, bool) and detail
    ok, detail, _advice = doctor._check_writable()
    assert isinstance(ok, bool) and detail


def test_doctor_run_returns_code(app_home, capsys):
    code = doctor.run(quiet=True)
    assert code in (0, 1)


# ---------------------------------------------------------------- 报告格式
@pytest.mark.parametrize(
    ("value", "needle"),
    [
        (0, "0"),
        (999, "999"),
        (12_345, "1.23万"),
        (123_456_789, "1.23亿"),
        (None, "0"),
    ],
)
def test_human_units(value, needle):
    assert needle in report.human(value)


def test_usd_format():
    assert report.usd(None) == "-"
    assert report.usd(0) == "$0.00"
    assert report.usd(1.2345) == "$1.23"


def test_display_name_falls_back():
    assert report.display_name("claude") == "Claude Code"
    assert report.display_name("unknown-x") == "unknown-x"


def test_force_utf8_console_no_crash():
    report._force_utf8_console()


# ---------------------------------------------------------------- 桌面外壳
def test_browser_candidates_returns_paths():
    items = desktop._browser_candidates()
    assert isinstance(items, list)
    for p in items:
        assert p.name.lower() in ("msedge.exe", "chrome.exe")


def test_port_open_false_for_free_port():
    assert desktop._port_open(9) is False


def test_wait_port_false_on_timeout():
    import time

    started = time.time()
    assert desktop._wait_port(9, timeout=0.3) is False
    assert time.time() - started < 2


def test_open_window_builds_app_command(monkeypatch, tmp_path):
    exe = tmp_path / "msedge.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(desktop, "_browser_candidates", lambda: [exe])

    seen = {}

    class FakeProc:
        def poll(self):
            return None

    def fake_popen(args, **kwargs):
        seen["args"] = args
        return FakeProc()

    monkeypatch.setattr(desktop.subprocess, "Popen", fake_popen)
    assert desktop.open_window("http://127.0.0.1:8765") is True
    joined = " ".join(seen["args"])
    assert "--app=http://127.0.0.1:8765" in joined
    assert "--user-data-dir=" in joined
    assert "tokenpanel-window-edge" in joined


def test_open_window_without_browser(monkeypatch):
    monkeypatch.setattr(desktop, "_browser_candidates", list)
    assert desktop.open_window("http://x") is False
