# Host Model Gateway 与 Tool Broker

JoyhouseBot 允许 Pi、OpenCLI 及其他 Node Agent loop 复用模型和 Runtime Capability，但不把 Provider
密钥、Device token、PostgreSQL 或内部 Python 对象交给 Host。

## 两类短期授权

- `jhm_`：绑定 Device delivery、Run、Provider revision、model、token/cost 预算和并发数；仅供独立
  Model Gateway 使用。Gateway 在调用前事务性预留预算，完成后按实际 usage 结算。
- `jht_`：绑定当前 Device claim、delivery、用户和冻结的 `tool_access`；仅能创建及查询该 delivery
  的 Host Tool request。令牌过期、设备撤销、claim 变化或 Run 终止后立即失效。

原始令牌只在签发响应和 Host 的瞬时内存中出现。数据库只保存 SHA-256 指纹；operation registry、事件、
Artifact、日志和 Pi checkpoint 都不能保存令牌。

## Host Tool 状态链

```text
Host -> POST /v1/host-tool-requests
     -> host_tool_requests (PostgreSQL + lease/fencing)
     -> Agent Worker resolves frozen Agent revision
     -> Capability Registry / Dispatcher
     -> optional Approval or Operation Reconciliation
     -> GET /v1/host-tool-requests/{request_id}
```

`host_request_id` 在一个 delivery 内唯一。同键同输入返回同一记录；同键换 Capability 或输入返回冲突。
Host 不能提交未冻结版本，也不能增加权限。副作用 Capability 继续使用 Runtime 生成的 `action_id` 和
`idempotency_key`，并要求 write receipt。

一个 delivery 最多创建 64 个不同 Tool request。父 Capability 不能授权调用自身，Broker grant 也不会
下传给被调用 Tool，因此动态 Tool 调用只有一层；若未来需要多层协同，必须显式设计新的父子 Run 和预算，
不能放宽当前 grant。

Node Extension 可使用 `@joyhousebot/extension-sdk` 的 `HostToolBrokerClient`。轮询是正确性基线；未来可在
不改变状态机的前提下增加 SSE 唤醒。

## 本地运行

```bash
JOYHOUSEBOT_START_MODEL_GATEWAY=true ./scripts/start-local.sh
```

Model Gateway 默认监听 `127.0.0.1:18794`。Node Host 生产发行必须使用
`hosts/node/runtime-lock.json` 固定的 Node `v24.19.0` LTS；运行时不下载 npm 包。

`docker-compose.runtime.yml` 中的 `model-gateway` 只通过 Compose 内部网络暴露 18794，不映射宿主机
端口；systemd 单机部署同样只监听 loopback。需要跨主机访问时必须放在内部 TLS/mTLS 代理之后，不能把
Gateway 直接暴露到公网。
