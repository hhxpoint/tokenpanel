"""运行时共享状态。

desktop 与 HTTP 服务分处不同线程，需要交换三类信息：
- 后台扫描进度（前端据此显示「正在扫描什么」并自动刷新）
- 页面心跳（窗口关闭后据此让进程自行退出，不留僵尸）
- 扫描超时保护（异常退出时状态不会永久卡在「扫描中」）

设计原则：读接口必须永远返回一个可用状态，哪怕扫描线程意外死掉。
"""

from __future__ import annotations

import os
import threading
import time

# 扫描超时：超过这个时长仍标记 running，视为状态失联并自动复位
SCAN_TIMEOUT_SEC = float(os.environ.get("TP_SCAN_TIMEOUT", "300"))
LOG_LIMIT = 24

_LOCK = threading.Lock()

SCAN: dict = {
    "running": False,
    "started": 0.0,
    "finished": 0.0,
    "added": 0,
    "error": "",
    "phase": "idle",      # idle / starting / scanning / pricing / done / error
    "current": "",        # 当前正在处理的数据源
    "step": 0,            # 已进入第几个数据源
    "total": 0,           # 本次计划处理的数据源总数
    "files": 0,           # 当前数据源已读文件数
    "events": 0,          # 本次累计解析到的记录数
    "sources": [],        # [{key, label, status, files, events, added, ms}]
    "log": [],            # 最近若干条进度说明
}

BEAT: dict = {"ts": 0.0, "seen": False}

# 页面在后台标签时浏览器会把定时器压到 1 分钟一次，阈值放宽到 3 分钟。
# 可用 TP_BEAT_TIMEOUT 覆盖，便于测试。
BEAT_TIMEOUT_SEC = float(os.environ.get("TP_BEAT_TIMEOUT", "180"))


def scan_started() -> None:
    with _LOCK:
        SCAN.update(
            running=True,
            started=time.time(),
            finished=0.0,
            added=0,
            error="",
            phase="starting",
            current="",
            step=0,
            total=0,
            files=0,
            events=0,
            sources=[],
            log=[],
        )


def scan_plan(items: list[tuple[str, str]]) -> None:
    """登记本次要扫描的数据源，前端据此先画出完整的待办列表。"""
    with _LOCK:
        SCAN["total"] = len(items)
        SCAN["phase"] = "scanning"
        SCAN["sources"] = [
            {
                "key": key,
                "label": label,
                "status": "pending",
                "files": 0,
                "events": 0,
                "added": 0,
                "ms": 0,
            }
            for key, label in items
        ]


def scan_enter(key: str) -> None:
    """进入某个数据源：当前置为进行中。"""
    with _LOCK:
        for src in SCAN["sources"]:
            if src["key"] == key:
                src["status"] = "active"
                break
        SCAN["current"] = key
        SCAN["step"] = min(SCAN["total"], SCAN["step"] + 1)
        SCAN["files"] = 0
        SCAN["phase"] = "scanning"


def scan_files(count: int) -> None:
    """当前数据源已处理的文件数，用于长任务里给出细粒度反馈。"""
    with _LOCK:
        SCAN["files"] = count
        for src in SCAN["sources"]:
            if src["key"] == SCAN["current"]:
                src["files"] = count
                break


def scan_source_done(
    key: str,
    files: int,
    events: int,
    added: int,
    ms: int,
    status: str = "done",
) -> None:
    with _LOCK:
        for src in SCAN["sources"]:
            if src["key"] == key:
                src.update(status=status, files=files, events=events, added=added, ms=ms)
                break
        SCAN["events"] = SCAN.get("events", 0) + events


def scan_skipped(key: str, reason: str) -> None:
    with _LOCK:
        for src in SCAN["sources"]:
            if src["key"] == key:
                src["status"] = "skipped"
                src["note"] = reason
                break


def scan_phase(phase: str) -> None:
    with _LOCK:
        SCAN["phase"] = phase


def scan_note(message: str) -> None:
    with _LOCK:
        log = SCAN["log"]
        log.append({"ts": round(time.time(), 3), "text": message})
        del log[:-LOG_LIMIT]


def scan_finished(added: int = 0, error: str = "") -> None:
    with _LOCK:
        SCAN.update(
            running=False,
            finished=time.time(),
            added=added,
            error=error,
            phase="error" if error else "done",
            current="",
            files=0,
        )


def beat() -> None:
    BEAT["ts"] = time.time()
    BEAT["seen"] = True


def should_exit() -> bool:
    """收到过心跳，且已经超时未再收到 → 说明窗口已关闭。"""
    if not BEAT["seen"]:
        return False
    return (time.time() - BEAT["ts"]) > BEAT_TIMEOUT_SEC


def scan_status() -> dict:
    """永远返回可用状态；发现超时失联会自动复位，避免前端无限转圈。"""
    with _LOCK:
        now = time.time()

        if SCAN["running"]:
            started = SCAN["started"]
            # started 为 0 说明状态被异常打断（线程崩溃等），同样视为失联
            if not started or (now - started) > SCAN_TIMEOUT_SEC:
                SCAN.update(
                    running=False,
                    finished=now,
                    error=f"扫描超过 {int(SCAN_TIMEOUT_SEC)} 秒未完成，已自动复位",
                    phase="error",
                    current="",
                )
                for src in SCAN["sources"]:
                    if src["status"] == "active":
                        src["status"] = "error"

        elapsed = (
            (now - SCAN["started"])
            if SCAN["running"]
            else ((SCAN["finished"] - SCAN["started"]) if SCAN["finished"] else 0.0)
        )

        sources = [dict(s) for s in SCAN["sources"]]
        done = sum(1 for s in sources if s["status"] in ("done", "skipped", "error"))

        return {
            "running": SCAN["running"],
            "added": SCAN["added"],
            "error": SCAN["error"],
            "elapsed": round(elapsed, 1),
            "started": SCAN["started"],
            "finished": SCAN["finished"],
            "phase": SCAN["phase"],
            "current": SCAN["current"],
            "step": SCAN["step"],
            "total": SCAN["total"],
            "files": SCAN["files"],
            "events": SCAN["events"],
            "done": done,
            "percent": round(done / SCAN["total"] * 100) if SCAN["total"] else 0,
            "sources": sources,
            "log": SCAN["log"][-8:],
        }
