# TokenPanel v0.1.0

免安装的单文件客户端。**下载 `TokenPanel.exe`，双击即可。**

## 它能做什么

自动扫描本机所有 AI 客户端的本地日志，把 token 用量汇总成一个面板：

| 已支持量化 | 方式 |
|---|---|
| Claude Code | 逐次调用，含缓存读写与 thinking |
| dsh（DeepSeek） | 逐次调用，含缓存读写 |
| OpenCode | 逐次调用，原生 cost |
| Codex CLI | 逐回合 |
| WorkBuddy | 会话级快照 + credit |
| 其他任意客户端 | 通用探测器：日志里有 token 字段就能识别 |

已在实测机器上自动发现 11 类 harness，其中 6 类可量化。

## 怎么用

1. 把 `TokenPanel.exe` 放到任意文件夹
2. 双击
3. 等 5–15 秒，弹出面板

数据保存在 exe 同目录的 `data\tokenpanel.db`，连同 `pricing.json` 一起拷走就是完整的便携部署。删除 `data\` 会自动重建。

> **首次运行时 Windows 可能提示「Windows 已保护你的电脑」**
> 这是未做代码签名（个人项目无证书）导致的，不是文件有问题。
> 点「更多信息」→「仍要运行」即可。

## 不需要什么

- 不需要安装 Python
- 不需要命令行
- 不需要联网（首次启动后完全离线可用）
- 不写注册表、不改 PATH、不装服务

## 隐私

只读打开本地日志与 SQLite（`mode=ro`），不发任何网络请求，数据不出机器。

## 已知边界

- **仅 Windows 10/11 x64**。macOS / Linux 请从源码运行
- **Qoder** 仅部分会话含 usage 字段，其余需在 Qoder 后台查看
- **Cursor / Trae / Windsurf / Copilot** 用量在云端，本地无逐次记录，面板会如实标注「仅发现」而不是估算
- **未定价模型** 的金额显示为「—」。在 `pricing.json` 补单价后点「重新扫描」即可

## 从源码运行

```bash
pip install -r requirements.txt
python -m tokenpanel doctor
python -m tokenpanel desktop
```

完整说明见 [README](https://github.com/hhxpoint/tokenpanel#readme)。
