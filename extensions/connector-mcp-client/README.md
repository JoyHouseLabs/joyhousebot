# Porthouse MCP Client Connector

可选的外部 MCP Server 客户端连接器。它在 Worker 启动时连接显式配置的 MCP Server，
把远端 Tool 注册到 Porthouse 的统一 Capability Registry；调用仍经过 allowlist、审批、
Action、审计和 Run/Task 链路。

```bash
uv pip install -e extensions/connector-mcp-client
```

新配置将 `connector-mcp-client` 加入 `extensions.allowedIds`，首次安装时如需自动激活再加入
`extensions.initiallyActive`，并使用 `extensions.settings.connector-mcp-client.servers` 配置 MCP
Server。`extensions.enabled` 仅为旧部署迁移兼容输入。

HTTP 连接强制经过 Core SSRF 防护。stdio 会启动本机子进程，默认关闭；只有明确设置
`allow_stdio: true` 才可使用，并应只在受信任的本地部署中启用。
