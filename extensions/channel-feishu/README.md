# joyhousebot Feishu Channel

可选 Feishu/Lark WebSocket Channel 扩展。供应商 SDK 和消息卡片转换位于本包，持久执行、投递 Outbox、
租约、重试和审计由 joyhousebot Core 负责。

```bash
uv pip install -e extensions/channel-feishu
```

安装后显式启用 `channel-feishu`；所有 secret/token 字段必须使用 `env://VARIABLE`。
