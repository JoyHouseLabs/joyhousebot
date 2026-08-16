# Extension Host Profile v1

状态：Incremental Implementation；D0-D5 与 D6 OpenCLI Host Pilot 已实现，Device Transport、Model
Model Gateway、Pi 和 Tool Broker 已实现；Channel/Event Source 已冻结 Manifest/SDK 协议基线，具体
Driver 与产品控制面继续按独立 Extension 分阶段实施（2026-08-16）

## 1. 设计结论

JoyhouseBot Core 保持 Python-first，Node.js、其他语言、OCI 容器和远程服务通过隔离的
Extension Host 接入。Extension Host 不是第二个 Runtime，也不建立新的公共执行协议：它是
[Remote Capability v1](REMOTE_CAPABILITY_PROTOCOL.md) 的运行环境与长程流式执行 Profile。

协议关系如下：

```text
JoyHouse / App / Skill
          |
          v
JoyhouseBot Run / Task / Action / Approval / Artifact / Audit
          |
          v
Capability Dispatcher
          |
          v
Remote Capability v1
  |- synchronous profile
  |- reconciliation profile
  `- extension-host streaming profile
                    |
                    v
             Extension Host
         Python | Node | OCI | remote
```

以下能力仍由 Runtime 独占：

- Run、Task、Action、Approval 和 Artifact 的权威状态；
- `user_id + agent_id + root_run_id` 隔离；
- Capability allowlist、参数 Schema、权限、配额和成本治理；
- 冻结 `action_id/idempotency_key`、写回执、重试与对账；
- 发布、Worker ACK、审计、回放与回滚。

Host 只执行 Runtime 已授权的精确版本调用，不接收 PostgreSQL 连接，不读取 Runtime Store，也不
承载垂直 App 的数据模型或业务状态机。

## 2. 适用边界

Extension Host 用于确实需要独立语言运行环境、依赖树或长生命周期的通用技术能力，例如：

- Pi 等编程执行器；
- OpenCLI 等通过用户已登录浏览器提供确定性网站命令的工具；
- Playwright、Puppeteer 等浏览器自动化；
- Node.js 特有的云服务、内容、音视频或设备 SDK；
- WhatsApp/Baileys、Discord Gateway 等持续连接；
- GitHub Webhook、消息队列或文件监听等事件源；
- 需要进程或容器隔离的 Python、Node 或其他语言工具。

以下情况不应强行进入 Host：

- 已经提供标准 MCP Server 的能力优先使用 MCP Gateway；
- 独立业务系统继续使用 Remote Capability，不打包进 Runtime Worker；
- 普通 HTTP API 优先使用通用 HTTP Connector；
- JoyHouse 产品界面和业务数据模型继续留在独立 App；
- 任意 npm 包、任意脚本和宿主机 Shell 不能直接开放给 Agent。

## 3. Host 内核与扩展包分层

Host 是稳定的 Supervisor，不包含 Pi、WhatsApp、Playwright 或供应商 SDK 的条件分支。

```text
Node Extension Host
|- protocol adapter
|- package verifier
|- process/container supervisor
|- secret and resource broker
|- event replay buffer
|- health and telemetry
`- extension processes
   |- capability-opencli
   |- capability-pi-runner
   |- capability-playwright
   |- channel-whatsapp
   `- event-source-github
