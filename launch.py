"""PyInstaller 入口。打包后双击即启动桌面客户端。

日志必须在任何业务代码之前就位：打包成 --windowed 后没有控制台，
这里如果晚了，启动阶段的崩溃就再也拿不到了。
"""

import sys

from tokenpanel import __version__, logsetup


def main() -> None:
    logsetup.setup()
    logsetup.startup_banner(__version__)

    from tokenpanel.desktop import run

    try:
        run()
    except SystemExit:
        raise
    except BaseException as exc:
        logsetup.capture(exc, "desktop.run")
        sys.exit(1)


if __name__ == "__main__":
    main()
