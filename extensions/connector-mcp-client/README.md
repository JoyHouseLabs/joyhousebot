# JoyhouseBot MCP Client Connector

可选的外部 MCP Server 客户端连接器。它在 Worker 启动时连接显式配置的 MCP Server，
把远端 Tool 注册到 JoyhouseBot 的统一 Capability Registry；调用仍经过 allowlist、审批、
Action、审计和 Run/Task 链路。

```bash
uv pip install -e extensions/connector-mcp-client
```

新配置使用 `extensions.enabled = ["connector-mcp-client"]` 和
`extensions.settings.connector-mcp-client.servers`。不存在旧配置翻译入口。

HTTP 连接强制经过 Core SSRF 防护。stdio 会启动本机子进程，默认关闭；只有明确设置
`allow_stdio: true` 才可使用，并应只在受信任的本地部署中启用。
