"""导出与诊断包：编码、超限、脱敏、内容边界。"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from tokenpanel import db, diagnostics, exporter, logsetup


def _seed(conn, n: int) -> None:
    conn.executemany(
        "INSERT INTO usage_event (client, session_id, event_key, ts, project, model,"
        " input, output, cost_usd) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("claude", "s", f"k{i}", 1_700_000_000_000 + i, "p", "claude-3", 10, 5, 0.001)
            for i in range(n)
        ],
    )


def test_export_csv_has_bom_and_header(app_home):
    conn = db.connect()
    try:
        _seed(conn, 2)
        conn.commit()
    finally:
        conn.close()

    body = exporter.export_csv(3650)
    assert body.startswith(b"\xef\xbb\xbf"), "Excel 双击不乱码的前提是 UTF-8 BOM"
    text = body.decode("utf-8-sig")
    assert "客户端" in text.splitlines()[0]
    assert len(text.strip().splitlines()) == 3


def test_export_refuses_over_limit(app_home):
    conn = db.connect()
    try:
        _seed(conn, 5)
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ValueError):
        exporter.export_csv(3650, max_rows=3)


def test_diagnostics_zip_contents(app_home, clean_logging):
    db_path = app_home / "data" / "tokenpanel.db"
    conn = db.connect(db_path)
    try:
        _seed(conn, 3)
        conn.commit()
    finally:
        conn.close()

    logsetup.setup()
    logsetup.get().info("测试写入一行日志")

    blob = diagnostics.export_zip()
    assert isinstance(blob, bytes)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = set(zf.namelist())
        assert {"summary.json", "logs/tail.log", "schema.txt", "scan_log.txt"} <= names
        summary = json.loads(zf.read("summary.json").decode("utf-8"))
        assert summary["database"]["usage_event_rows"] == 3
        assert "测试写入一行日志" in zf.read("logs/tail.log").decode("utf-8")


def test_diagnostics_masks_secrets(app_home, clean_logging, monkeypatch):
    monkeypatch.setenv("TP_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("TP_CLAUDE_DIR", "/tmp/claude")
    summary = diagnostics.build_summary()
    assert summary["env"]["TP_API_KEY"] == "***"
    assert summary["env"]["TP_CLAUDE_DIR"] == "/tmp/claude"


def test_diagnostics_contains_no_token_details(app_home, clean_logging):
    blob = diagnostics.export_zip()
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        joined = b"".join(zf.read(n) for n in zf.namelist() if n != "logs/tail.log")
    # 只允许出现计数与结构，不允许出现任何会话/提示词字段
    for forbidden in (b"session_id", b"prompt", b"messages[", b"content"):
        assert forbidden not in joined, f"诊断包不应包含 {forbidden!r}"
