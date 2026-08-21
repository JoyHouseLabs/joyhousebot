# joyhousebot Node Extension Host

通用 Supervisor 把精确安装、已 allowlist 的 Node Extension 暴露为 Extension Host Profile。它不拥有
Run/Task/Approval 状态，只通过签名 Remote Capability v1 接收调用，并把每个 Extension 放入独立故障域。

当前 Alpha 提供：

- 启动前校验 Extension ID、build/lockfile digest、bundle root 与入口文件 SHA-256；
- 每 Extension 独立 child process、JSONL stdio、请求/响应字节上限、超时终止和 crash-loop breaker；
- stdout 只能发送协议帧，stderr 独立限流日志；
- Host `health/meta:describe/invoke/reconcile/command/cancel` 固定入口和 HMAC 防重放；
- operation 到 Extension 的原子持久路由，Supervisor 重启后仍可回到原 Extension reconcile；
- `env://VARIABLE` 显式传入 Extension 环境，默认不继承宿主进程密钥。
- Manifest 分别声明 Invocation Capability、Channel Driver 与 Event Source；三类组件共享供应链和进程
  生命周期协议，但 Channel/Event 不进入 operation reconciliation。
- 瞬时上下文只接受部署 allowlist 中的 Model Gateway/Tool Broker URL 及对应短期 grant。

`child_process` 仅用于可信内置 Extension 和本地开发。Shell、浏览器、不受信包、需要强网络限制的
Server Extension 必须选择 `runner=oci`；Alpha 遇到 OCI 配置会失败关闭，不能退回宿主进程。

启动：

```bash
npm ci
npm run build
export JOYHOUSEBOT_NODE_HOST_CONFIG=/absolute/path/host.config.json
export JOYHOUSEBOT_NODE_HOST_KEY_ID=runtime-key-id
export JOYHOUSEBOT_NODE_HOST_SIGNING_SECRET='env secret with at least 32 bytes'
node dist/cli.js
```

配置由部署脚本或 Desktop 安装器生成，Console 不上传 npm/tgz，也不修改入口路径。`bundle_root` 必须是
绝对路径；`entrypoint` 必须位于该根目录内并匹配 `entrypoint_sha256`。生产发行使用
`../runtime-lock.json` 固定的内置 Node，不使用用户全局 Node。
