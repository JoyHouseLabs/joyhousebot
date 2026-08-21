# joyhousebot QQ Channel

可选 QQ Channel 扩展。它只负责 QQ Open Platform 与 joyhousebot Channel SDK 之间的协议转换；Run、Task、
PostgreSQL Outbox、租约、重试和审计仍由 Core 负责。

QQ 不是默认产品渠道。只有明确需要 QQ 私信入口时才安装：

```bash
uv pip install -e extensions/channel-qq
```

然后显式启用 `channel-qq`。`secret` 必须使用 `env://VARIABLE`，安装扩展不会自动启用或授予权限。
