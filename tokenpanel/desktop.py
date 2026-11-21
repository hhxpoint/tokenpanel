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

from . import config, ingest

APP_TITLE = "TokenPanel"


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


def run(port: int = 8765, rescan: bool = True) -> None:
    url = f"http://127.0.0.1:{port}"

    if _port_open(port):
        # 已有实例在跑，直接开窗口
        if not open_window(url):
            webbrowser.open(url)
        return

    if rescan:
        try:
            result = ingest.run()
            added = sum(v.get("added", 0) for v in result["sources"].values())
            print(f"扫描完成：新增 {added} 条事件，用时 {result['elapsed_ms']} ms")
        except Exception as exc:  # 扫描失败不阻止面板启动
            print(f"扫描失败，仍启动面板：{exc}")

    from . import server_fallback

    threading.Thread(target=server_fallback.serve, args=(port,), daemon=True).start()

    if not _wait_port(port):
        print("服务启动失败，请检查端口是否被占用。")
        sys.exit(1)

    if open_window(url):
        print(f"已打开客户端窗口：{url}")
    else:
        print(f"未找到 Edge/Chrome，改用默认浏览器：{url}")
        webbrowser.open(url)

    # 保持进程存活，窗口关闭后随服务一起退出
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
