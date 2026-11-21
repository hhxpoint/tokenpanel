"""运行时共享状态。

desktop 与 HTTP 服务分处不同线程，需要交换两类信息：
- 后台扫描进度（前端据此显示「正在扫描」并自动刷新）
- 页面心跳（窗口关闭后据此让进程自行退出，不留僵尸）
"""

from __future__ import annotations

import os
import time

SCAN: dict = {
    "running": False,
    "started": 0.0,
    "finished": 0.0,
    "added": 0,
    "error": "",
}

BEAT: dict = {"ts": 0.0, "seen": False}

# 页面在后台标签时浏览器会把定时器压到 1 分钟一次，阈值放宽到 3 分钟。
# 可用 TP_BEAT_TIMEOUT 覆盖，便于测试。
BEAT_TIMEOUT_SEC = float(os.environ.get("TP_BEAT_TIMEOUT", "180"))


def scan_started() -> None:
    SCAN.update(running=True, started=time.time(), error="")


def scan_finished(added: int = 0, error: str = "") -> None:
    SCAN.update(running=False, finished=time.time(), added=added, error=error)


def beat() -> None:
    BEAT["ts"] = time.time()
    BEAT["seen"] = True


def should_exit() -> bool:
    """收到过心跳，且已经超时未再收到 → 说明窗口已关闭。"""
    if not BEAT["seen"]:
        return False
    return (time.time() - BEAT["ts"]) > BEAT_TIMEOUT_SEC


def scan_status() -> dict:
    now = time.time()
    elapsed = (now - SCAN["started"]) if SCAN["running"] else (
        (SCAN["finished"] - SCAN["started"]) if SCAN["finished"] else 0
    )
    return {
        "running": SCAN["running"],
        "added": SCAN["added"],
        "error": SCAN["error"],
        "elapsed": round(elapsed, 1),
    }
