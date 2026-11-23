"""汇率、版本比较、路径与价格表合并。"""

from __future__ import annotations

import json

import pytest

from tokenpanel import config, fx, updater


# ---------------------------------------------------------------- 汇率
@pytest.mark.parametrize(
    ("amount", "src", "dst", "expected"),
    [
        (1, "USD", "CNY", 7.0),
        (7, "CNY", "USD", 1.0),
        (1, "USD", "USD", 1.0),
        (0, "USD", "CNY", 0.0),
    ],
)
def test_convert(amount, src, dst, expected):
    assert fx.convert(amount, src, dst, 7.0) == pytest.approx(expected)


def test_convert_none():
    assert fx.convert(None, "USD", "CNY", 7.0) is None


def test_convert_rejects_bad_rate():
    assert fx.convert(1, "USD", "CNY", 0) is None
    assert fx.convert(1, "USD", "CNY", -3) is None


def test_rate_sanity_range():
    assert fx._valid(7.1) is True
    assert fx._valid(0.5) is False
    assert fx._valid(99) is False


def test_get_uses_cache_when_fresh(app_home, monkeypatch):
    cache = app_home / "data" / "fx.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({
        "usd_cny": 7.12, "source": "unit", "home": "",
        "as_of": "2026-11-23", "fetched_at": 9_999_999_999,
    }), encoding="utf-8")

    monkeypatch.setattr(fx, "_fetch", lambda: (_ for _ in ()).throw(AssertionError("不该联网")))
    got = fx.get(force=False)
    assert got["usd_cny"] == pytest.approx(7.12)


def test_get_falls_back_when_offline(app_home, monkeypatch):
    monkeypatch.setattr(fx, "_fetch", lambda: None)
    got = fx.get(force=False)
    assert got["usd_cny"] > 5
    assert got.get("stale") in (True, False) or "source" in got


# ---------------------------------------------------------------- 版本
@pytest.mark.parametrize(
    ("candidate", "current", "expected"),
    [
        ("0.3.1", "0.2.9", True),
        ("0.3.0", "0.3.0", False),
        ("0.2.9", "0.3.0", False),
        ("v0.4", "0.3.9", True),
    ],
)
def test_is_newer(candidate, current, expected):
    assert updater.is_newer(candidate, current) is expected


def test_version_info_shape():
    info = updater.version_info()
    assert info["version"] and "python" in info and isinstance(info["frozen"], bool)


def test_check_handles_offline(monkeypatch):
    monkeypatch.setattr(updater, "RELEASES_API", "http://127.0.0.1:9/none")
    got = updater.check(timeout=0.5)
    assert got["checked"] is False and got["error"] and got["has_update"] is False


# ---------------------------------------------------------------- 路径
def test_first_existing_prefers_real(tmp_path):
    real = tmp_path / "a"
    real.mkdir()
    missing = tmp_path / "b"
    assert config.first_existing([missing, real]) == real


def test_first_existing_returns_first_when_none(tmp_path):
    missing = tmp_path / "nope"
    assert config.first_existing([missing]) == missing


def test_is_writable_true(app_home):
    config.ensure_dirs()
    ok, err = config.is_writable()
    assert ok is True and err == ""


def test_is_writable_false(app_home, monkeypatch):
    def boom(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr("pathlib.Path.write_bytes", boom)
    ok, err = config.is_writable()
    assert ok is False and "read-only" in err


def test_merge_pricing_keeps_user_edits(app_home):
    bundled = app_home / "bundled.json"
    user = app_home / "user.json"
    bundled.write_text(json.dumps({
        "models": {"a": {"input": 1.0}, "b": {"input": 2.0}},
        "_meta": {"updated": "2026-11-20"},
    }), encoding="utf-8")
    user.write_text(json.dumps({
        "models": {"a": {"input": 9.9}},
        "_meta": {"updated": "2026-11-01"},
    }), encoding="utf-8")

    config._merge_pricing(bundled, user)
    merged = json.loads(user.read_text(encoding="utf-8"))
    assert merged["models"]["a"]["input"] == 9.9, "用户改过的价格不能被覆盖"
    assert merged["models"]["b"]["input"] == 2.0, "缺失的型号要补进来"
    # _meta 属于用户可改的元信息，合并时不做覆盖
    assert merged["_meta"]["updated"] == "2026-11-01"


def test_ensure_dirs_creates_data(app_home):
    config.ensure_dirs()
    assert config.DATA_DIR.is_dir()