```

每个 Extension 默认运行在独立 child process；生产环境对 Shell、浏览器、非受信依赖和高风险网络访问
使用独立 OCI 容器。多个 Extension 不得共享同一个可加载任意业务包的 Node 进程，以避免依赖冲突、
事件循环阻塞、内存泄漏和密钥越权。

Host Supervisor 只负责启动、终止、资源限制、协议转发和健康管理。Extension 只能依赖公开的 Node
Extension SDK，不能导入 Host 内部模块。

## 4. 三种运行 Profile

### 4.1 Invocation Profile

适用于有明确开始和终态的 Capability：编程、浏览器自动化、文档处理、云 SDK 调用和数据分析。

Runtime Worker 通过 Remote Capability v1 提交调用。短任务直接返回 `succeeded`；长任务返回
`accepted + operation_id`。最终一致性的强制基线仍是现有 `operations:reconcile`；流式事件是低延迟观测
增强，不能成为正确完成或恢复操作的唯一途径：

```http
GET  {base_url}/operations/{operation_id}/events       # optional SSE
POST {base_url}/operations/{operation_id}:command
POST {base_url}/operations/{operation_id}:cancel
POST {base_url}/operations:reconcile
```

`operations:reconcile` 可以返回自上次 cursor 后的有界进度事件，因此 Worker 不保持 SSE 连接也能恢复
完整状态。可选事件端点使用 SSE 并支持 `Last-Event-ID`；命令端点用于返回 Tool 结果、恢复 checkpoint
或传递取消指令。Runtime 只从固定 `base_url + operation_id` 构造路径，不接受 Host 返回的任意回调 URL。

Runtime 在 `operation.cursor` 发送上次已提交的 opaque cursor。响应可附带 `provider_cursor`、
`checkpoint_ref`、`progress_summary`、`progress_percent`、`cursor_reset` 和最多 100 个 `events`。
每个事件必须携带稳定 `event_id`、非负 `sequence`、规范化 `event_type`，单事件 payload 不超过 32 KiB，
单批不超过 256 KiB。Core 在同一 PostgreSQL 事务中去重事件并推进 cursor；重复批次不会产生重复 Run Event，
旧 lease owner 也不能推进 cursor。每个 operation 最多保留 10,000 条 provider event，详情通过
`GET /v1/runs/{run_id}/operations/{reconciliation_id}/events` 分页查询，Run 列表不加载事件正文。

### 大型 Artifact 上传

Host 不得返回 `file://`、宿主机路径或自行生成的公网 URL 作为 Runtime Artifact。调用方先在冻结 operation
下创建 `artifact-upload-grant`，拿到只显示一次的 Bearer token；上传时必须逐字匹配 action、operation、
Content-Type、Content-Length 和 `X-Content-SHA256`。API 流式写入 content-addressed object 后立即销毁 token，
再由 Execution Worker 通过 fenced lease 物化 `host.output` Artifact。Grant 最长一小时、默认十五分钟；
单文件默认上限 250 MiB，可由部署配置降低。

### 4.2 Channel Profile

适用于 WhatsApp、Discord、电话或实时设备等永久双向连接。传输可以使用 WebSocket、Webhook 或供应商
长轮询，但 Core-facing 语义保持为：

```text
inbound envelope -> Channel adapter -> normal Run submission
outbound_id -> outbound command -> accepted/sent/error receipt
```

Channel Profile 不使用 Invocation 的长任务状态机，不创建第二套 Run、Outbox 或 Lease。现有 WhatsApp
Python adapter 仍是唯一接触 Runtime Channel/Outbox/Lease 契约的组件；Node bridge 只处理供应商协议。

### 4.3 Event Source Profile

适用于 GitHub Webhook、消息队列、云告警、支付回调、文件监听和变更数据流。Host 将供应商事件规范化为
版本化 Trigger envelope，由 Runtime 去重并创建正常 Run。

事件源不能直接执行 Agent、修改 Run 或写业务数据库。每条事件必须包含稳定的供应商事件 ID、来源、
时间、Schema 版本和可验证的连接身份。

### 4.4 Device Host 是传输形态，不是第四种执行 Profile

Desktop 上的 OpenCLI、浏览器会话和未来本地设备能力通常位于 NAT 之后，Cloud Runtime 不能直接访问
Host `base_url`。此时使用 Device Host Transport：本地 Host 通过设备身份主动建立出站 HTTPS 连接，
领取已授权 operation 并提交事件、结果和 Artifact；可选 SSE 只负责低延迟唤醒。

