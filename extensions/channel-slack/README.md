# joyhousebot Slack Channel

可选 Slack Socket Mode Channel 扩展。Slack SDK、线程和 reaction 语义位于本包；Run、Outbox、Lease、
重试和审计仍由 Core 负责。

```bash
uv pip install -e extensions/channel-slack
```

安装后显式启用 `channel-slack`；Bot Token 和 App Token 必须使用 `env://VARIABLE`。
