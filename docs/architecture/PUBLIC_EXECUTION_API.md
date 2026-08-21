# ADR：EntryPoint 驱动的公共执行 API

- 状态：Accepted for joyhousebot 2.0 migration
- 日期：2026-08-18
- 范围：App/Owner HTTP、SSE 与 SDK 契约

实施状态（2026-08-18）：EntryPoint 列表/详情、结构化 Run 提交、Run 查询、Installation token exchange、
Artifact、Approval/Input、Cancel 与可恢复 SSE 已经以 experimental `/v2` 路由落地。公共投影和 PostgreSQL
契约测试已经建立；OpenAPI snapshot、SDK 和跨进程 E2E 仍在后续批次完成。

## 决策

joyhousebot 为产品和独立 App 提供一套 EntryPoint 驱动的 `/v2` 执行面。Owner 与 Installation 使用相同
资源模型和 application service，仅 Authority resolution 与可见范围不同。内部 Agent、Team、Workflow、
Scenario 和 Graph 不能要求 App 在运行时选择或拼装。

## 资源

### EntryPoint

EntryPoint 是已发布 App 或 Owner 产品能力的稳定投影：

```json
{
  "id": "candidate-review",
  "name": "候选人综合评审",
  "description": "生成基于证据的岗位匹配评审",
  "input_schema": {},
  "output_schema": {},
  "interaction_mode": "async",
  "permission_summary": [],
  "risk_summary": []
}
```

内部记录冻结精确 execution target 与 Release，但公共 DTO 不返回 target kind、Revision、CapabilityRef、
build digest、Worker 或 Graph。EntryPoint stable ID 在一个 App/Installation namespace 中唯一。

### Run

公共 Run 只返回稳定状态、进度、时间、公开错误、输出 Artifact、待处理 Approval/Input 和允许动作。内部
Task、lease、重试细节、供应商 payload 与 Trace 通过受权限控制的诊断面查询。

Run 提交：

```json
{
  "input": {
    "job_id": "job-123",
    "candidate_id": "candidate-456"
  },
  "idempotency_key": "candidate-review:request-789",
  "client_context": {}
}
```

所有 execution target 都接受同一结构化 `input`；application service 在创建 Run 前按 EntryPoint
`input_schema` 验证。不得要求 App 把 JSON 序列化进自然语言 content，也不得让不同 target kind 产生不同
提交协议。

### Artifact 与 Approval

输出先形成私有 Artifact。Run 可以标记一个 primary output 和多个具名 outputs；公开分享必须另行进入
Work 发布链，不能暴露原始 Artifact URL。

Approval 关联冻结 Action、风险摘要、请求参数摘要、到期时间和允许决策。批准不能改变原 Action 的
`action_id`、`idempotency_key` 或 Capability ceiling。

## 路由

目标路由为：

```http
GET    /v2/entrypoints
GET    /v2/entrypoints/{entrypoint_id}
POST   /v2/entrypoints/{entrypoint_id}/runs

GET    /v2/runs/{run_id}
GET    /v2/runs/{run_id}/events
GET    /v2/runs/{run_id}/inputs
POST   /v2/runs/{run_id}/inputs
POST   /v2/runs/{run_id}/cancel

GET    /v2/runs/{run_id}/artifacts
GET    /v2/artifacts/{artifact_id}

GET    /v2/runs/{run_id}/operations

GET    /v2/runs/{run_id}/approvals
POST   /v2/approvals/{approval_id}/decisions
```

Run 详情可以链接相关资源，但不能内嵌无界事件、Trace 或二进制 Artifact。列表统一使用 cursor pagination。
长时外部操作只公开面向用户的状态、摘要、百分比、已完成/总数以及当前/下一项标签；供应商操作标识、
签名参数和任意事件字段留在控制面。公开操作进度是 Run 的只读投影，不是第二套状态机。

## SSE 契约

事件信封固定为：

```json
{
  "sequence": 42,
  "event": "run.output.delta",
  "run_id": "run-123",
  "timestamp": "2026-08-18T00:00:00Z",
  "data": {}
}
```

- sequence 在单 Run 内单调递增并来自 PostgreSQL 事实源。
- 服务接受 `Last-Event-ID` 或等价 cursor，重连时补发未消费事件。
- 事件只能包含当前 Principal 可读取的数据；内部诊断事件不能混入公共 stream。
- 公共事件词汇收敛为 Run 状态/进度/输出、Artifact、Approval 与 Input；Model、Task、Capability、lease、
  Worker 和 Trace 事件即使在内部标记为 public，也不会直接穿透 `/v2`。
- 心跳不是业务事件，不推进持久 sequence。
- SDK 自动重连；SSE 不可用时以条件轮询降级，不能创建第二套状态机。

## 状态和错误

公共状态集合保持小而稳定：`queued`、`running`、`waiting_for_input`、`waiting_for_approval`、
`succeeded`、`failed`、`cancelled`。内部重试、等待子 Run、lease 恢复等状态映射为公开进度或诊断详情，
不扩大公共状态枚举。

公开错误至少包含稳定 `code`、安全 `message`、`retryable` 和可选字段路径。供应商原始响应、prompt、密钥、
内部堆栈和未授权业务数据不能进入公开错误。

## 幂等与版本

- `idempotency_key` 在 Principal + EntryPoint scope 内唯一；相同键和相同规范化输入返回同一 Run。
- 同键不同输入必须冲突，不能静默复用。
- Run 创建时冻结 EntryPoint Release、execution target、Capability ceiling、Schema 和策略。
- Release 激活只影响新 Run，进行中的 Run 不漂移。

## SDK 表面

Python 和 TypeScript SDK 以资源和 RunHandle 为中心：

```python
client = joyhousebot.from_env()
run = await client.entrypoints.run(
    "candidate-review",
    input={"candidate_id": "candidate-456"},
    idempotency_key="review:request-789",
)
async for event in run.events():
    ...
output = await run.output()
# 长时 Connector 可以读取脱敏后的当前进度
operations = await run.operations()
```

SDK 隐藏 token exchange、Grant ID、Event cursor、重试和 URL 拼接。App 可以保存业务 request ID、Run ID 与
Artifact ID，但不复制 Runtime 的 Task/Event 状态机。

## 控制面分离

Agent、Workflow、Skill、Release、Eval、Extension、Capability Registry、Worker 和 Trace 使用独立 Control
API 与权限。公共 `/v2` 路由不能因为 Console 需要高级数据而扩张 DTO；Console 也不能用公共 App token
绕过 Operator 审计。

## 契约测试

实现必须增加：

1. OpenAPI snapshot 与 Python/TypeScript DTO 一致性测试。
2. 所有 execution target 的统一结构化 input 测试。
3. Owner/Installation 可见性和负向权限测试。
4. SSE 重连、补发、终态和权限过滤测试。
5. 幂等同输入复用、异输入冲突测试。
6. Release 切换时进行中 Run 不漂移测试。
7. Artifact 与 Approval 生命周期测试。

旧产品执行路由仅在跨仓库集成分支迁移期间存在；JoyHouse 和 Talent Flow 切换后直接删除，不发布长期
兼容 shim。