Device Relay 面向 Runtime 仍呈现固定、签名的 Remote Capability 接口。Relay 只保存 operation delivery、
cursor、deadline 和设备 ACK 等传输投影，不拥有 Run/Task/Approval 终态，也不把浏览器 Cookie、Chrome
Profile 内容或本地凭据上传到 Cloud。SSE 断开、设备换网或 Runtime 重启后，正确性继续依赖 HTTPS
claim/reconcile 和 PostgreSQL；不能依赖一条常驻 WebSocket 作为事实源。

调用必须绑定 `user_id + device_id + host_revision + capability_revision + action_id`。设备撤销、Extension
禁用、Run 取消或 deadline 到期后不得领取新任务；离线任务保持 `waiting_external`，由 Runtime 决定重试、
换设备或进入 `manual_required`。

## 5. Node Extension SDK v1

Node SDK 是具体 npm 生态与 Host 内核之间唯一允许的编程接口。概念接口如下：

```ts
export interface JoyhouseNodeExtension {
  manifest(): ExtensionManifest;
  capabilities(): CapabilityDefinition[];
  activate(context: ExtensionContext): Promise<void>;
  deactivate(): Promise<void>;
}

export interface CapabilityHandler {
  invoke(
    context: InvocationContext,
    input: unknown,
  ): AsyncIterable<HostEvent>;

  resume?(
    context: InvocationContext,
    checkpoint: Checkpoint,
    command: RuntimeCommand,
  ): AsyncIterable<HostEvent>;

  cancel?(context: InvocationContext): Promise<void>;
}
```

SDK 只暴露受限能力：结构化日志、事件、checkpoint、Artifact upload grant、Runtime Tool Broker 和只读
调用上下文。SDK 不暴露数据库、Runtime Store、任意服务定位器或跨用户查询接口。

Channel 和 Event Source 使用独立接口，不能假装成 Capability Handler：

```ts
export interface ChannelDriver {
  start(context: ChannelContext): Promise<void>;
  send(command: OutboundCommand): Promise<DeliveryReceipt>;
  stop(): Promise<void>;
}

export interface EventSourceDriver {
  start(context: EventSourceContext): Promise<void>;
  stop(): Promise<void>;
}
```

## 6. 发布 Manifest

Node Extension release 必须声明精确运行环境、完整性和权限边界：

```json
{
  "extension_id": "capability-playwright",
  "version": "1.0.0",
  "extension_types": ["capability"],
  "runtime": {
    "language": "node",
    "node_range": ">=20 <25",
    "host_protocol_version": "1",
    "sdk_version": "1",
    "profile": "invocation",
    "entrypoint": "dist/index.js"
  },
  "integrity": {
    "build_digest": "sha256:...",
    "lockfile_digest": "sha256:..."
  },
  "permissions": {
    "network_domains": ["example.com"],
    "workspace": "ephemeral",
    "secrets": ["env://BROWSER_TOKEN"],
    "shell": false
  },
  "resources": {
    "memory_mb": 512,
    "timeout_seconds": 600,
    "max_concurrency": 2
  }
}
```

第一阶段不增加 `executor` Extension 类型。Pi 以 `capability` 发布，例如 `coding.pi.execute`；Node Host
作为部署服务进入 Remote Connection Revision；Node 构建摘要映射为远程 Capability 的
`implementation_digest`。连接与 Capability 继续复用现有两阶段 rollout 和 Worker ACK。

## 7. Invocation 身份与 fencing

Remote Capability 的 `execution` 字段在 Host Profile 中增加：

```json
{
  "execution": {
    "run_id": "run_...",
    "root_run_id": "run_...",
    "task_id": "task_...",
    "request_id": "request_...",
    "action_id": "action_...",
    "idempotency_key": "action:action_...",
    "request_digest": "sha256:..."
  }
}
```

规则：

