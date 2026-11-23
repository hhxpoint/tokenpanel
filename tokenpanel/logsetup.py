"""本地日志与崩溃捕获。

打包成 `--windowed` 之后没有控制台，进程一旦抛异常就是静默闪退，用户和你
都拿不到任何信息。这里补上三件事：

1. 轮转文件日志：默认 2 MB × 3 份，写 `data/logs/tokenpanel.log`
2. 未捕获异常钩子：主线程、子线程、`sys.unraisablehook` 全部接管，带完整堆栈落盘
3. `faulthandler`：段错误、栈溢出、死锁这类原生崩溃也会留下痕迹

日志只落本地，不上传。是否分享由用户在「导出诊断」时自己决定。
初始化失败绝不影响主流程——日志坏了不该让面板起不来。
"""

from __future__ import annotations

import atexit
import faulthandler
import logging
import logging.handlers
import os
import platform
import sys
import threading
import traceback
from pathlib import Path

from . import config

ROOT_NAME = "tokenpanel"
MAX_BYTES = 2 * 1024 * 1024
BACKUPS = 3
LOG_NAME = "tokenpanel.log"
FAULT_NAME = "crash.log"

_configured = False
_fault_handle = None
_hooks_installed = False
_file_ok = True


def log_dir() -> Path:
    return Path(config.DATA_DIR) / "logs"


def log_path() -> Path:
    return log_dir() / LOG_NAME


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _fmt() -> logging.Formatter:
    return logging.Formatter(
        fmt="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _quiet(handler: logging.Handler) -> logging.Handler:
    """日志系统自身的问题不能反过来打断程序。"""
    handler.handleError = lambda record: None  # type: ignore[method-assign]
    return handler


def setup(level: int | None = None) -> logging.Logger:
    """幂等初始化。重复调用只会返回同一个 logger。"""
    global _configured, _file_ok

    root = logging.getLogger(ROOT_NAME)
    root.setLevel(level if level is not None else logging.INFO)
    root.propagate = False

    # 只看「我们的」文件 handler 在不在，别人（pytest、宿主）挂的 handler 不算数
    if any(getattr(h, "_tokenpanel_file", False) for h in root.handlers):
        _configured = True
        return root

    if _configured:
        # 被显式重置过（测试或重新初始化），但 handler 已被移除，重建一次
        _configured = False

    _file_ok = _writable(log_dir())
    if _file_ok:
        try:
            handler = logging.handlers.RotatingFileHandler(
                log_path(),
                maxBytes=MAX_BYTES,
                backupCount=BACKUPS,
                encoding="utf-8",
                delay=True,
            )
            handler.setFormatter(_fmt())
            handler._tokenpanel_file = True  # type: ignore[attr-defined]
            root.addHandler(_quiet(handler))
        except OSError:
            _file_ok = False

    # 源码运行时顺便打到终端；打包后 stderr 往往是 None，不能碰
    if not config.FROZEN and sys.stderr is not None:
        try:
            stream = logging.StreamHandler(sys.stderr)
            stream.setFormatter(_fmt())
            root.addHandler(_quiet(stream))
        except Exception:
            pass

    if not root.handlers:
        root.addHandler(logging.NullHandler())

    logging.captureWarnings(True)
    _install_hooks(root)
    _enable_faulthandler()
    _configured = True
    return root


def get(name: str = ROOT_NAME) -> logging.Logger:
    if not _configured:
        setup()
    return logging.getLogger(name if name.startswith(ROOT_NAME) else f"{ROOT_NAME}.{name}")


# ---------------------------------------------------------------- 异常钩子
def _record(logger: logging.Logger, title: str, text: str) -> None:
    try:
        logger.error("%s\n%s", title, text.rstrip())
    except Exception:
        pass


def _install_hooks(logger: logging.Logger) -> None:
    global _hooks_installed
    if _hooks_installed:
        return

    prev_hook = sys.excepthook

    def excepthook(exc_type, exc_value, exc_tb):
        _record(
            logger,
            "未捕获异常",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )
        try:
            prev_hook(exc_type, exc_value, exc_tb)
        except Exception:
            pass

    sys.excepthook = excepthook

    if hasattr(threading, "excepthook"):
        prev_thread_hook = threading.excepthook

        def thread_hook(args):
            name = getattr(args.thread, "name", "?")
            _record(
                logger,
                f"子线程未捕获异常（{name}）",
                "".join(
                    traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
                ),
            )
            try:
                prev_thread_hook(args)
            except Exception:
                pass

        threading.excepthook = thread_hook

    if hasattr(sys, "unraisablehook"):
        prev_unraisable = sys.unraisablehook

        def unraisable_hook(args):
            tb = getattr(args, "exc_traceback", None)
            detail = "".join(
                traceback.format_exception(type(args.exc_value), args.exc_value, tb)
            )
            where = getattr(args, "object", None)
            _record(logger, f"无法抛出的异常（{where!r}）", detail)
            try:
                prev_unraisable(args)
            except Exception:
                pass

        sys.unraisablehook = unraisable_hook

    _hooks_installed = True


def _enable_faulthandler() -> None:
    global _fault_handle
    if _fault_handle is not None or not _file_ok:
        return
    try:
        # 句柄必须活到进程结束，faulthandler 会一直往里写，不能用 with
        _fault_handle = open(  # noqa: SIM115
            log_dir() / FAULT_NAME, "a", encoding="utf-8", buffering=1
        )
        faulthandler.enable(file=_fault_handle, all_threads=True)

        def _close() -> None:
            try:
                faulthandler.disable()
            finally:
                try:
                    _fault_handle.close()  # type: ignore[union-attr]
                except Exception:
                    pass

        atexit.register(_close)
    except Exception:
        _fault_handle = None


# ---------------------------------------------------------------- 记录
def startup_banner(version: str) -> None:
    logger = get()
    try:

        db_size = config.DB_PATH.stat().st_size if config.DB_PATH.exists() else 0
        db_note = f"{db_size / 1024:.0f} KB"
    except Exception:
        db_note = "未知"

    logger.info(
        "TokenPanel %s 启动 | Python %s | %s %s | frozen=%s",
        version,
        platform.python_version(),
        platform.system(),
        platform.release(),
        config.FROZEN,
    )
    logger.info(
        "APP_HOME=%s | DATA_DIR=%s | DB=%s (%s) | log=%s",
        config.APP_HOME,
        config.DATA_DIR,
        config.DB_PATH,
        db_note,
        log_path() if _file_ok else "不可写",
    )


def capture(exc: BaseException, where: str = "") -> None:
    """在 except 块里显式记录一条异常。"""
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    _record(get(), f"异常（{where or '未标注'}）", detail)


def tail(lines: int = 300) -> str:
    """取最近若干行日志，跨轮转文件。用于导出诊断。"""
    chunks: list[str] = []
    for name in (LOG_NAME, *(f"{LOG_NAME}.{i}" for i in range(1, BACKUPS + 1))):
        path = log_dir() / name
        try:
            if path.exists():
                with open(path, encoding="utf-8", errors="replace") as fh:
                    chunks.append(fh.read())
        except OSError:
            continue
    if not chunks:
        return ""
    text = "\n".join(reversed(chunks))
    parts = text.splitlines()
    return "\n".join(parts[-lines:])


def summary() -> dict:
    return {
        "log_dir": str(log_dir()),
        "file_ok": _file_ok,
        "log_path": str(log_path()),
        "crash_log": str(log_dir() / FAULT_NAME),
        "size": log_path().stat().st_size if log_path().exists() else 0,
        "pid": os.getpid(),
        "threads": threading.active_count(),
    }
