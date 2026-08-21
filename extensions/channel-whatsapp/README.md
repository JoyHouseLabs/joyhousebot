# joyhousebot WhatsApp Channel

可选 WhatsApp Channel 扩展，由两个独立制品组成：

- `joyhousebot-channel-whatsapp` Python adapter：连接 Runtime 的统一 Run/Outbox/Lease 链；
- `bridge/` Node.js sidecar：通过 Baileys 连接 WhatsApp Web，只实现供应商协议。

```bash
uv pip install -e extensions/channel-whatsapp
cd extensions/channel-whatsapp/bridge
npm ci && npm run build && npm start
```

安装 adapter 后显式启用 `channel-whatsapp`。`bridge_token` 必须使用 `env://VARIABLE`，并同时
传给 sidecar 的 `BRIDGE_TOKEN`。Sidecar 默认只监听 `127.0.0.1`；设置非 loopback
`BRIDGE_HOST` 时若没有 `BRIDGE_TOKEN` 会拒绝启动。

Python adapter 会等待 bridge 的 `sent/error` 回执后再向 Core 报告投递结果；Runtime 的稳定
outbound ID 会作为 bridge `requestId` 传递，以便审计和重试关联。
