# TokenPanel v0.3.0 — 技术动态全量重做

「技术动态」这一栏从 4 个源扩到 **26 个**，并且第一次把论文正经纳进来。

## 现在覆盖什么

**大模型厂商（6）**

Anthropic、Kimi / Moonshot、MiniMax、DeepSeek、OpenAI、Qwen。

其中 Anthropic、Kimi、MiniMax、DeepSeek 官网没有 RSS，这次改成解析官方列表页：只认「路径前缀 + 像标题的文本」，不依赖具体 class 名，站点改版也不会立刻失效。

**研究与工程（15）**

NVIDIA、Microsoft Research、Google DeepMind、Google AI、Meta AI、Mistral、xAI、Cohere、Hugging Face、Together AI、MIT News、Apple ML、TensorFlow、AWS ML、Perplexity。

**论文（5）**

- arXiv cs.CL 最新 60 篇
- arXiv cs.LG 最新 60 篇
- 国产模型论文：按型号名检索 Kimi K2、MiniMax-M2、DeepSeek-V3 / R1、Qwen3、Hunyuan、GLM-4.5、ERNIE
- 海外模型论文：Claude、Gemini、GPT-5、Llama、Mistral、Grok-4
- HF Daily Papers（海外，常不可达）

论文卡片会自动标注机构。裸 `minimax` 会撞上数学里的 minimax，所以只认 `MiniMax-M` 这种型号名——宁可漏，不可错。

## 界面变化

- 顶部四条统计：总条目 / 官方博客 / 论文 / 来源在线
- **厂商最新**：Anthropic、Kimi、MiniMax、DeepSeek、OpenAI、Qwen 各取最新一篇常驻首屏，优先挑技术类标题（避开 IPO、招聘这类公告）
- 全部 / 博客 / 论文三段切换
- 来源筛选 chips 带品牌色标，离线源标红并折叠成一行
- 列表默认 60 条，「加载更多」每次 +60
- 卡片来源徽章带品牌色竖线，论文卡片有 PAPER 徽标与机构标签

## 抓取怎么保证不崩

- **硬预算**：主源 13 秒 + 海外源 5 秒 + 失败重试 6 秒。单个慢源（旧版 BAIR 要 44 秒）不会再把整次刷新拖死
- **失败隔离**：一个源挂了不影响别的源；失败的源保留上次抓到的内容，状态里写「离线」
- **增量合并**：按 URL 去重，缓存上限 1000 条，并为小源留足空间，不会被 OpenAI 的 1400 条挤掉
- **摘要补全**：列表页不给摘要时回访详情页取 `og:description` 或正文首段；识别并丢弃「Anthropic is an AI safety and research company…」这种站点通用描述，绝不拿它冒充每篇文章的摘要
- **清洗**：HTML 实体解码、标题尾部日期剪除、`/news/` 栏目主页不会被当成文章、导航段落不会被当成摘要
- **排序**：先按天分组，同一天内官方博客排在论文前面，否则首屏会被 arXiv 日更淹没

## 诚实的地方

海外源（DeepMind、Meta、Mistral、xAI、Cohere、HF、Perplexity、TensorFlow、AWS）在国内网络下经常连不上。界面会照实显示「17 / 26 个来源在线」，而不是假装全部成功。

米哈游目前没有公开的 AI 技术博客（`tech.mihoyo.com` 长期 502），arXiv 上也没有 miHoYo / HoYoverse 署名的论文，因此没有单独成源。一旦出现，机构识别规则里已经预留了 `mihoyo` / `hoyoverse`。

## 自检

新增 4 项断言：官网列表页提取、论文机构识别、增量合并不丢内容、同日博客优先。合计 **75 项**，全部通过。

## 下载

- `TokenPanel.exe`（Windows 10/11 x64，单文件，10.7 MB）
- `SHA256SUMS.txt`
- `verify.bat`

SHA256：`4e87e10b7f432c04bbea8ae6b806ca9d266a5607f2e8852ae081281d4fdd0d30`
