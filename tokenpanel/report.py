"""CLI：scan / report / serve。"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime

from . import config, db, ingest, pricing


DISPLAY = {
    "claude": "Claude Code",
    "codex": "Codex",
    "dsh": "dsh",
    "opencode": "OpenCode",
    "workbuddy": "WorkBuddy",
    "qoder": "Qoder",
    "trae": "Trae",
    "copilot": "Copilot",
    "codebuddy": "CodeBuddy",
    "marvis": "Marvis",
}


def human(n: float | int | None) -> str:
    """中文计数单位：万 / 亿 / 万亿。"""
    n = float(n or 0)
    a = abs(n)
    if a < 10_000:
        return str(int(round(n)))
    if a < 100_000_000:
        v = n / 10_000
        return f"{v:.2f}万" if abs(v) < 100 else f"{v:.1f}万"
    if a < 1_000_000_000_000:
        return f"{n / 100_000_000:.2f}亿"
    return f"{n / 1_000_000_000_000:.2f}万亿"


def usd(v: float | None) -> str:
    if v is None:
        return "-"
    return f"${v:,.2f}"


def cmd_scan(args: argparse.Namespace) -> None:
    result = ingest.run(force=args.force, recalc_all=args.recalc)
    print(f"扫描完成，用时 {result['elapsed_ms']} ms，回算成本 {result['priced_rows']} 行\n")
    for name, info in result["sources"].items():
        status = info.get("status")
        if status == "skipped":
            print(f"  {name:<10} 跳过（{info.get('reason')}）")
            continue
        print(
            f"  {name:<10} {status:<8} 文件 {info.get('files', 0):<3} "
            f"解析 {info.get('parsed', 0):<5} 新增 {info.get('added', 0):<5} "
            f"快照 {info.get('credits', 0)}"
        )
        for err in info.get("errors", []):
            print(f"      ! {err}")


def cmd_report(args: argparse.Namespace) -> None:
    since = int((time.time() - args.days * 86400) * 1000)
    with db.session() as conn:
        rows = db.totals_by_client(conn)
        print("=== 全量总账（累计） ===")
        if not rows:
            print("  暂无数据，先运行: python -m tokenpanel scan")
            return
        print(f"{'客户端':<12}{'调用':>7}{'输入':>9}{'输出':>9}{'缓存读':>9}{'缓存写':>9}{'合计':>9}{'成本':>11}")
        grand = 0
        sum_cost = 0.0
        sum_in = sum_out = sum_cr = sum_cw = sum_re = 0
        for r in rows:
            total = (r["input"] or 0) + (r["output"] or 0) + (r["cache_read"] or 0) + (r["cache_write"] or 0)
            grand += total
            sum_cost += r["cost_usd"] or 0
            sum_in += r["input"] or 0
            sum_out += r["output"] or 0
            sum_cr += r["cache_read"] or 0
            sum_cw += r["cache_write"] or 0
            sum_re += r["reasoning"] or 0
            print(
                f"{DISPLAY.get(r['client'], r['client']):<12}{r['calls']:>7}"
                f"{human(r['input']):>9}{human(r['output']):>9}"
                f"{human(r['cache_read']):>9}{human(r['cache_write']):>9}"
                f"{human(total):>9}{usd(r['cost_usd']):>11}"
            )
        print(
            f"{'合计':<12}{'':>7}{human(sum_in):>9}{human(sum_out):>9}"
            f"{human(sum_cr):>9}{human(sum_cw):>9}{human(grand):>9}{usd(sum_cost):>11}"
        )
        print(f"\n总计 {human(grand)} tokens：输入 {human(sum_in)} · 输出 {human(sum_out)} · "
              f"缓存读 {human(sum_cr)} · 缓存写 {human(sum_cw)} · 推理 {human(sum_re)}")

        models = db.by_model(conn, since)
        if models:
            print(f"\n=== 近 {args.days} 天按模型 ===")
            for r in models[:12]:
                print(f"  {DISPLAY.get(r['client'], r['client']):<12}{str(r['model'])[:36]:<38}"
                      f"{human(r['tokens']):>9}{usd(r['cost_usd']):>11}")

        projects = db.by_project(conn, since, limit=10)
        if projects:
            print(f"\n=== 近 {args.days} 天按项目 ===")
            for r in projects:
                print(f"  {DISPLAY.get(r['client'], r['client']):<12}{str(r['project'])[:36]:<38}"
                      f"{human(r['tokens']):>9}{usd(r['cost_usd']):>11}")

        credits = db.credit_rows(conn)
        if credits:
            total_credit = sum((c["credits"] or 0) for c in credits)
            print(f"\n=== 快照类（WorkBuddy，覆盖语义不可累加） ===")
            print(f"  会话数 {len(credits)}，credit 合计 {total_credit:,.1f}")
            for c in credits[:5]:
                when = datetime.fromtimestamp((c["ts"] or 0) / 1000).strftime("%m-%d %H:%M")
                print(
                    f"  {when}  {str(c['project'] or '-')[:30]:<32}"
                    f"上下文 {human(c['used']):>7}/{human(c['size']):>7}"
                    f"  credit {c['credits'] if c['credits'] is not None else '-'}"
                )

        misses = pricing.unpriced_models(conn)
        if misses:
            print("\n! 以下模型未在 pricing.json 命中，成本按「未定价」显示（不计入金额）：")
            for m in misses[:12]:
                print(f"    {m}")
            print("  如需金额，请在 pricing.json 的 models 下补充单价后执行: scan --recalc")


def cmd_discover(args: argparse.Namespace) -> None:
    from . import discovery

    items = discovery.discover(deep=args.deep)
    if not items:
        print("未发现任何 AI 客户端数据目录。")
        return
    print(f"本机发现 {len(items)} 个 harness：\n")
    print(f"{'客户端':<22}{'状态':<10}{'类型':<12}{'证据':<22}{'体积':>8}  路径")
    print("-" * 110)
    for h in items:
        status = "可量化" if h.supported else "仅发现"
        size = f"{h.size_mb:.0f}MB" if h.size_mb else "-"
        print(
            f"{h.client:<22}{status:<10}{h.kind:<12}{h.evidence[:20]:<22}{size:>8}  {h.path}"
        )
        if h.note:
            print(f"{'':<22}└ {h.note}")
    s = discovery.summary(items)
    print(f"\n可量化 {len(s['supported'])} 个：{'、'.join(s['supported']) or '无'}")
    if s["detected_only"]:
        print(f"仅发现 {len(s['detected_only'])} 个：{'、'.join(s['detected_only'])}")


def cmd_desktop(args: argparse.Namespace) -> None:
    from . import desktop

    desktop.run(port=args.port, rescan=not args.no_scan)


def cmd_serve(args: argparse.Namespace) -> None:
    import threading
    import webbrowser

    print("正在扫描本地数据…")
    try:
        result = ingest.run()
        added = sum(v.get("added", 0) for v in result["sources"].values())
        print(f"扫描完成，新增 {added} 条事件，用时 {result['elapsed_ms']} ms")
    except Exception as exc:
        print(f"扫描失败（仍会启动面板）：{exc}")

    url = f"http://127.0.0.1:{args.port}"
    if args.open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    try:
        import uvicorn

        from .server import app
    except ImportError:
        # FastAPI 不可用时退回标准库实现，接口完全一致
        from . import server_fallback

        server_fallback.serve(args.port)
        return

    print(f"面板地址: {url}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tokenpanel", description="AI 客户端 token 用量面板")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="采集本地数据")
    p_scan.add_argument("--force", action="store_true", help="忽略文件指纹，全量重扫")
    p_scan.add_argument("--recalc", action="store_true", help="按最新价格表重算全部成本")
    p_scan.set_defaults(func=cmd_scan)

    p_rep = sub.add_parser("report", help="终端报表")
    p_rep.add_argument("--days", type=int, default=7)
    p_rep.set_defaults(func=cmd_report)

    p_dis = sub.add_parser("discover", help="检索本机 AI 客户端 harness")
    p_dis.add_argument("--deep", action="store_true", help="同时统计目录体积（较慢）")
    p_dis.set_defaults(func=cmd_discover)

    p_desk = sub.add_parser("desktop", help="以桌面客户端窗口启动")
    p_desk.add_argument("--port", type=int, default=8765)
    p_desk.add_argument("--no-scan", action="store_true", help="启动时不扫描")
    p_desk.set_defaults(func=cmd_desktop)

    p_srv = sub.add_parser("serve", help="启动可视化面板")
    p_srv.add_argument("--port", type=int, default=8765)
    p_srv.add_argument("--no-open", dest="open", action="store_false", help="不自动打开浏览器")
    p_srv.set_defaults(func=cmd_serve, open=True)

    args = parser.parse_args(argv)
    config.ensure_dirs()
    args.func(args)
