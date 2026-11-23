# TokenPanel v0.2.1 — 换台电脑也能自己找到数据

这一版解决「把 exe 发给别人、他电脑上什么都没显示」的问题。

## 三个以前会漏掉的情况

**1. 客户端装在非常规位置**

以前每个数据源只认一条写死的路径。现在每个源都有多个候选，按顺序取第一个真实存在的：

| 平台 | opencode 的候选 |
|---|---|
| Windows | `%APPDATA%\opencode`、`~/.local/share/opencode`、`~/.opencode` |
| macOS | `~/Library/Application Support/opencode` |
| Linux | `~/.local/share/opencode` |

Windows / macOS / Linux 三平台的路径规则都写进了候选列表。

**2. 出现了我没适配过的新客户端**

以前靠名字匹配（claude / codex / qoder…），名字不在表里就直接跳过。现在加了**内容级识别**：

- 目录里有 `sessions/` `projects/` 之类的结构
- 且里面的 jsonl 文件**真的出现** `usage` / `input_tokens` / `total_tokens` 等字段

两条都满足才认定为可读来源，交给通用探测器读取。

**3. 什么都没找到时，用户不知道为什么**

现在首屏会直接说明，并把检查过的位置逐条列出来：

```
还没有找到可统计的数据
程序已自动检查本机 8 个位置，其中 0 个存在。若你刚安装客户端，
先运行一次让它产生会话记录，再点右上角「重新扫描」。
  未找到  C:\Users\xxx\.claude\projects
  未找到  C:\Users\xxx\.codex\sessions
  ...
客户端装在非默认位置时，可用环境变量 TP_GENERIC_DIRS 指定。
```

有数据时，这个位置换成「本机检测」列表，显示每个来源的路径与状态（已接入 / 通用读取 / 仅发现）。

## 误报是怎么压下去的

一开始按「有 jsonl 就算」判断，结果 Electron 缓存、网盘客户端、clash 全被当成了 AI 客户端——40 多个假来源。

三层收紧：

1. **内容校验**：读文件开头，必须出现 usage 类字段
2. **sqlite 精度**：必须同时有输入/输出 token 列；只有一个 `token` 列的（多为 API Key 表）不算
3. **数量上限**：陌生来源最多 8 个，按最近修改时间排序

现在这台机器上从 40+ 降到 2 个（`~/.qoder` 与 `~/.qoderworkcn`，都是真实含用量数据的）。

## 顺带修的两个 bug

- `_has_usage_signal` 返回 `(bool, evidence)` 元组，非空元组恒为真，导致校验形同虚设——这个 bug 让前面所有收紧都失效过
- 通用探测器会去扫纯日志目录，白跑；现在只扫判定为可读的目录

## 验证

**沙盒实测（模拟新电脑）**：只放一个陌生客户端 `~/.brandnewai/sessions/*.jsonl`，内容含 usage 字段。

```
发现的来源: [('generic:.brandnewai', False, True)]
扫描结果: generic → 新增 1 条
入库记录: [('generic:.brandnewai', 'mystery-model-v1', 1200, 300, 800)]
```

1200 输入 / 300 输出 / 800 缓存读，全部正确。同时确认：

- 只有普通 jsonl（无 usage）的目录 → 不收录
- 只有 `token` 列的凭据库 → 不收录
- 空环境 → calls = 0，界面显示引导

自检 **71 项全通过**。

## 升级

直接覆盖旧版 `TokenPanel.exe`。首次启动会自动重新探测本机，不需要手动配置。
