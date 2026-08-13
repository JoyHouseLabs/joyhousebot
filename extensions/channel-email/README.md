# JoyhouseBot Email Channel

JoyhouseBot 的可选官方 Channel 扩展。它通过 IMAP 接收入站邮件，通过 SMTP 投递
Runtime 的出站消息；Run、Task、PG Outbox、租约、重试与审计仍由 JoyhouseBot Core 负责。

## 本地安装

```bash
uv pip install -e extensions/channel-email
```

生产环境应构建并固定 wheel 摘要。将 `channel-email` 加入
`extensions.allowedIds`，首次安装时如需自动激活再加入 `extensions.initiallyActive`，并在
`extensions.settings.channel-email` 中配置；密码必须使用 `env://VARIABLE` 引用，且必须显式授予
`consentGranted`。`extensions.enabled` 只保留给旧部署迁移，不应用于新配置。
