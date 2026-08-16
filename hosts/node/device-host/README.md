# Porthouse Device Host

Device Host 是 Cloud Runtime 到本机 Node Supervisor 的出站传输代理。它主动使用设备 Bearer 身份连接
Cloud，领取已经过 Runtime 权限、审批和 Action 冻结的 delivery，再通过本机 HMAC 协议调用 Supervisor。
它不开放公网端口，也不上传 Cookie、Chrome Profile、浏览器存储或本机凭据。

配置只保存 `env://VARIABLE` 引用：

```json
{
  "runtime_base_url": "https://cloud.example.com",
  "model_gateway_base_url": "http://127.0.0.1:18794",
  "device_id": "my-mac",
  "device_token_ref": "env://PORTHOUSE_DEVICE_TOKEN",
  "host_revision": "node-host@0.1.0+desktop-build",
  "host_manifest_digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "poll_interval_ms": 2000,
  "claim_lease_seconds": 60,
  "local_host": {
    "base_url": "http://127.0.0.1:17381",
    "base_path": "/porthouse/v1",
    "key_id": "desktop-host-key",
    "signing_secret_ref": "env://PORTHOUSE_NODE_HOST_SIGNING_SECRET",
    "require_response_signature": true
  }
}
```

设备 token 只在 Runtime 注册响应或显式轮换时返回一次。Desktop 应保存到系统 Keychain，并在启动
Device Host 子进程时注入环境变量；不能把 token 写回 JSON。生产包使用 `../runtime-lock.json` 固定的
Node LTS 绝对路径启动本包。

Device Host 只把按 delivery 签发的短期 `jhm_` Model grant 和 `jht_` Tool grant 放入瞬时调用上下文；
不会把设备总 token 交给 Supervisor 或 Extension。Tool request 仍由 Runtime Agent Worker 执行。