- Runtime 生成所有执行身份；App、模型、Host 和 Extension 均不能自行替换；
- 同幂等键、同请求摘要必须返回原 `operation_id`；
- 同幂等键、不同请求摘要必须返回 `409 IDEMPOTENCY_CONFLICT`；
- Host operation 绑定稳定 Action，不绑定某一次短暂的 Run/Task lease；
- 新 Worker 可重新连接同一 operation；Runtime 使用 `operation_reconciliations.lease_version` 抢占对账权，
  并继续使用 Run/Task lease fencing 推进状态；
- Runtime lease、Worker ID 和内部 attempt 不作为 Host 可决定或必须回显的公共协议身份；
- Host 的状态和 replay buffer 只是投影，不是运行时事实源。

## 8. 事件、checkpoint 与暂停恢复

Host 事件拥有不可变 `event_id` 和每 operation 严格递增的 `sequence`。它们可以通过 reconcile 的有界
事件批次或可选 SSE 投递，语义均为 at-least-once。Runtime 按 `operation_id + event_id` 去重，并只在
持有当前 reconciliation lease 时写入规范化 Event 和推进 cursor。

v1 事件类型：

- `accepted`、`progress`、`warning`；
- `tool.requested`、`tool.started`、`tool.completed`、`tool.failed`；
- `checkpoint.created`、`waiting_runtime`、`resumed`；
- `model.usage`；
- `artifact.pending`、`artifact.committed`；
- `completed`、`failed`、`cancelled`。

审批或用户输入可能持续数小时，Host 不能占用 Runtime Worker lease 等待。Extension 必须先生成
checkpoint，Host 转为 `waiting_runtime`；是否等待审批属于 Runtime 内部状态，不要求 Host 理解
`waiting_approval`。Runtime 持久化等待原因并释放 lease，批准后新的 Worker 通过 `:command` 发送
checkpoint 和结果，Host 恢复执行。

checkpoint 是可丢弃执行缓存的恢复材料，不得包含明文密钥、隐藏思维链或未授权私人数据。

## 9. Runtime Tool Broker

Node Extension 不直接获得 Git、浏览器、部署或外部写权限。需要工具时发出 `tool.requested`：

```text
Extension emits tool.requested
  -> Runtime validates exact Tool/Capability reference
  -> Dispatcher freezes child Action and idempotency identity
  -> approval when required
  -> Capability invocation and evidence persistence
  -> Runtime posts tool.completed/tool.failed command to Host
```

工具写操作继续走现有 Dispatcher、Approval、write receipt、reconciliation 和 Audit。Pi 只能决定工具调用
顺序，不能绕过这些机制。Broker 命令必须绑定父 operation、Task lease、Tool invocation ID 和冻结 Action。

## 10. Artifact 与模型调用

Host 不能写 Runtime 数据库，也不能把任意本地路径或公网 URL 当作 Artifact。Runtime 为一次 invocation
签发有大小、媒体类型、用户和到期时间限制的 upload grant；Host 上传后提交 SHA-256，Runtime 校验并
物化为私有 Artifact。小型结构化结果可以内联，但受 Remote Connection `max_response_bytes` 限制。

Pi 和其他自带 Agent loop 的 Extension 从第一版开始通过 Runtime Model Proxy，或使用等价的短期、
限额凭证。每次请求前执行模型 allowlist、token 和成本预算检查；`model.usage` 是账本与观测记录，不能
替代事前预算控制。

## 11. 安全与运行约束

- 认证、HMAC、防重放、响应签名和写回执复用 Remote Capability v1；
- Node 依赖必须锁定，生产从不可变 bundle 或 OCI image 启动；
- 密钥只使用部署注入的 `env://VARIABLE`，并按 Extension 最小范围分配；
- 每次调用限制 CPU、内存、wall time、输出、workspace mount、网络域名和并发；
- Shell 只能在隔离容器中运行，容器不可用时失败关闭；
- stdio JSONL 只作为 Host 与本地子进程的私有适配：stdout 仅允许协议帧，日志写 stderr，并限制帧大小；
- Console 只查询 Runtime 在 PostgreSQL 中保存的 Host/operation 投影，不直接连接 Host；
- Event、日志和 Artifact 禁止包含密钥、隐藏思维链和无界供应商请求/响应 Blob。

