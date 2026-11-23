# TokenPanel v0.4.0 — 可诊断、可回归

这一版不加功能。补的是工程上真正会卡住人的两件事：**出问题时看不见**，和**改代码时不敢改**。

## 崩溃不再是黑箱

打包成 `--windowed` 之后没有控制台，以前一旦抛异常就是静默闪退，什么都拿不到。现在：

- `data/logs/tokenpanel.log`：2 MB 轮转，保留 3 份，记录启动环境、抓取结果、扫描异常
- `sys.excepthook` / `threading.excepthook` / `sys.unraisablehook` 三个钩子全部接管，主线程、子线程、析构阶段的异常都带完整堆栈落盘
- `faulthandler` 把段错误、栈溢出写进 `data/logs/crash.log`
- 日志初始化失败也不会影响启动，只会降级

## 一键诊断包

页脚新增「导出诊断」，命令行等价 `python -m tokenpanel diagnostics --out d.zip`。

包里有 `summary.json`（版本、系统、路径、库统计、源状态）、最近 600 行日志、崩溃记录、表结构、最近 30 次扫描摘要。

**不含** token 明细、会话内容或提示词。环境变量里名字带 key / token / secret 的一律打成 `***`。可以直接发出来排查。

## 测试与静态检查

| 项目 | 之前 | 现在 |
| --- | --- | --- |
| 单元/集成测试 | 0 | 146 项 pytest |
| 覆盖率 | 无统计 | 72%，门槛 70% |
| 静态检查 | 只有 compileall | ruff + mypy 全绿 |
| CI | 单 job 三矩阵 | lint / test（4 矩阵）/ selftest（3 平台）/ build |

测试覆盖到的关键路径：数据库版本迁移与损坏隔离、计价最长前缀匹配、聚合的 RSS/Atom/列表页解析与合并排序、五个内置适配器的真实样本、服务端 11 个接口、日志钩子、诊断脱敏。

## 顺手修掉的真实问题

这些不是「看起来不优雅」，是会在用户机器上出事的：

1. **单源扫描异常会中断整次采集** —— `ingest.run` 里适配器抛异常直接冒到顶层，一个坏文件就能让所有源白扫。现在捕获、记录、标 `error` 后继续。
2. **价格回算失败会让采集整体抛错** —— 数据已经入库，却因为回算崩掉而被当成失败。现在跳过并在日志里写「下次自动补算」。
3. **自检里两个同名 `test_discovery`** —— 后一个覆盖前一个，7 项断言从来没执行过。重命名后全部执行，自检项数 74。
4. **`/api/diagnostics` 少一个导入** —— 由 ruff 的 F821 抓到，属于一请求就 500 的错。
5. **遍历不跳过噪声目录** —— `iter_files` 现在跳过 `.git`、`node_modules`、`__pycache__`、`.venv` 等。

## 数据库迁移

`PRAGMA user_version` 从无到 3：

- v2：`usage_event` 补 `cost_src`
- v3：建 `idx_credit_client`，并清掉早期试做留下的重复索引

旧库打开即升级，幂等，不丢数据。损坏的库仍然改名成 `*.corrupt-时间戳.db` 保留，不直接删。

## 下载

- `TokenPanel.exe`（Windows 10/11 x64，单文件，10.7 MB）
- `SHA256SUMS.txt`
- `verify.bat`

SHA256：`69affd44c1faedb8c86c2731a4f192a344a35625993725cbb80b317722f59150`
