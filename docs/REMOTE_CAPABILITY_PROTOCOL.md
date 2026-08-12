# 远程 Capability 协议 v1

状态：Implemented（2026-08-10）

## 1. 边界

企业程序是产品平面，拥有用户界面、组织身份、业务权限、业务数据和最终事务；JoyhouseBot 是执行平面，
负责任务规划、调度、审批、恢复、对账、审计和成果。企业业务代码不得作为 Python 插件加载进 Core 或
Worker，只通过 `connector-http-capability` 的固定 HTTP 协议接入。

```text
企业程序 ── submit/query/SSE ──▶ JoyhouseBot
企业程序 ◀── signed Capability ── JoyhouseBot Worker
```

## 2. 安装与控制面配置

Connector 代码仍由部署显式安装和启用：

```bash
uv pip install -e extensions/connector-http-capability
```

```json
{
  "extensions": {
    "catalogDirectories": ["./extensions"],
    "allowedIds": ["connector-http-capability"],
    "initiallyActive": ["connector-http-capability"],
    "allowConsoleActivation": true
  }
}
```

安装只表示 Worker 可以加载协议实现。管理员随后在 Console 的“集成中心 → 远程能力”创建连接
Revision，或者调用 `POST /v1/admin/remote-connections`。服务地址、Key ID、密钥引用和 Capability
目录进入 PostgreSQL 不可变版本；API 不修改部署配置，也不保存密钥明文。

连接请求示例：

```json
{
  "connection_id": "crm",
  "name": "CRM 业务服务",
  "description": "销售线索读写边界",
  "base_url": "https://crm.internal.example/joyhousebot/v1",
  "key_id": "joyhousebot-prod-2026-01",
  "signing_secret_ref": "env://CRM_JOYHOUSEBOT_SIGNING_SECRET",
  "require_response_signature": true,
  "timeout_seconds": 60,
  "max_response_bytes": 10485760,
  "capabilities": [
    {
      "capability_id": "crm.lead.read",
      "version": "1.0.0",
      "implementation_digest": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "name": "读取销售线索",
      "description": "按授权范围读取一条销售线索",
      "input_schema": {
        "type": "object",
        "properties": {"lead_id": {"type": "string"}},
        "required": ["lead_id"],
        "additionalProperties": false
      },
      "output_schema": {"type": "object"},
      "permissions": ["crm.lead.read"],
      "side_effect": "read",
      "idempotent": true,
      "data_classification": "confidential"
    }
  ]
}
```

`signing_secret_ref` 只能是 `env://VARIABLE`。每个 Agent Worker 必须设置相同变量，控制面和数据库永远
看不到它的值；缺少变量或密钥少于 32 bytes 时，该 Worker 的发布预热失败，旧 Revision 继续生效。
同一 `capability_id + version` 的 Schema、权限、远端实现摘要或其他定义发生变化时，PostgreSQL 不可变
目录会拒绝覆盖，必须发布新版本。

远端地址完全来自已发布连接 Revision。模型输入、Run payload 和 Capability 参数都不能选择目标 URL。生产只允许
HTTPS；`allow_insecure_http=true` 也只允许 `localhost`、`127.0.0.1` 或 `::1`。

完整控制面链路是：

```text
Connector 插件 active
  → 连接 Draft
  → 连接 rollout / Worker 配置与精确定义预热
  → 连接 active
  → Capability discovered
  → Capability rollout / Worker ACK
  → Capability published
  → Agent Revision 显式授权
```

连接回滚复用 `/v1/admin/rollouts/{rollout_id}/rollback`。回滚同样先要求 Worker 预热旧 Revision，不会
在预热失败时覆盖当前有效连接。

## 3. 调用接口

```http
POST {base_url}/capabilities/{capability_id}:invoke
Content-Type: application/json
X-Joyhouse-Capability-Protocol: 1
X-Joyhouse-Key-Id: <key-id>
X-Joyhouse-Timestamp: <unix-seconds>
X-Joyhouse-Nonce: <random>
X-Joyhouse-Signature: v1=<hex-hmac-sha256>
X-Joyhouse-Run-ID: <run-id>
X-Joyhouse-Action-ID: <action-id>       # 写操作
Idempotency-Key: action:<action-id>
```

正文：

