# 媒体与公众号内容采集

状态：Implemented baseline（2026-08-16）

Porthouse 将“外部内容如何取得”和“Agent 如何使用内容”分开：Connector/Capability 只产生可核验的
来源内容；Skill、Workflow 或 App 再决定筛选、分析、入库、提醒和产出。这避免把站点抓取逻辑、用户
浏览器会话或产品工作流放进 Core。

## 1. 路由选择

| 来源 | 首选实现 | 回退 | 不使用 |
| --- | --- | --- | --- |
| 媒体 RSS/Atom、公开 API、sitemap | `capability-media-monitor` 的 Python HTTP 路径 | 经审核的站点专用 Host | 任意网页爬虫/模型给出的 URL |
| 公众号公开历史文章 | `opencli.weixin.search` 找到候选来源，`opencli.weixin.download` 提取 Markdown | 用户粘贴具体文章 URL 后用 Context Assets 导入 | 未审核的通用 Browser 自动化 |
| 自有公众号草稿/发布 | 单独的写能力、审批和人工发布确认 | 官方 API（具有授权时） | 复用只读历史采集能力 |

`capability-opencli` 当前冻结目录新增的只有 `weixin search` 和 `weixin download` 两项读取能力。
它们必须使用明确的本机 `browser_profile_ref`；Cookie 和 Chrome Profile 不离开设备。下载图片被能力
调用显式关闭，最多把一个 512 KiB Markdown 文档作为 Runtime Artifact 回传，绝不把宿主机路径、整个
浏览器 profile 或图片目录返回给 Runtime。

## 2. 媒体增量闭环

```text
Schedule / Agent Monitor
  → media.feed.read(feed_url, cursor)
  → new_entries + next_cursor
  → monitor_scratch.update(next_cursor)
  → 需要全文时：Context Assets URL ingestion
  → Artifact / Knowledge → Skill 形成简报、机会或提醒
```

`media.feed.read` 使用 ETag、Last-Modified 和最多 500 个 source entry id 构成不透明 cursor。它不创建
扩展表、不另起 scheduler，也不替代 Porthouse 的 Run/Task/Trace。每一次读取仍在正常 Capability
Invocation 与审计链中。

## 3. 部署与授权

Python 媒体能力要进入 `extensions.allowedIds`，再在 Console 激活；Agent 需显式拥有
`network.http.read` 及 `media.feed.read` 工具许可。OpenCLI 微信能力是 Node Host 的冻结 Release，不能
通过 Console 安装 npm 包或任意增加命令；更新上游 OpenCLI、allowlist 或收集策略必须重新构建 Catalog、
形成新的 Host Revision、通过 preflight 后再发布。

对无 RSS/API 的媒体，请先建立来源专用、只读的 OpenCLI Catalog 条目，并明确域名、参数、浏览器 profile、
频率和失败处理。不要将其降级为通用的 Python 页面抓取器。
