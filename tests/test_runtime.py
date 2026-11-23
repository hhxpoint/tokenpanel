"""扫描状态机与心跳：前端进度依赖它，错一步就会一直转圈。"""

from __future__ import annotations

import time

from tokenpanel import runtime


def _reset() -> None:
    runtime.SCAN.update(
        running=False, started=0.0, finished=0.0, added=0, error="",
        phase="", current="", step=0, total=0, files=0, sources=[], log=[],
    )
    runtime.BEAT["ts"] = 0.0
    runtime.BEAT["seen"] = False


def test_initial_status_is_idle():
    _reset()
    s = runtime.scan_status()
    assert s["running"] is False
    assert s["step"] == 0


def test_scan_lifecycle():
    _reset()
    runtime.scan_started()
    runtime.scan_plan([("claude", "Claude Code"), ("codex", "Codex")])
    s = runtime.scan_status()
    assert s["running"] is True and s["total"] == 2

    runtime.scan_enter("claude")
    s = runtime.scan_status()
    assert s["current"] == "claude" and s["phase"] == "scanning" and s["step"] == 1

    runtime.scan_files(7)
    assert runtime.scan_status()["files"] == 7

    runtime.scan_source_done("claude", files=3, events=10, added=9, ms=120, status="done")
    s = runtime.scan_status()
    assert s["sources"][0]["added"] == 9 and s["sources"][0]["status"] == "done"

    runtime.scan_finished(9)
    s = runtime.scan_status()
    assert s["running"] is False and s["added"] == 9


def test_finished_without_start_does_not_crash():
    _reset()
    runtime.scan_finished(3)
    assert runtime.scan_status()["running"] is False


def test_timeout_resets_stuck_scan(monkeypatch):
    _reset()
    runtime.scan_started()
    # 伪造一个「三天前开始、一直没结束」的扫描
    with runtime._LOCK:
        runtime.SCAN["started"] = time.time() - 10_000
    s = runtime.scan_status()
    assert s["running"] is False
    assert "复位" in (s["error"] or "")


def test_zero_started_is_treated_as_lost():
    _reset()
    runtime.scan_started()
    with runtime._LOCK:
        runtime.SCAN["started"] = 0.0
    s = runtime.scan_status()
    assert s["running"] is False


def test_beat_and_exit():
    _reset()
    assert runtime.should_exit() is False, "从未收到心跳时不能退出"
    runtime.beat()
    assert runtime.should_exit() is False
    with runtime._LOCK:
        runtime.BEAT["ts"] = time.time() - 10_000
    assert runtime.should_exit() is True


def test_log_is_bounded():
    _reset()
    for i in range(200):
        runtime.scan_note(f"line-{i}")
    log = runtime.scan_status()["log"]
    # 接口只回最近 8 条，避免长轮询把响应撑大
    assert len(log) == 8
    assert log[-1]["text"].endswith("line-199")
    assert log[0]["text"].endswith("line-192")


def test_scan_status_is_json_safe():
    import json

    _reset()
    runtime.scan_started()
    runtime.scan_enter("claude")
    json.dumps(runtime.scan_status(), ensure_ascii=False)