```json
{
  "protocol_version": "1",
  "capability": {
    "capability_id": "crm.lead.update",
    "version": "1.0.0",
    "implementation_digest": "sha256:..."
  },
  "subject": {
    "user_id": "42",
    "agent_id": "sales-agent",
    "session_id": "session-1"
  },
  "execution": {
    "run_id": "run-1",
    "root_run_id": "run-1",
    "task_id": "task-1",
    "request_id": "request-1",
    "action_id": "act-1",
    "idempotency_key": "action:act-1"
  },
  "authorization": {
    "permissions": ["crm.lead.update"],
    "permission_mode": "default"
  },
  "input": {"lead_id": "lead-1", "status": "qualified"}
}
```

企业程序必须再次校验主体、权限、能力版本、实现摘要和业务规则。JoyhouseBot 的授权不能替代业务系统的
最终授权。

## 4. 签名

请求正文使用 UTF-8、键排序、无多余空格的 JSON。请求签名原文为：

```text
JHBCAP-HMAC-SHA256\n
1\n
POST\n
<base-path + request-path>\n
<timestamp>\n
<nonce>\n
<sha256(body)>
```

以共享密钥计算 HMAC-SHA256，头部值为 `v1=<hex>`。服务端应限制时间偏差（建议五分钟），并在窗口内
拒绝重复 nonce。

响应必须使用同一请求 nonce 签名：

```text
JHBCAP-RESPONSE-HMAC-SHA256\n
1\n
<http-status>\n
<request-nonce>\n
<sha256(response-body)>
```

响应头为 `X-Joyhouse-Response-Signature: v1=<hex>`。连接器默认拒绝未签名或签名错误的响应。

## 5. 同步结果与写入回执

读取成功：

```json
{
  "protocol_version": "1",
  "status": "succeeded",
  "summary": "已读取线索",
  "output": {"lead_id": "lead-1", "status": "new"},
  "artifacts": []
}
```

写操作成功时必须额外返回：

```json
{
  "protocol_version": "1",
  "status": "succeeded",
  "summary": "已更新线索",
  "output": {"lead_id": "lead-1", "status": "qualified"},
  "write_receipt": {
    "action_id": "act-1",
    "idempotency_key": "action:act-1"
  }
}
```

连接器会逐字比对回执与 Runtime 冻结身份。缺失或不一致都会失败。企业程序必须持久化
`idempotency_key + input_hash + status + result`：同键同参数回放原结果，同键换参数返回冲突。

## 6. 异步操作与对账

已受理但尚未结束时返回 HTTP 202：

```json
{
  "protocol_version": "1",
  "status": "accepted",
  "summary": "任务已受理",
  "operation": {"operation_id": "crm-op-42"},
  "write_receipt": {
    "action_id": "act-1",
    "idempotency_key": "action:act-1"
  }
}
```

JoyhouseBot 不会重新提交操作，而是调用：

```http
POST {base_url}/operations:reconcile
```

正文包含原 Capability、主体、冻结执行身份和 `operation.operation_id`。响应状态只能是：

- `pending`：可带 `retry_after_seconds`；
- `succeeded`：带最终 `output` 和 `artifacts`；
- `failed`：带结构化 `error`；
- `unknown`：无法确定结果，JoyhouseBot 转人工处理，不重新写入。

## 7. 错误

非 2xx 和业务失败都返回签名 JSON：

```json
{
  "protocol_version": "1",
  "status": "failed",
  "error": {
    "code": "IDEMPOTENCY_CONFLICT",
    "message": "same key was used with different input",
    "retryable": false
  }
}
```

网络错误、超时和服务端明确声明的可重试错误由 Runtime 策略处理；认证失败、权限不足、幂等冲突、协议
错误和输出 Schema 错误不会被当成安全重试。响应大小默认限制为 10 MiB，最大可配置为 50 MiB。

## 8. 发布流程

Worker 启动时加载当前有效连接，不再等到第一条 Agent 消息。连接发布预热只验证固定 endpoint、安全
策略、Worker 密钥可用性和精确 Capability 定义；它不会调用任何业务 Capability，因此不会制造探测性
业务写入。预热通过后，能力只记录为 `discovered`，不会自动激活。管理员仍需执行：

```text
discovered → staged → Worker exact definition ACK → active
```

连接与它发现的 Capability 是两个连续但独立的 rollout。最后一个 Worker 对连接提交 ACK 时，连接才成为
PostgreSQL 中的 active generation；其他 Worker 在处理紧随其后的 Capability rollout 前，必须先同步这代
已生效连接，再校验精确 Capability identity。该同步只更新 Worker 内的 Connector Registry，不调用业务
能力，也不会绕过 Capability 的独立发布、权限或 Agent allowlist。

Agent Revision 再以精确 `CapabilityRef` 和最小权限绑定能力。更新企业程序实现时必须更换版本或
`implementation_digest`；同版本漂移会被不可变目录拒绝。
