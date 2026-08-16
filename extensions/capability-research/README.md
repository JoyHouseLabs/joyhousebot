# Porthouse Research Capabilities

可选研究能力包，提供版本化 `web_search` 与 `web_fetch`。Brave Search、Readability 和网页
抽取属于本扩展；Core 只提供网络追踪、SSRF 防护、权限、配额和调用审计。

```bash
uv pip install -e extensions/capability-research
```

设置 `BRAVE_API_KEY` 后启用 `web_search`。Agent 必须获得 `network.search` 或
`network.http.read` 权限。`web_fetch` 仍通过 Core 提供的 SSRF-safe transport，禁止访问本地、
元数据和解析后落入私网的地址。
