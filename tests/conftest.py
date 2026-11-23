"""测试环境隔离。

所有测试都在临时目录里跑：绝不允许碰到用户真实的 data/、数据库或日志。
"""

from __future__ import annotations

import logging

import pytest

from tokenpanel import config, feeds, logsetup, pricing


@pytest.fixture
def app_home(tmp_path, monkeypatch):
    """把可写目录整体搬到 tmp_path。"""
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "APP_HOME", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "DB_PATH", data / "tokenpanel.db")
    monkeypatch.setattr(config, "BLOG_CACHE", data / "blogs.json")
    monkeypatch.setattr(config, "PRICING_PATH", tmp_path / "pricing.json")
    monkeypatch.setattr(config, "CATALOG_PATH", tmp_path / "catalog.json")

    feeds._cache = None
    pricing._cache = None
    yield tmp_path
    feeds._cache = None
    pricing._cache = None


@pytest.fixture
def clean_logging():
    """重置日志单例，避免测试之间互相污染。"""
    root = logging.getLogger(logsetup.ROOT_NAME)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    logsetup._configured = False
    logsetup._file_ok = True
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    logsetup._configured = False


@pytest.fixture
def sample_pricing(app_home):
    import json

    table = {
        "models": {
            "claude-3": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
            "claude-3-5-sonnet": {"input": 3.0, "output": 15.0},
            "gpt-4o": {"input": 2.5, "output": 10.0},
            "qwen-max": {"input": 1.6, "output": 6.4},
        }
    }
    config.PRICING_PATH.write_text(json.dumps(table), encoding="utf-8")
    pricing._cache = None
    return table
