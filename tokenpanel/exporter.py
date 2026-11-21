"""CSV 导出（两种服务实现共用）。"""

from __future__ import annotations

import csv
import io
from datetime import datetime

from . import db

COLUMNS = ["时间", "客户端", "会话", "项目", "模型",
           "输入", "输出", "缓存读", "缓存写", "推理", "成本USD"]


def since_ms(days: int) -> int:
    import time

    return 0 if days <= 0 else int((time.time() - days * 86400) * 1000)


MAX_ROWS = 200_000


def export_csv(days: int = 30, max_rows: int = MAX_ROWS) -> bytes:
    """UTF-8 BOM 保证 Excel 双击打开不乱码。

    超过 max_rows 直接报错而不是悄悄截断——导出半个报表比报错更糟。
    """
    with db.session() as conn:
        rows = conn.execute(
            """SELECT ts, client, session_id, project, model,
                      input, output, cache_read, cache_write, reasoning, cost_usd
               FROM usage_event WHERE ts >= ? ORDER BY ts LIMIT ?""",
            (since_ms(days), max_rows + 1),
        ).fetchall()
    if len(rows) > max_rows:
        raise ValueError(f"记录超过 {max_rows:,} 条，请缩小时间范围后再导出")

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(COLUMNS)
    for r in rows:
        writer.writerow([
            datetime.fromtimestamp((r["ts"] or 0) / 1000).strftime("%Y-%m-%d %H:%M:%S"),
            r["client"], r["session_id"], r["project"] or "", r["model"] or "",
            r["input"], r["output"], r["cache_read"], r["cache_write"],
            r["reasoning"],
            "" if r["cost_usd"] is None else round(r["cost_usd"], 6),
        ])
    return buf.getvalue().encode("utf-8-sig")
