"""日志与崩溃捕获：写入、轮转目录、异常钩子。"""

from __future__ import annotations

import sys
import threading

from tokenpanel import logsetup


def test_setup_is_idempotent(app_home, clean_logging):
    first = logsetup.setup()
    second = logsetup.setup()
    assert first is second
    assert logsetup.log_path().parent.is_dir()


def test_writes_to_file(app_home, clean_logging):
    log = logsetup.setup()
    log.info("hello-log-line")
    for handler in log.handlers:
        handler.flush()
    text = logsetup.tail(50)
    assert "hello-log-line" in text


def test_capture_includes_traceback(app_home, clean_logging):
    logsetup.setup()
    try:
        raise ValueError("boom-marker")
    except ValueError as exc:
        logsetup.capture(exc, "unit.test")
    for handler in logsetup.get().handlers:
        handler.flush()
    text = logsetup.tail(80)
    assert "boom-marker" in text
    assert "Traceback" in text
    assert "unit.test" in text


def test_excepthook_records_uncaught(app_home, clean_logging):
    logsetup.setup()
    hook = sys.excepthook
    try:
        raise RuntimeError("uncaught-marker")
    except RuntimeError:
        exc_type, exc_value, exc_tb = sys.exc_info()
        hook(exc_type, exc_value, exc_tb)
    for handler in logsetup.get().handlers:
        handler.flush()
    assert "uncaught-marker" in logsetup.tail(80)


def test_thread_hook_records_failure(app_home, clean_logging):
    logsetup.setup()
    hook = threading.excepthook

    def boom():
        raise RuntimeError("thread-marker")

    t = threading.Thread(target=boom, name="boom-thread")
    t.start()
    t.join()

    for handler in logsetup.get().handlers:
        handler.flush()
    text = logsetup.tail(120)
    assert "thread-marker" in text or "boom-thread" in text
    assert callable(hook)


def test_tail_is_bounded(app_home, clean_logging):
    log = logsetup.setup()
    for i in range(50):
        log.info("line-%d", i)
    for handler in log.handlers:
        handler.flush()
    tail = logsetup.tail(10)
    assert len(tail.splitlines()) <= 10


def test_summary_shape(app_home, clean_logging):
    logsetup.setup()
    info = logsetup.summary()
    assert set(info) >= {"log_dir", "file_ok", "log_path", "size", "pid"}
    assert isinstance(info["pid"], int)


def test_unwritable_dir_still_returns_logger(app_home, clean_logging, monkeypatch):
    """日志目录不可写时，程序必须照常起来。"""
    monkeypatch.setattr(logsetup, "_writable", lambda path: False)
    log = logsetup.setup()
    log.info("degraded-mode")
    assert logsetup.summary()["file_ok"] is False
