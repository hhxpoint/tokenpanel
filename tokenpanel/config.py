"""路径与运行参数。所有路径都允许用环境变量覆盖，方便测试与迁移。"""

from __future__ import annotations

import os
from pathlib import Path

HOME = Path.home()
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("TOKENPANEL_DATA", ROOT / "data"))
DB_PATH = DATA_DIR / "tokenpanel.db"
PRICING_PATH = Path(os.environ.get("TOKENPANEL_PRICING", ROOT / "pricing.json"))

# 各客户端数据源位置。用环境变量覆盖是为了测试时能指向样本目录。
SOURCES = {
    "claude": Path(os.environ.get("TP_CLAUDE_DIR", HOME / ".claude" / "projects")),
    "dsh": Path(os.environ.get("TP_DSH_DIR", HOME / ".dsh" / "sessions")),
    "codex": Path(os.environ.get("TP_CODEX_DIR", HOME / ".codex" / "sessions")),
    "opencode": Path(
        os.environ.get("TP_OPENCODE_DB", HOME / ".local" / "share" / "opencode" / "opencode.db")
    ),
    "workbuddy": Path(
        os.environ.get("TP_WORKBUDDY_DB", HOME / ".workbuddy" / "workbuddy.db")
    ),
}

# 全量重扫的阈值：单文件超过这个大小就跳过（防止误读巨大文件卡死）。
MAX_FILE_BYTES = int(os.environ.get("TP_MAX_FILE_BYTES", 512 * 1024 * 1024))


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
