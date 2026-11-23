"""环境体检。

解决的问题：脱离开发环境后，用户最常遇到的是「Python 版本不对」「缺 zstandard
导致 dsh/Codex 数据静默消失」「端口被占用」。这里一次性检查并给出可执行的修复建议。

用法：
    python -m tokenpanel doctor          # 完整报告
    python -m tokenpanel doctor --quiet  # 只返回码，供启动脚本判断
"""

from __future__ import annotations

import socket
import sys

from . import config

OK = "\u2713"
BAD = "\u2717"
WARN = "\u25cb"


def _check_python() -> tuple[bool, str, str]:
    v = sys.version_info
    ok = v >= (3, 10)
    detail = f"{v.major}.{v.minor}.{v.micro} · {sys.executable}"
    advice = "" if ok else "需要 Python 3.10 及以上，请升级后重试"
    return ok, detail, advice


def _check_zstandard() -> tuple[bool, str, str, bool]:
    """返回 (可用, 说明, 建议, 是否必需)。"""
    try:
        import zstandard  # noqa: F401

        return True, "已安装", "", True
    except ImportError:
        return (
            False,
            "缺失",
            "dsh 与 Codex 的会话是 zstd 压缩的，缺它这两路数据会缺失。运行 setup.bat 或 pip install zstandard",
            True,
        )


def _check_optional() -> list[tuple[bool, str, str, bool]]:
    out = []
    for name in ("fastapi", "uvicorn"):
        try:
            __import__(name)
            out.append((True, name, "已安装", False))
        except ImportError:
            out.append((False, name, "未安装（可选，会自动使用标准库服务）", False))
    return out


def _check_sources() -> list[tuple[bool, str, str]]:
    from . import discovery

    rows = []
    try:
        items = discovery.discover()
    except Exception as exc:
        return [(False, "发现失败", str(exc))]
    for h in items:
        if h.supported:
            rows.append((True, h.client, h.path))
        else:
            rows.append((False, h.client + "（仅发现）", h.note or h.path))
    return rows


def _check_port(port: int = 8765) -> tuple[bool, str, str]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        in_use = s.connect_ex(("127.0.0.1", port)) == 0
    if in_use:
        return True, f"{port} 已有 TokenPanel 在运行", "直接双击启动即可打开窗口"
    return True, f"{port} 空闲", ""


def _check_writable() -> tuple[bool, str, str]:
    ok, err = config.is_writable()
    if ok:
        return True, str(config.DATA_DIR), ""
    return False, "目录不可写", f"{err}；请把程序移到桌面或文档等可写目录"


def _check_data() -> tuple[bool, str, str]:
    db = config.DB_PATH
    if not db.exists():
        return True, "尚未采集", "首次启动会自动扫描"
    try:
        size_mb = db.stat().st_size / 1_048_576
        import sqlite3

        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            n = conn.execute("SELECT COUNT(*) FROM usage_event").fetchone()[0]
        finally:
            conn.close()
        return True, f"{n:,} 条事件 · 数据库 {size_mb:.1f} MB", ""
    except Exception as exc:
        return False, "数据库读取失败", str(exc)


def dependency_status() -> dict[str, bool]:
    """给 API 用的依赖快照。"""
    status: dict[str, bool] = {}
    for name in ("zstandard", "fastapi", "uvicorn"):
        try:
            __import__(name)
            status[name] = True
        except ImportError:
            status[name] = False
    return status


def run(quiet: bool = False) -> int:
    problems: list[str] = []

    def emit(mark: str, title: str, detail: str = "", advice: str = "", required: bool = False):
        if not quiet:
            line = f"  {mark} {title}"
            if detail:
                line += f"  {detail}"
            print(line)
            if advice:
                print(f"      \u2192 {advice}")
        if mark == BAD and required:
            problems.append(f"{title}：{advice}")

    def section(name: str) -> None:
        if not quiet:
            print(f"\n[{name}]")

    if not quiet:
        print("TokenPanel 环境体检")

    section("运行环境")
    py_ok, py_detail, py_advice = _check_python()
    emit(OK if py_ok else BAD, "Python", py_detail, py_advice, required=True)
    if not py_ok and quiet:
        problems.append("Python 版本过低")

    zs_ok, zs_detail, zs_advice, zs_req = _check_zstandard()
    emit(OK if zs_ok else BAD, "zstandard", zs_detail, zs_advice, required=zs_req)
    if not zs_ok and quiet:
        problems.append("缺少 zstandard")

    section("可选组件")
    for ok, name, detail, _ in _check_optional():
        emit(OK if ok else WARN, name, detail)

    section("数据源")
    for ok, name, detail in _check_sources():
        emit(OK if ok else WARN, name, detail)

    section("运行状态")
    _port_ok, port_detail, port_advice = _check_port()
    emit(OK, "端口", port_detail, port_advice)
    wr_ok, wr_detail, wr_advice = _check_writable()
    emit(OK if wr_ok else BAD, "数据目录", wr_detail, wr_advice, required=True)
    if not wr_ok and quiet:
        problems.append("数据目录不可写")
    data_ok, data_detail, data_advice = _check_data()
    emit(OK if data_ok else BAD, "数据", data_detail, data_advice)

    if not quiet:
        print()
        if problems:
            print("需要处理：")
            for p in problems:
                print("  -", p)
            print("\n最快的修复方式：双击 setup.bat")
        else:
            print("一切就绪。双击 TokenPanel.vbs 即可打开面板。")

    return 1 if problems else 0


def main(quiet: bool = False) -> int:
    return run(quiet=quiet)
