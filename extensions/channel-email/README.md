# Porthouse Email Channel

Porthouse 的可选官方 Channel 扩展。它通过 IMAP 接收入站邮件，通过 SMTP 投递
Runtime 的出站消息；Run、Task、PG Outbox、租约、重试与审计仍由 Porthouse Core 负责。

## 本地安装

```bash
uv pip install -e extensions/channel-email
```

生产环境应构建并固定 wheel 摘要。将 `channel-email` 加入
`extensions.allowedIds`，首次安装时如需自动激活再加入 `extensions.initiallyActive`，并在
`extensions.settings.channel-email` 中配置；密码必须使用 `env://VARIABLE` 引用，且必须显式授予
`consentGranted`。`extensions.enabled` 只保留给旧部署迁移，不应用于新配置。

## Resend 发信

Resend 不需要进入 Runtime Core，也不需要复制一套 Channel 状态机：它通过本扩展的标准 SMTP 出站能力接入。
把 API Key 保留在部署环境，用户名固定为 `resend`；不要把 Key 写入 `config.json`、Console 或日志：

```json
{
  "extensions": {
    "settings": {
      "channel-email": {
        "consentGranted": true,
        "fromAddress": "Porthouse <hello@porthouse.me>",
        "smtpHost": "smtp.resend.com",
        "smtpPort": 465,
        "smtpUseSsl": true,
        "smtpUsername": "resend",
        "smtpPassword": "env://RESEND_API_KEY"
      }
    }
  }
}
```

入站邮件仍应使用 IMAP 或单独的 Webhook/Connector Extension；Resend 的发送回执、退信和营销统计不能绕过
Runtime 的 Action、Outbox、审计与对账链直接写入业务状态。
