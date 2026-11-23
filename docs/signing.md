# 代码签名与完整性校验

> 这份文档说明 TokenPanel 的签名现状、你可以怎样验证下载的文件没被动过，以及为什么「买一张证书」并不能像很多人以为的那样消掉 Windows 的拦截弹窗。

## 当前状态

**TokenPanel 目前的 exe 没有 Authenticode 代码签名。**

```
> Get-AuthenticodeSignature .\TokenPanel.exe
Status: NotSigned
```

原因是公开渠道已经不存在可用的免费可信证书：

- 自 2023 年 6 月起，CA/Browser Forum 要求**所有**代码签名证书（OV 和 EV 一样）的私钥必须存放在符合 FIPS 140-2 Level 2 的硬件令牌或合规云 HSM 中。这直接终结了"下载一个 .pfx 文件就能签名"的时代。
- 自 2026 年 3 月起，公开可信代码签名证书的最长有效期被限制为 460 天，成本进一步上升。
- 自签名证书完全免费，但 Windows 对它的态度与"完全没有签名"完全一致，反而更容易被认为是可疑文件。**本项目不会使用自签名证书**，因为它给不了任何实际保护。

## 为什么 Windows 会弹「Windows 已保护你的电脑」

这里有三件常被混为一谈的事：

| 事实 | 说明 |
|---|---|
| 未签名 → 显示「未知发布者」 | 弹窗文案最难看的那个分支 |
| **签名了 → 首次下载照样弹** | 只是发布者名字从「未知」变成你的名字 |
| 信誉是按「文件哈希 + 证书」累积的 | 需要数周时间、数百次来自不同用户的干净安装 |

微软官方文档《[SmartScreen reputation for Windows app developers](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)》明确写着：

> Note: EV certificates no longer bypass SmartScreen. Years ago, signing files with an Extended Validation (EV) code signing certificate would result in positive SmartScreen reputation by default, but this behavior no longer exists.

也就是说：**花钱买 EV 证书来"秒过"SmartScreen 这条路已经不存在了。** 现在 OV 和 EV 的初始表现完全一样，都是先弹窗、再靠真实下载量慢慢建立信誉。唯一能完全避免下载弹窗的官方渠道是 Microsoft Store——因为商店分发的应用由微软重新签名。

## 你现在能做的三件事

### 1. 校验文件完整性（最重要）

比对 SHA256 是唯一在你本地就能完成的、真正有意义的验证。任何人对 exe 改一个字节，哈希都会完全不同。

**Windows（最简单）**：把 `TokenPanel.exe`、`SHA256SUMS.txt`、`verify.bat` 放在同一个文件夹，双击 `verify.bat`。

**Windows 手动命令**：

```powershell
Get-FileHash .\TokenPanel.exe -Algorithm SHA256
# 与 SHA256SUMS.txt 里的值逐字比对
```

**macOS / Linux**：

```bash
sha256sum -c SHA256SUMS.txt
```

**当前发布版本的校验值**（v0.1.6，10,775,258 字节）：

```
ef30ff74568fd208ed0dcae2bcd6384406c03f2d50b03f1ae6f67f580dcec223  TokenPanel.exe
```

如果哈希对不上，**不要运行这个文件**，删掉重新从官方发布页下载。

### 2. 去掉首次运行的拦截弹窗

如果你已经核对过哈希、确认文件没问题，可以通过「解除锁定」去掉那个弹窗：

1. 右键 `TokenPanel.exe` → **属性**
2. 「常规」标签页最下方勾选 **解除锁定 / Unblock**
3. 确定，再双击运行

或者命令行：

```powershell
Unblock-File .\TokenPanel.exe
```

**这一步在做什么**：Windows 会给从网络下载的文件打上"来自 Internet"的标记（Mark-of-the-Web）。SmartScreen 针对下载文件的拦截主要依赖这个标记。解除锁定只是去掉这个来源标记，不修改文件内容、不关闭任何安全功能、不修改注册表。

**顺序很重要**：先校验哈希，再解除锁定。反过来就失去了验证的意义。

### 3. 自己从源码构建

这是最彻底的信任方式——你亲自看过代码、亲自编译：

```bash
git clone https://github.com/hhxpoint/tokenpanel
cd tokenpanel
pip install -r requirements.txt
python -m tokenpanel selftest   # 49 项自检，全绿说明逻辑正常
python -m tokenpanel desktop    # 直接跑，不用打包
```

源码运行完全不需要 exe，也不会有任何弹窗。

## 代码签名策略（Code signing policy）

本项目对所有发布产物承诺：

1. **只发布从本仓库源码构建的产物。** 不接受第三方提交的二进制。
2. **每个 Release 都附带 `SHA256SUMS.txt`**，且校验值必须与上传的资产一致。
3. **发布前必须通过自检**（`python -m tokenpanel selftest`，49 项断言）与 CI 三平台矩阵。
4. **如果未来获得代码签名证书**，将对所有可执行文件（主程序、安装包、更新包）统一签名，并**必须添加可信时间戳**——否则证书过期后历史版本会全部失效。
5. **每次签名请求需由仓库维护者本人确认**，不做无人值守的自动签名。
6. 不签任何包含恶意行为、捆绑推广或未经用户同意的数据收集的产物。

## 免费方案评估（2026-11 核实）

| 方案 | 费用 | 硬门槛 | 发布者显示 | 能否消掉弹窗 |
|---|---|---|---|---|
| **SignPath Foundation** | 免费 | OSI 许可证、公开活跃维护、已有 Release、无专有组件、全员 MFA、需 Author/Reviewer/Approver 三角色、每版人工审批 | `SignPath Foundation`（不是项目作者） | 不能立即消除，但签名后可按证书累积信誉 |
| **Certum 开源证书** | 免费（原价 €329/年） | GitHub 托管、**Star ≥ 1000**、至少 1 个 Release、申请人中国国籍 | 项目作者本人 | 同上 |
| **Azure Artifact Signing**（原 Trusted Signing） | $9.99/月 | **个人开发者仅限美国/加拿大**；需付费 Azure 订阅与身份核验 | 本人/组织 | 同上 |
| **商业 OV 证书** | $130–300/年 + 硬件令牌或云签名 | 企业或个人身份核验 | 本人/组织 | 同上 |
| **Microsoft Store** | 免费（个人账号） | 需按 MSIX 打包并通过商店审核 | 本人 | **完全不弹窗**（微软重签） |
| **自签名证书** | 免费 | 无 | 仍显示「未知发布者」 | **完全无效**，本项目不采用 |

**结论**：目前没有任何一条路径能"免费 + 立刻消除弹窗"。能免费拿到可信签名的两条路（SignPath、Certum）都需要时间或社区规模；而 Store 虽然零弹窗，但分发方式完全不同。

因此本项目的取舍是：**把透明度做到位**——公开校验值、提供校验脚本、把签名现状和原因写清楚，而不是用一张没用的自签名证书制造"已签名"的错觉。

## 参考

- [SmartScreen reputation for Windows app developers — Microsoft Learn](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)
- [Code signing options for Windows app developers — Microsoft Learn](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options)
- [SignPath Foundation 开源签名条件](https://signpath.org/terms.html)
- [Certum 开源代码签名证书](https://certumcodesign.cn/shop/product/open-source-code-signing-cloud/)
