# JoyhouseBot DingTalk Channel

可选 DingTalk Stream Channel 扩展。供应商 SDK 和协议实现位于本包，Run、Task、PG Outbox、Lease、重试与
审计仍由 JoyhouseBot Core 负责。

```bash
uv pip install -e extensions/channel-dingtalk
```

安装后显式启用 `channel-dingtalk`；`clientSecret` 必须通过 `env://VARIABLE` 引用。该渠道不是 Joyhouse
面向 OPC 的默认产品能力。