### 11.1 内置 Node Runtime 版本策略

Desktop、Server bundle 和离线安装包不得使用用户全局 `node`。仓库以
[`hosts/node/runtime-lock.json`](../hosts/node/runtime-lock.json) 固定 Node `v24.19.0` LTS，并记录
macOS、Linux 与 Windows 支持架构的官方 SHA-256。打包脚本下载后必须调用
`scripts/verify-node-runtime-lock.py --platform ... --archive ...` 校验，随后将 Node 二进制作为私有运行组件
放入应用资源目录，不加入用户 `PATH`。

Extension Manifest 可以声明受支持的 Node 主版本范围；实际发行包必须固定一个经过测试的精确补丁版本。
Node 升级是独立、可回滚的 Host Runtime release，必须重新运行 SDK、Host、OpenCLI、Channel 与真实
Extension 契约测试，不能因为用户机器安装了更新版本而自动漂移。

### 11.2 OpenCLI 与浏览器能力约束

OpenCLI 是普通 Node Invocation Extension，不获得 Core 特权：

- 禁止向 Agent 暴露接受任意命令文本的 `opencli.execute`；每个准入命令发布为精确 Capability；
- 上游命令 Manifest 只用于生成发布草稿，Capability Revision 固定 OpenCLI 版本、Manifest digest、
  Extension build、lockfile、SBOM、参数 Schema、目标域名和风险策略；
- 命令通过 argv 数组和 `shell: false` 执行，结构化结果使用 JSON；stdout、stderr 和结果大小均有硬上限；
- OpenCLI 的 `access: read/write` 是风险输入而非最终授权，Runtime 可以提升审批等级，不能被上游声明降级；
- 用户浏览器会话只以不透明 `device_id + browser_profile_ref` 引用，Cookie 和 Profile 数据不能进入 Run、
  Event、Artifact、checkpoint 或 Cloud；
- 下载文件通过 scoped Artifact upload grant 提交，不能把 Host 本地路径直接返回给 Runtime；
- `browser eval`、任意 URL 导航、`external register`、动态插件扫描和运行时插件更新默认禁止；
- 第三方 OpenCLI plugin 必须作为独立签名 Extension release 接受相同的发布、ACK、禁用和回滚治理；
- 发帖、评论、私信、关注、删除、购买和账号修改等副作用必须使用 Runtime 冻结的 Action、Approval、
  idempotency key 和可验证写回执。

## 12. 版本与验收

当前尚未对外发布，Host Profile 和 Remote Connection 可以直接采用更简洁的新契约，不为开发期旧字段
保留兼容分支。正式发布后再启用协议版本兼容纪律。Remote Connection 发布时固定 Host protocol、SDK、
Extension version、build digest 和 lockfile digest；任何新版本预热失败都不得覆盖当前生效版本。

一个 Host/Extension release 必须通过：

1. 重复 invocation、事件和 Tool command 不产生第二次副作用；
2. 相同幂等键不同输入稳定返回冲突；
3. Worker/Host/Extension 任一重启后可以 reconcile 或从 checkpoint 恢复；
4. 未持有当前 reconciliation/Run/Task lease 的 Worker 不能用 Host 事件完成 Task；
5. 审批等待不长期占用 Worker lease；
6. 取消、超时、背压和进程崩溃都产生可审计终态；
7. Secret redaction、跨用户隔离、网络和 workspace 限制有效；
8. Artifact 摘要、模型用量、Tool Action 和写回执可以关联到同一 Run；
9. Extension 独立升级或崩溃不影响其他 Extension；
10. 发布禁用与回滚不会破坏已经存在的 Run；
11. OpenCLI 未准入命令、参数注入、未授权域名、Profile 串用和动态插件加载稳定失败关闭；
12. Device Host 离线、换网、SSE 重连、重复领取和设备撤销不会丢失终态或重复副作用。
