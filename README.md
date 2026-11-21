# TokenPanel

**自动检索本机所有 AI 客户端，把散落在各处的 token 用量汇总成一个可读的数字。**

![面板截图](docs/screenshot.png)

你同时用着 Claude Code、Codex、OpenCode、dsh、Qoder、WorkBuddy……每家的用量数据都躺在自己的目录里，格式互不兼容，官方面板各看各的。TokenPanel 把它们统一采集、去重、按同一口径量化，给出一个真实的总额：**这个月你到底烧了多少 token，花了多少钱。**

---

## 特性

- **自动发现** — 扫描 `~`、`AppData`、`~/.config`、`~/Library/Application Support`，按目录结构与文件指纹识别 harness，不依赖写死的清单
- **通用探测** — 即使某个客户端没有专门适配，只要日志里带 token 字段，也能被自动识别并纳入统计
- **中文计量** — 所有数字按「万 / 亿」展示，不再对着 `1,043,270,912` 数零
- **逐次精确** — 能拿到逐条 usage 的，就精确到每一次 API 调用，含缓存读写与推理 token
- **零上报** — 只读本地文件，不发任何网络请求，数据不出机器
- **零前端依赖** — 原生 JS + SVG 手绘图表，无 CDN、无构建步骤，断网可用
- **可降级** — FastAPI 未安装时自动切换标准库 HTTP 服务，接口完全一致
- **一键上手** — `setup.bat` 装依赖、`doctor` 体检，缺什么会明说而不是静默失败
- **自检** — 28 项内建断言覆盖计量、解析、去重、计价，升级后可一键回归

## 支持的客户端

自动发现 11 类 harness，其中 6 类可量化：

| 客户端 | 数据源 | 采集粒度 | 成本来源 |
|---|---|---|---|
| **Claude Code** | `~/.claude/projects/**/*.jsonl` | 逐次调用（含缓存读写、thinking） | 价格表回算 |
| **dsh**（DeepSeek） | `~/.dsh/sessions/**/session.v4.jsonl.zstd` | 逐次调用（含缓存读写） | 价格表回算 |
| **OpenCode** | `~/.local/share/opencode/opencode.db` | 逐次调用 | 原生 `cost` 字段 |
| **Codex CLI** | `~/.codex/sessions/**/rollout-*.jsonl.zst` | 逐回合 | 价格表回算 |
| **WorkBuddy** | `~/.workbuddy/workbuddy.db` → `session_usage` | 会话级快照 | 原生 credit |
| **通用探测器** | 其余任意含 usage 的 jsonl / jsonl.zst / jsonl.gz | 逐次调用 | 价格表回算 |

> Qoder 就属于最后一行：它的常规会话日志没有 usage，但**部分**会话里确实记了 token。通用探测器把这 6 条记录捞了出来——这是靠专门适配发现不了的。

以下客户端会被自动发现，但**本地确实没有 token 记录**，因此如实标注为「仅发现」，不做估算：

| 客户端 | 原因 |
|---|---|
| Qoder（其余会话） | 日志无 `usage` 字段 |
| Trae / Cursor / Windsurf | 用量在云端 Dashboard |
| Copilot（CLI + VS Code 扩展） | 本地只有会话索引 |
| CodeBuddy | 日志未写入 usage |
| Marvis | 日志未写入 usage |

> 估算出来的假数字比没有数字更糟。宁可显示「不做」，也不猜。

## 快速开始

### Windows：三步用上

| 步骤 | 做什么 | 预期结果 |
|---|---|---|
| 1 | 双击 **`setup.bat`** | 自动检查 Python、安装 `zstandard`、跑环境体检 |
| 2 | 双击 **`TokenPanel.vbs`** | 弹出一个无地址栏的独立窗口，自动开始扫描 |
| 3 | 等 5–15 秒 | 看到本机所有客户端的汇总数据 |

`setup.bat` 只做三件事：确认 Python ≥ 3.10、`pip install zstandard`、打印体检结果。它不会改系统环境，可以重复运行。

启动脚本自己也会体检：缺 Python 会弹窗引导装，缺 `zstandard` 会问你要不要跑 `setup.bat`，不会静默失败。

### 没有 Python？

1. 到 https://www.python.org/downloads/ 下载 3.10 或更高版本
2. 安装时**务必勾选 “Add Python to PATH”**
3. 重新双击 `setup.bat`

### 首次运行的预期

- 首次扫描较慢（5–15 秒，取决于日志量），之后增量扫描 1–3 秒
- 只读本地文件，不联网、不上传
- 数据落在 `data/tokenpanel.db`，删掉会自动重建

