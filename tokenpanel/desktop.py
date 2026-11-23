"""桌面客户端外壳。

零额外依赖：优先用本机 Edge / Chrome 的 --app 模式打开一个无地址栏的独立窗口，
看起来就是一个原生应用；找不到浏览器时退回默认浏览器打开。

启动流程：
1. 端口已有服务 → 直接开窗口（单实例）
2. 否则先扫描一次数据，再起本地服务，最后开窗口
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

from . import config, ingest, runtime

# PyInstaller 的 --windowed 模式下 stdout 为 None，任何 print 都会抛 AttributeError。
# 这里兜底到 devnull，保证无控制台打包也能安全运行。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = sys.stdout
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

APP_TITLE = "TokenPanel"


def alert(text: str, title: str = APP_TITLE) -> None:
    """无控制台的打包环境里，用系统弹窗把问题讲清楚。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    except Exception:
        print(text)


def _browser_candidates() -> list[Path]:
    env = os.environ
    roots = [
        env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        env.get("ProgramFiles", r"C:\Program Files"),
        env.get("LOCALAPPDATA", ""),
    ]
    rel = [
        r"Microsoft\Edge\Application\msedge.exe",
        r"Google\Chrome\Application\chrome.exe",
    ]
    out: list[Path] = []
    for root in roots:
        if not root:
            continue
        for r in rel:
            p = Path(root) / r
            if p.exists():
                out.append(p)
    return out


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_port(port: int, timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_open(port):
            return True
        time.sleep(0.15)
    return False


def open_window(url: str, width: int = 1440, height: int = 940) -> bool:
    """用 app 模式打开独立窗口，失败则返回 False。"""
    browsers = _browser_candidates()
    if not browsers:
        return False
    # 独立 profile，保证是独立窗口且不受用户已有浏览器实例影响
    profile = Path(tempfile.gettempdir()) / "tokenpanel-window"
    profile.mkdir(parents=True, exist_ok=True)
    for exe in browsers:
        try:
            subprocess.Popen(
                [
                    str(exe),
                    f"--app={url}",
                    f"--window-size={width},{height}",
                    f"--user-data-dir={profile}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-features=Translate",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except OSError:
            continue
    return False


def _background_scan() -> None:
    try:
        # prepared=True：状态已在开窗前置位，避免二次重置导致进度闪回
        result = ingest.run(prepared=True)
        if result.get("status") == "busy":
            runtime.scan_finished(0, "已有扫描在进行中")
            print("已有扫描在进行中，跳过本次")
            return
        added = sum(v.get("added", 0) for v in result["sources"].values())
        print(f"扫描完成：新增 {added} 条，用时 {result['elapsed_ms']} ms")
    except Exception as exc:  # 后台扫描失败不影响面板使用
        runtime.scan_finished(0, str(exc))
        print(f"扫描失败：{exc}")


def _watch_for_close() -> None:
    """窗口关闭后前端不再发心跳，进程自行退出，不留僵尸占用端口。"""
    # 给页面加载留出时间；从未收到心跳说明浏览器没打开，此时保持常驻
    deadline = time.time() + 40
    while time.time() < deadline and not runtime.BEAT["seen"]:
        time.sleep(0.5)
    while True:
        time.sleep(5)
        if runtime.should_exit():
            print("窗口已关闭，退出。")
            os._exit(0)


def run(port: int = 8765, rescan: bool = True) -> None:
    config.ensure_dirs()

    ok, err = config.is_writable()
    if not ok:
        alert(
            "无法写入数据目录：\n"
            f"{config.DATA_DIR}\n\n"
            f"{err}\n\n"
            "请把 TokenPanel.exe 移到有写权限的文件夹（例如桌面或文档）后重试。"
        )
        sys.exit(2)

    url = f"http://127.0.0.1:{port}"

    if _port_open(port):
        # 已有实例在跑：打开窗口即可，本进程不必常驻
        if not open_window(url):
            webbrowser.open(url)
        return

    from . import server_fallback

    def _serve() -> None:
        try:
            server_fallback.serve(port, watch=True)
        except OSError:
            # 并发双击时端口可能被另一个实例抢占，忽略即可
            pass

    threading.Thread(target=_serve, daemon=True).start()

    if not _wait_port(port):
        print("服务启动失败，请检查端口是否被占用。")
        sys.exit(1)

    # 必须在开窗口之前置位：页面首屏才会显示「正在扫描」，而不是一片空白
    if rescan:
        runtime.scan_started()

    # 先开窗口（页面显示骨架屏），扫描放到后台，避免用户干等
    if not open_window(url):
        print(f"未找到 Edge/Chrome，改用默认浏览器：{url}")
        webbrowser.open(url)

    if rescan:
        threading.Thread(target=_background_scan, daemon=True).start()

    _watch_for_close()
