# joyhousebot Telegram Channel

可选 Telegram long-polling Channel 扩展。Telegram SDK、消息格式、媒体下载、reaction 和 typing
语义位于本包；Run、Outbox、Lease、重试和审计仍由 Core 负责。

```bash
uv pip install -e extensions/channel-telegram
```

安装后显式启用 `channel-telegram`；Bot Token 必须使用 `env://VARIABLE`。音频转写不是 Channel
职责，请另行安装并授权转写 Capability/Provider 扩展。