### macOS / Linux

`.vbs` / `.bat` 是 Windows 专用，其他平台走命令行：

```bash
pip install -r requirements.txt
python -m tokenpanel doctor      # 先体检
python -m tokenpanel scan        # 采集
python -m tokenpanel report      # 终端看结果
python -m tokenpanel serve       # 浏览器打开 http://127.0.0.1:8765
```

## 界面怎么看

| 区块 | 含义 |
|---|---|
| **顶部四个数字** | 所选区间内的总量、缓存命中率、平均每次调用、活跃情况 |
| **总账条** | 输入 / 输出 / 缓存读 / 缓存写 / 推理的分项拆分 |
| **每日用量趋势** | 按客户端堆叠的柱状图，柱子上悬停看当天明细 |
| **客户端占比** | 各来源在所选区间内的份额与调用次数 |
| **近 90 天活跃度** | 每格代表一天，颜色越深消耗越高，悬停看具体数值 |
| **WorkBuddy 计费快照** | 会话级 credit 与上下文占用，**不可跨会话累加** |
| **模型用量 / 项目用量** | 按 token 排序，快速定位哪个模型或项目在烧钱 |
| **最近会话** | 按最近活动排序，可看出哪次会话量级异常 |
| **数据源状态** | 每个来源的采集条数与时间；未定价模型会在这里列出 |

右上角可在 **7 天 / 30 天 / 90 天 / 1 年 / 全部** 之间切换，也可以把计量方式从 **Token** 切到 **成本**。

## 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| 双击 vbs 没反应 | Python 不在 PATH | 跑 `setup.bat`，按提示装 Python 并勾选 Add to PATH |
| 打开后一片空白，提示无法连接 | 服务没起来或端口被占 | 双击 `TokenPanel.bat` 看控制台报错；或 `python -m tokenpanel doctor` |
| dsh / Codex 显示 0 条 | 缺 `zstandard` | 跑 `setup.bat`，然后点面板右上角「重新扫描」 |
| 某个客户端完全没出现 | 本机没有这个客户端，或它不记 token | 运行 `python -m tokenpanel discover` 确认是否被识别 |
| 金额全是「—」 | 模型不在价格表里 | 编辑 `pricing.json` 补单价，再 `scan --recalc` |
| 数字比官方少 | 官方可能含订阅内额度或未落盘的调用 | 以面板的「未定价/未采集」提示为准，两者口径不同 |
| 端口 8765 被占用 | 已经有一个实例在跑 | 直接双击 vbs 会复用它；或 `serve --port 8766` |

## 换机器 / 备份

- **要带走数据**：复制 `data/tokenpanel.db`
- **要带走价格配置**：复制 `pricing.json`
- **不要复制**：`__pycache__`、`dist/`、`build/`
- 换机器后重新跑 `setup.bat` 即可，数据会自动重建（也可以手动覆盖 db）

## 验证是否装好了

```bash
python -m tokenpanel doctor      # 环境体检，全绿即可用
python -m tokenpanel selftest    # 28 项逻辑自检
python -m tokenpanel scan        # 能打印「新增 N 条」即正常
```

## 命令行全量命令

```bash
python -m tokenpanel discover           # 检索本机 harness
python -m tokenpanel discover --deep    # 附带各目录体积
python -m tokenpanel scan               # 增量采集
python -m tokenpanel scan --force --recalc    # 全量重扫 + 按新价格重算
python -m tokenpanel report --days 30   # 终端报表
python -m tokenpanel doctor             # 环境体检
python -m tokenpanel selftest           # 内建自检（28 项）
python -m tokenpanel desktop            # 桌面窗口
python -m tokenpanel serve --port 8765  # 仅启动网页服务
```

终端报表长这样：

```
=== 全量总账（累计） ===
客户端              调用       输入       输出      缓存读      缓存写       合计         成本
dsh            4067   924.7万   504.8万   13.80亿        0   13.94亿     $43.34
OpenCode       5983    1.48亿   299.5万    4.52亿    1.18万    6.03亿      $0.78
通用·qoder         6   179.1万    7.36万   178.3万        0   364.8万          -
Claude Code      15       44   24.32万   38.18万   20.12万   82.62万     $22.58
Codex             2     6285      378    1.42万        0    2.09万          -
合计                     1.59亿   836.0万   18.34亿   21.31万   20.01亿     $66.70

总计 20.01亿 tokens：输入 1.59亿 · 输出 836.0万 · 缓存读 18.34亿 · 缓存写 21.31万 · 推理 68.05万
```

## 泛化机制

新客户端层出不穷，逐个写适配器永远追不上。TokenPanel 用三层策略应对：

