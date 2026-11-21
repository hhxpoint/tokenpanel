# TokenPanel v0.1.3

**下载 `TokenPanel.exe`，双击即用。**

## 这版改了什么

### 数据文件损坏不再崩溃

如果 `data\tokenpanel.db` 因为断电、磁盘错误或误操作损坏，之前会直接报错打不开。现在会自动把坏文件改名保留（`tokenpanel.corrupt-时间戳.db`），然后重建一个空库，程序照常可用。

### 放错位置会说清楚

如果把 exe 放进 `C:\Program Files` 这类没有写权限的目录，之前是静默失败。现在会弹系统提示框，明确告诉你「无法写入数据目录，请把 exe 移到桌面或文档」。

### 连续点击不会重复扫描

点「重新扫描」时如果上一轮还没跑完，现在会排队而不是并发执行，避免数据库写入冲突。

### 中文在非 UTF-8 控制台不再崩

Windows 默认代码页（GBK/cp936）无法编码部分符号，之前用命令行跑 `selftest`、`doctor` 在非 UTF-8 终端会直接抛 `UnicodeEncodeError`。现在入口统一强制 UTF-8 输出。

### 每次推送自动验证

新增 GitHub Actions：Ubuntu（Python 3.10 / 3.12）+ Windows（3.12）三组矩阵，每次提交自动跑 38 项自检 + 编译检查。README 顶部有 CI 状态徽章。

> 这轮 CI 第一次跑就抓到了上面那个编码问题——本地 Git Bash 是 UTF-8，一直没暴露。

## 怎么用

1. 把 `TokenPanel.exe` 放到任意有写权限的文件夹
2. 双击
3. 秒开界面，十几秒后数据补齐

> **首次运行 Windows 可能提示「Windows 已保护你的电脑」**
> 未做代码签名（个人项目无证书），点「更多信息」→「仍要运行」即可。

## 隐私

只读本地日志与 SQLite（`mode=ro`）。唯一联网行为是你主动点击「检查更新」时的一次版本查询。

## 已知边界

- **仅 Windows 10/11 x64**；macOS / Linux 请从源码运行
- **Qoder** 仅部分会话含 usage 字段
- **Cursor / Trae / Windsurf / Copilot** 用量在云端，本地无逐次记录
- **未定价模型** 金额显示为「—」，在 `pricing.json` 补单价后重新扫描即可