1. **已知位置表** — 对格式稳定的客户端走专用 adapter，精度最高
2. **结构指纹识别** — 对陌生目录检查「有没有 `projects/` 或 `sessions/`」「sqlite 里有没有 token 字段」，判断是否值得采集
3. **通用 JSONL 探测器** — 兜底方案：递归查找任意嵌套结构里同时含输入/输出 token 的节点

通用探测器识别的字段名覆盖：

```
input  → input_tokens / inputTokens / prompt_tokens / promptTokens / promptTokenCount
output → output_tokens / outputTokens / completion_tokens / completionTokens / candidatesTokenCount
cache  → cache_read_input_tokens / cacheReadTokens / cached_tokens / cache_read
```

为避免误采和拖慢主流程，它有三道约束：

- 只扫「发现但未专门适配」的目录，专用 adapter 的路径天然排除，不会重复计数
- 前置双关键词正则，一行里必须同时出现输入和输出字段名才解析 JSON
- 文件数上限 1200、总预算 6 秒、单文件 512MB 上限，大文件仍可被时间片打断

也可以手动指定额外目录：

```bash
TP_GENERIC_DIRS="D:/some-agent/logs" python -m tokenpanel scan
TP_DISABLE_GENERIC=1 python -m tokenpanel scan   # 完全关闭通用探测
```

## 计量口径

这是本项目最在意的部分。三类数据进入两张不同的表，**绝不混算**：

| 表 | 语义 | 累加规则 |
|---|---|---|
| `usage_event` | 一次模型调用一行 | 可累加 |
| `credit_snapshot` | 会话级累计值（WorkBuddy） | 只覆盖，不可累加 |

几个容易踩的坑，已处理：

- **Claude Code 流式重复**：同一 `message.id` 会写成多行，按 id 去重取最完整的一条
- **Codex 累计值陷阱**：用 `last_token_usage` 而非 `total_token_usage`，且 `input_tokens` 已含缓存，需相减
- **dsh 多帧 zstd**：必须流式解压，一次性 `decompress` 只能读到第一帧
- **Codex 文件后缀**：新版是 `.jsonl.zst`，不是 `.jsonl`
- **dsh 的 cwd 在顶层**：不在 `data` 里，取错就是全项目归属丢失
- **免费模型**：模型名含 `free` 的直接按 0 计价
- **未定价模型**：显示「—」，不计入金额，并在面板列出待补清单

## 定价

编辑根目录 `pricing.json`，单位为 **USD / 1M tokens**：

```json
{
  "models": {
    "claude-opus":    { "input": 15.0, "output": 75.0, "cache_read": 1.5,   "cache_write": 18.75 },
    "deepseek-flash": { "input": 0.28, "output": 0.42, "cache_read": 0.028, "cache_write": 0.28 }
  }
}
```

匹配顺序：免费模型 → 精确 → 前缀 → 包含。改完执行 `python -m tokenpanel scan --recalc`。

## 自检

```bash
python -m tokenpanel selftest
```

覆盖 8 组共 28 项断言：中文计量、时间戳解析、价格匹配、路径归并、通用探测器、环境指纹、存储语义、Codex 净输入算法。退出码 0 即通过，可直接接 CI。

## 架构

```
adapters/          每个客户端一个采集器，输出统一 schema
  claude.py  dsh.py  codex.py  opencode.py  workbuddy.py  generic.py
        │
discovery.py  ── 跨平台目录扫描 + 结构指纹识别
        │
ingest.py  ── 文件指纹增量 + 主键去重
        │
    SQLite  ── usage_event（可累加） / credit_snapshot（覆盖）
        │
pricing.py ── 价格表回算，未命中留空
        │
server_fallback.py（标准库） / server.py（FastAPI）
        │
   web/index.html ── 原生 JS + SVG，无 CDN
```

前端交互细节：数字滚动、柱状图生长、热力图渐显、悬停抬升，并完整适配 `prefers-reduced-motion`。

新增一个客户端只需实现 `adapters/base.py` 的 `Adapter`，在 `ingest.py` 注册，其余自动生效。

## 隐私

- 只读打开本地日志与数据库（sqlite 使用 `mode=ro`），不修改原始数据
- 无任何远程请求；页面只访问 `127.0.0.1`
- `data/` 是纯派生数据，可随时删除重建

## 开发

```bash
python -m tokenpanel scan
python -m tokenpanel report
python -m tokenpanel selftest
python -m tokenpanel serve --port 8765
```

可选安装 FastAPI（更快，但非必需）：

```bash
pip install fastapi uvicorn
```

## License

MIT
