# Joyhousebot 个人数据与智能执行底座架构

本文是当前代码唯一有效的总体架构说明。Joyhousebot 是以个人资源归属为核心、同时支持本地一体化与云端多用户并发部署的智能执行底座。它不是本地单 Agent 聊天客户端，也不兼容 OpenClaw Gateway；多用户并发是 Runtime 能力，当前产品不预设企业租户模型。

## 不可变原则

- 不引入 `tenant_id`。认证主体映射为 `user_id`，会话边界是 `user_id + agent_id + session_id`。
- Agent、Skill、Tool、模型和子 Agent 是平台共享能力；用户状态绝不存放在这些共享对象上。
- Core 不选择供应商或移动模型别名。空目录的初始 Agent 仅从
  `runtime.bootstrapModel` / `LLM_MODEL` 冻结精确模型；既有 Revision 不由迁移脚本改写。
- Capability 的可执行身份是不可变 `CapabilityRef`：`capability_id + version + kind + plugin_id + plugin_version + plugin_build_digest`；
  Scenario、显式 Graph、MCP 调用和 Run payload 都不允许用能力名称解析“最新版本”。
- 公网协议只有版本化 HTTP 与 SSE；不存在公共 RPC 或 WebSocket 命令协议。
- API 只认证、提交和查询；Agent Worker 执行模型与工具；Scheduler 和 Channel Worker 独立部署。
- PostgreSQL 是所有环境的唯一事实源，不提供文件型存储回退。
- Redis 不是必选组件；只能作为可拔插广播/缓存加速层，不能成为 Run/Task 事实源。
- Shell、filesystem、MCP 和外部 URL 等高风险能力均为显式扩展，默认不安装/不启用；启用后仍必须经过 Capability allowlist、权限、配额和审计。Shell 的容器隔离与 fail-closed 策略由 Core 强制。
- 外部 MCP Client 由 `joyhousebot-connector-mcp-client` 提供；HTTP 连接经过 Core SSRF
  校验与 DNS pinning，stdio 默认关闭。Core 只保留对外 `/mcp/` 协议网关和通用 Tool
  Connector 生命周期。
- 每类业务状态使用专用表，禁止恢复通用 JSON `shared_state`。
- 普通用户接口只输出结构化进度摘要、事件、日志和产物；供应商实际返回的推理内容和完整请求/响应只进入受权限控制的诊断面。
- App Market 是 Core 外的远程解决方案分发控制面。作者签名、Market 上架证明和本地权限批准是三层独立
  信任；购买或下载不能自动授予 Capability、安装 Extension 或切换 active Release。协议见
  [App Market 治理与跨实例分发协议](APP_MARKET_GOVERNANCE.md)。

## 运行模型

```text
User
 └─ Session (user_id, agent_id, session_id)
     └─ Run
         ├─ Task / child Run / dependency
         ├─ Event（可续传执行时间线）
         ├─ Log（结构化诊断日志）
         ├─ Artifact（执行产物）
```

聊天、定时任务、Channel 入站和多 Agent 工作流最终都提交 Run。父子关系由 `root_run_id / parent_run_id / parent_task_id` 表达；子 Agent 先持久化再返回 ID，不依赖进程内后台任务。

公共 Run 提交可以携带显式 `allowed_tools` 作为调用方能力上限。字段缺省时沿用冻结 Agent/Scenario 策略；空
数组表示本 Run 禁用全部工具；非空值必须是冻结能力的子集。该上限进入 execution snapshot，Worker 不得把空
allowlist 解释为“使用默认全部工具”。

### App 数据面与身份委托

App Pack 通过声明式 Entry Point 把业务动作精确映射到已发布的 Agent、AgentTeam、Scenario 或
Workflow；Entry Point 只做版本锁定、输入边界和产品命名，最终调用与普通请求共用同一个 Run submission
service，不创建 App 专属状态机。

独立 App 以 `App Client → owner-approved installation Grant → short-lived access token` 接入。委托 scope
同时受 Client allowlist 和安装权限上限约束；Token 绑定一个 `user_id + installation_id`，只能通过对应
Entry Point 启动 Run，并只能查询带同一 App metadata 的 Run。Grant 重新授权、缩权或撤销会事务性撤销
既有 Token，防止旧权限继续存活。

App 的终态回调由 Run 终态事务投影到 `app_callback_outbox`。Scheduler Worker 用 PostgreSQL lease、
fencing 和指数退避投递 HMAC 签名事件；Callback Payload 不复制 Result/Artifact，消费者继续通过绑定
Token 读取 Run。这样 App 获得可靠唤醒，但执行事实源仍唯一留在 Runtime。

### AI Workflow Studio

Workflow 是用户拥有、可版本化的“执行定义”，不是另一套运行时。用户在独立 Studio 页面描述目标，
API 只提交一个 `workflow_design` Run；Agent Worker 在禁用 Tool 的设计上下文中输出受 JSON Schema
约束的 DAG。用户可以查看节点、依赖、Agent 与能力绑定，并继续用自然语言要求修改。每次确认保存都会
产生不可变 `user_workflow_revision`，发布只切换该用户 Workflow 的已发布 revision。

执行时，服务把选定 revision 确定性编译为已有 `TaskGraphSpec`，随后仍走统一的 Run、Task、Lease、
Approval、Event、Artifact、审计和回放链路。Studio 支持三类工作节点：单 Agent、冻结 AgentTeam 子
Run、冻结 fixed Scenario 子 Run；以及四类控制节点：verify、branch、bounded_loop、approval。Team 和
Scenario 子节点先持久化精确子 Run，父 Task 以 `waiting_external` 暂停，子 Run 终态后由任一 Worker
恢复。节点不以拖拉拽作为主要创作方式，避免界面配置与自然语言目标形成两个事实源。草稿可显式试运行，
正式复用默认只接受已发布 revision。

```text
自然语言目标 → design Run（无 Tool）→ 结构化 DAG → 可视化审查/对话修改
                                              ↓
                                      immutable revision
                                              ↓ publish
                                      TaskGraphSpec → Runtime
```

## 部署拓扑

```text
Client ──HTTP/SSE──▶ API replicas ───────────────┐
                                                  ▼
                                             PostgreSQL
                      ┌───────────────────────────┼──────────────────────┐
                      ▼                           ▼                      ▼
               Agent Workers              Scheduler Workers      Channel Workers
```

- Agent Worker 使用数据库 lease、fencing version 和 `FOR UPDATE SKIP LOCKED` claim 工作。
- Scheduler Worker 除 Schedule/Eval/App Market 获取外，也投递 App Callback Outbox；回调请求受公网
  HTTPS、SSRF/DNS pinning、禁止重定向、HMAC 和最大重试次数约束。
- API、Control、Scheduler 和 Migrator 不发现或 import Provider/Capability/Connector 扩展，也不接收
  模型密钥。Agent Worker 加载 Provider/Capability/Connector；Channel Worker 只加载 Channel。
  配置解析只保存通用部署别名，供应商协议识别延迟到 Agent Worker。
- 所有 lease 比较统一使用数据库时钟（database time owns leases）：runtime_runs/tasks、schedules、
  channel lease/outbox 的 claim/续租/finish 都在 SQL 内以 `clock_timestamp()` 判定，不依赖各副本的
  客户端墙钟。心跳续期要求 lease 未过期，过期即失败并走 lease-lost 路径，zombie Worker 不能复活租约。
- 同一会话的顶层 Run 串行；不同用户、会话和子任务可以并发。
- Run 的取消是两阶段的：跨进程 cancel 先在 `runtime_runs` 写入 `cancel_requested_at`/`cancel_reason`
  标记（仅非终态可置）；持有活 lease 的 owning Worker 在下一次心跳（约 lease_seconds/3）或执行检查点
  发现标记后自行中止，并带 fencing 提交终态 `cancelled`。只有 lease 缺失或已过期时，非 owner
  （API 进程或恢复扫描）才允许直接把 Run 强转终态——`finish_runtime_run` 的 fencing 谓词在
  数据库层强制这一点，`worker_id` 为空不再是无条件后门。已被请求取消的 Run 不可被 claim、
  心跳或转入 `running`，且在真正终态前仍以 `running` 身份阻塞同会话下一个顶层 Run 的 claim；
  owning Worker 死亡时由协调器的恢复扫描把标记 Run 推进到 `cancelled`，保证取消最终完成。
- 不完整 Run 的恢复顺序按用户轮转，避免单个用户占满恢复队列。
- Schedule occurrence、Channel lease/outbox、Provider profile health、Memory 和 Knowledge 都是集群共享的规范化状态。
- Channel 投递成功或失败会写 delivery audit；外部连接所有权由带续租的 channel lease 决定。
- Channel 的 PG Outbox、Lease、`RunAdapter` 和 `ChannelRuntimeBridge` 属于 Core；供应商协议属于扩展。
  `ChannelRegistry` 默认为空，只发现 `joyhousebot.channels` entry point，并只启用
  `extensions.allowedIds` 明确准入且 PostgreSQL desired state 已启用的扩展。Email 与其他供应商均为独立 distribution，Core 不保留旧
  适配器、旧配置或任意模块加载入口。完整边界见 `CORE_AND_EXTENSIONS.md`。
- Memory/Knowledge 的 PostgreSQL 事实源、权限策略和隔离服务属于 Core；模型可调用的
  `retrieve`、`memory_get` 和 URL 入库属于可卸载的
  `joyhousebot-capability-context-assets`。扩展只收到当前 Run 的窄服务，不能持有 Repository
  或自行选择 `user_id/agent_id`。

### 定时任务闭环

Scheduler claim `schedules` 后先创建不可变的 `schedule_occurrences` 快照，再使用
`schedule_id + scheduled_for_ms + execution attempt` 生成 Run 幂等键。提交成功只把 Occurrence
推进到 `submitted`，不再把“已提交”误记为“执行成功”。Agent Worker 通过
`finish_runtime_run` 提交 Run 终态时，会在同一个 PostgreSQL 事务内完成以下投影：

1. 以 Worker ID 和 lease version fencing 更新 Run，并写入唯一终态 Event；
2. 写入最终不可变 Artifact；Artifact 冲突会回滚 Run 与 Event，而不是留下半完成状态；
3. 将 Run 的 `completed / failed / cancelled / timed_out` 回写到对应 Occurrence；
4. 对允许重试的失败将 Occurrence 置为 `retry_wait`，由 Scheduler 重新提交下一次 Run attempt；
5. 对最终结果按需写入 `channel_outbox`，再由 Channel Worker 独立投递。

新建的定时 Run 带 submission-ready 栅栏；Scheduler 将 Run ID 绑定到 Occurrence、推进下一调度游标
并提交同一事务后才解除栅栏和唤醒 Agent Worker，避免极速 Run 在绑定前结束而漏掉终态投影。

Occurrence 保存所有 `run_ids`、当前 execution/submit attempt、下次重试时间以及
`delivery_status / delivery_error / delivered_at_ms`，因此控制面可以区分调度、执行和投递三段状态。
Run 重试默认关闭，因为失败前可能已经产生外部副作用；提交重试默认开启并复用同一幂等键。

Schedule 的 `policy` 是版本化 HTTP 契约中的可选增量字段：

- `max_submit_attempts`：提交 Runtime 失败的最大尝试次数，默认 3；
- `max_run_retries`：Run 失败或超时后的额外执行次数，默认 0；
- `retry_backoff_ms`：上述两类重试的指数退避基数，默认 60 秒，单次最多 1 小时；
- `misfire_policy=fire_once|skip` 与 `misfire_grace_ms`：宕机恢复后执行一次，或跳过超过宽限期的旧触发；
- `overlap_policy=serialize|skip`：同一 Schedule 的 Run 通过固定 session 串行，或在前序
  Occurrence 仍活跃时直接记录 `skipped_overlap`。

Channel Outbox 的 deterministic `outbound_id` 防止数据库内重复入队；发送失败按指数退避重试，
达到 `gateway.channelSendMaxAttempts` 后进入 `dead`。由于外部 Channel 未必支持幂等键，发送成功后
Worker 在确认数据库前崩溃仍可能导致外部重复消息，投递语义是 at-least-once，而不是伪装成
exactly-once。

#### Agent Monitor

`agent_monitor` 是现有 Schedule 的一等 Payload Kind，不启动第二套 Heartbeat timer。每次 tick 仍按
`Schedule → Occurrence → Run → terminal projection → Channel Outbox` 执行，因此继承用户隔离、Agent
版本冻结、Capability 权限、重试、审计和回放。

- `session_mode=isolated`（默认）使用稳定的 `monitor:<schedule_id>` 会话，保留该 Monitor 自己的上下文，
  不污染用户主会话；`session_mode=main` 只有显式选择时才进入指定 `session_id`（默认 `main`）。
- `defer_when_busy=true` 时，目标会话存在非终态顶层 Run 就不调用模型，而把同一 Occurrence 放入
  `retry_wait`，按 `busy_backoff_ms` 重试；超过 `misfire_grace_ms` 后记录 `skipped_busy`。
- Monitor 未显式提供 Policy 时默认 `misfire_policy=skip`、`overlap_policy=skip`，避免恢复风暴和
  重叠唤醒。
- Runtime 会把静默契约加入 Monitor Prompt；完成内容精确等于 `quiet_token`（默认 `NO_ACTION`）时，
  Run 和 Occurrence 仍正常完成，但投递状态记为 `suppressed`，不写 Channel Outbox。
- `preflight_mode=runtime_attention` 会先在 PostgreSQL 内确定性地快照当前用户的待处理审批、最近七天
  非 Monitor 顶层 Run 失败以及 dead Channel 投递。首次无信号或快照未变化时，Occurrence 直接记录
  `skipped_unchanged`，不创建模型 Run；快照摘要与 SHA-256 写入 Occurrence，便于解释和回放。
- 每个 Monitor 有独立、用户隔离的 `schedule_monitor_state` 和不可变
  `schedule_monitor_scratch_revisions`。实际提交前，Occurrence 冻结一个 scratch revision；提交重试和
  Run 重试继续读取该 revision，避免同一触发在重试时静默更换上下文。Agent 只能在带有 Monitor
  Run metadata 的上下文中通过 `monitor_scratch` Capability 乐观更新，仍经过统一 Dispatcher。
- `active_hours={start,end,timezone}` 使用 IANA 时区限制自动 tick；窗口可以跨午夜，起止相等表示全天。
  窗口外 Occurrence 明确记为 `skipped_inactive_hours`，手动 Run Now 绕过该限制，原 occurrence 的提交或
  Run 重试不受后续窗口配置影响。
- `context_mode=light` 只注入不可变系统策略、Agent revision、当前 Monitor request 和会话路由；不自动
  注入历史消息、持久记忆、Active Skill 指令或 Skill catalog。Tool schema、Capability 权限和显式检索
  工具不变。`full` 保留正常 Agent 上下文。

Agent revision 可发布 `monitor_policy` 作为 desired state。`enabled=true` 时，用户首次使用该 Agent 后，
Runtime 以 `user_id + agent_id` 的稳定散列对账一个 `managed_by=agent_revision` 的普通 Schedule；不会建立
第二个 timer 或跨用户共享会话。新 revision 发布后会更新已物化用户的 Schedule，遗漏的对账会在该用户
下一次 Run 时修复。托管 Schedule 不能从普通 Schedule API 修改或删除，应通过新 Agent revision 禁用或
调整；`delivery=origin` 只记住真实外部 Channel 来源，API/CLI 来源不会变成投递目标。

安装并授权 `joyhousebot-capability-runtime-control` 后，Agent 可以通过 `cron` Capability 的
`monitor=true` 创建 Monitor，并选择 `session_mode=isolated|main` 与
`preflight_mode=always|runtime_attention`、`context_mode=full|light` 和可选 active hours。内置预检刻意只覆盖 Runtime attention；业务数据变化应由
版本化 Connector/Capability 提供，不允许定时脚本绕过权限层，也不允许 Scheduler 直接执行 Tool。

控制台的“立即补跑”调用 `POST /v1/schedules/{schedule_id}/runs`。API 只 claim 一个带不可变
Occurrence 快照的手动触发并提交 Run，模型和 Tool 仍由 Worker 执行；暂停的个人 Schedule 也可显式
补跑，但补跑不会绕过用户归属、会话串行、Monitor 预检、幂等键或审计链。

### Webhook 与外部事件入口

用户可以通过 `event_triggers` 配置外部事件到 Agent Run 的稳定映射。管理 API 绑定认证用户，入口密钥
在创建或轮换时只返回一次，数据库只保存 SHA-256 摘要。公开接收端点
`POST /v1/hooks/{trigger_id}` 必须同时携带：

- `X-Joyhouse-Webhook-Secret`：验证入口归属，错误密钥统一返回 404；
- `Idempotency-Key`：外部系统稳定事件 ID，缺失时拒绝；
- `{event_type, payload}`：事件类型必须匹配规则，Payload 最大 64 KiB。

每次请求先在 `event_trigger_deliveries` 冻结 Payload hash。相同键与相同 Payload 返回原 Run，相同键与
不同 Payload 返回 409。投递记录只保存 hash、事件类型、状态、尝试次数和 Run ID，不保存原始业务
Payload；实际 Run 仍由 PostgreSQL Runtime 创建，并进入统一 Worker、权限、回放与成果链路。

## 身份与认证

普通用户和自动化请求的 `user_id` 只能来自数据库签发的 Bearer Token。`api_access_tokens` 只保存 SHA-256 指纹，明文仅在签发响应中返回一次；吊销在所有 API 副本即时生效。普通用户不能通过 Header 或请求体指定资源归属。环境变量 `JOYHOUSEBOT_CONTROL_TOKEN` 是紧急 operator 凭据，代用户操作必须显式发送 `X-Impersonate-User-ID`（每次代操作都会写 warning 级审计日志）。认证 fail-closed：没有有效 token 时默认拒绝（401）；仅当显式设置 `gateway.allowInsecureAuth=true` 的开发模式下，`X-User-ID`/`JOYHOUSEBOT_DEV_USER_ID` 才生效，默认用户为 `joyhousebot`，启动时会打印 INSECURE DEV MODE 警告。

控制台管理员另有密码登录链路。`admin_login_credentials` 只保存带随机盐的 Scrypt 哈希，连续失败会在
PostgreSQL 中跨 API 副本锁定；`admin_auth_sessions` 和 MFA challenge 也只保存 Token 指纹并有明确过期时间。
Google Authenticator 使用 RFC 6238 的 30 秒 TOTP、最多前后一个时间窗，并以已接受 counter 阻止同码重放。
TOTP shared secret 用 `JOYHOUSEBOT_AUTH_ENCRYPTION_KEY` 做 AES-256-GCM 加密；恢复码只保存哈希、单次消费，
明文只在激活响应出现一次。管理员会话、密码、TOTP 与恢复码操作全部写入 `platform_admin_events`。

`user_id` 只表达业务资源归属，管理权限来自独立的 `platform_admins` 表。权限按 `runs.read/runs.cancel`、`agents.write/agents.publish`、`tokens.write` 等操作拆分，不存在“只读管理员可以取消 Run”的隐式升级。最后一个拥有 `admins.write` 的启用管理员由数据库事务和 PG advisory lock 保护，不能被并发删除或降权。开发模式首次启动会把默认 `joyhousebot` 显式登记为 `is_test_user=true` 的平台管理员并创建必须修改的本地初始密码；生产仅在同时提供 `JOYHOUSEBOT_BOOTSTRAP_ADMIN_USER` 与 `JOYHOUSEBOT_BOOTSTRAP_ADMIN_PASSWORD` 时执行一次性引导，仓库内没有生产固定密码。

JSON 配置不接受明文 token、API key、password 或 database URL；敏感值只能来自进程环境或 `env://VARIABLE` 引用。Agent、Capability、Scenario 和权限是数据库业务配置，进程配置只保留数据库连接、进程角色和本地执行参数。

所有 Run、Session、Schedule、Memory、Knowledge 和查询都带 `user_id`。平台能力使用 `agent_id` 标识，不复制成每用户一份。

## 公共 API

公共接口位于 `/v1`：

- `POST/GET /v1/runs`；提交必须使用判别联合 `execution.mode=agent|team|scenario|workflow` 指定唯一顶层
  编排权威，旧的顶层 `agent_id/team_id/scenario_id` 混合字段不再接受。其余接口包括 run 的
  cancel、resume、events、tasks、artifacts、logs、
  invocations、verifications、decisions、context-manifest、approvals、operations、pending inputs 和 input resolve。提交可携带
  `output_schema`、由 schema/artifact/deterministic verifier 组成的 `verification_policy`，以及
  `max_repairs`、`max_replans`；所有 required verifier 通过后 Run 才能完成，协调器计划重试耗尽后
  Run 以 `max_replans_exhausted` 明确失败。可修复的 Schema 失败会在同一 Run 的有界修复轮次中向模型
  提供字段路径和违反的约束；公开 Event、Verification API 与终态错误只保留脱敏摘要和 hash，避免把
  私有输出或完整校验细节暴露给无权调用者。
- `POST /v1/runs/graphs` 提交显式 DAG；每个 Task 可声明 `output_schema`、
  `verification_policy` 和 `max_repairs`，普通请求也可由主协调器自动提升为 Graph。Graph 在执行前冻结为
  不可变 revision；`GET /v1/runs/{run_id}/graph-revisions` 返回 owner 隔离的节点、边、定义 hash 和版本来源。
  Graph 与每个 Agent/aggregate Task 还可分别声明 `max_input_tokens / max_output_tokens / max_cost_usd`；
  节点调用先执行局部门槛，最终收敛再次核对全图累计使用量，超限只能以失败终结。
  `branch` 节点只允许从直连上游已验证的 `structured_output` 读取值，并使用 allowlist 运算符选择已冻结的
  直连目标，不执行表达式或模型生成代码。`foreach` 只从已验证数组展开最多 64 个模板实例，并同时受
  节点级和 Run 级并发上限约束；`bounded_loop` 只允许最多 32 轮的显式状态迭代；`wait_event` 冻结
  事件类型、Payload Schema 和最长七天 deadline。
  `approval` 将上游结果 hash 冻结为审批主体，`verify` 验收一个直连上游的不可变结果；`compensation`
  只能调用源 Capability 版本明确声明的 pinned 补偿 Capability。
- `POST /v1/runs/{run_id}/graph-patches` 对非终态 Graph 提交受控变更，当前只接受 `append` 和
  `replace_pending`；`GET /v1/runs/{run_id}/graph-patches` 返回 owner 隔离的原因、提出者、完整变更、差异和
  校验结果。Patch 只能复用父 revision 已冻结的 Agent/Capability/Tool/Skill 范围，高风险变更必须显式
  `approve_high_risk=true`。
- `GET /v1/runs/{run_id}/event-waits` 查询 owner 可见的外部事件等待；
  `POST /v1/runs/{run_id}/event-waits/{wait_id}/token` 轮换并仅返回一次投递 token。外部系统使用
  `POST /v1/run-events/{wait_id}` 和 `X-Joyhouse-Event-Token` 投递；数据库只保存 token SHA-256，Payload
  必须通过冻结 Schema，重复投递只有在事件类型和 Payload hash 相同时才幂等成功。
- `GET/DELETE /v1/sessions`。
- `GET/POST/PATCH/DELETE /v1/schedules`；`GET /v1/schedules/runs` 返回 Occurrence 的 Run
  终态、execution/submit attempt、Run ID 历史、Monitor 预检摘要和 Channel 投递状态；
  `GET/PUT /v1/schedules/{id}/monitor-scratch` 以及其 `/revisions` 查询提供 owner 隔离、乐观并发的
  Monitor 私有状态契约。
- `GET /v1/memory/documents`、`GET /v1/memory/documents/{document_path}` 按认证用户和 Agent
  返回持久记忆台账与文档详情；详情还必须匹配 scope，控制台可查看个人属性、长期、情景和 Agent
  经验层，但平台管理员不能因此越权读取其他用户数据。`GET /v1/memory/candidates` 和
  `POST /v1/memory/candidates/{candidate_id}/resolve` 提供同一 owner 边界内的候选查看、接受与拒绝；
  候选正文属于用户私有数据，不进入公开 Run 事件或 ContextManifest。
- `GET /v1/knowledge/documents`、`GET /v1/knowledge/documents/{doc_id}` 提供 owner 隔离的知识源清单、
  索引汇总、分块正文与来源证据；`DELETE /v1/knowledge/documents/{doc_id}` 删除文档及分块并保留
  `knowledge_asset_events` 审计事件。知识抓取和解析仍由 Worker Tool 执行，HTTP API 不直接访问外部来源。
  当前 `agent_id` 只表示采集来源，知识检索仍按用户隔离；不能把它解释为已发布的 Agent 授权绑定。
- `GET/POST /v1/knowledge/bases` 和 `PATCH/DELETE /v1/knowledge/bases/{knowledge_base_id}` 管理
  owner 私有知识库；`PUT/DELETE /v1/knowledge/bases/{knowledge_base_id}/documents/{doc_id}` 幂等维护
  知识源绑定。知识源可进入多个知识库，删除知识库只级联删除绑定，不删除 `knowledge_documents`；
  创建、更新、归档、绑定和删除进入 `knowledge_base_events`。
- `GET /v1/agents`、`GET /v1/capabilities`、`GET /v1/scenarios`、`GET /v1/me`、
  `GET /v1/usage`。
- `/v1/admin/scenarios` 提供草稿、发布、模拟和能力目录，分别要求 scenarios.read/write/publish。
- `/v1/admin/overview`、`runs`、`workers`、`agents`、`capabilities`、`config` 和
  `users`、`access-tokens`、`rollouts` 和 `configuration-events` 构成平台管理面；全局 Run diagnostics 汇总 Event、Log、Task、Invocation、Trace、
  Artifact 和动态子 Run。只有数据库管理员权限或 control-token operator 可以调用。
- `/v1/admin/runs/{run_id}/reasoning`、`blobs/{blob_id}` 和 `replays` 分别提供原始推理、
  完整请求/响应读取与回放。它们要求 `reasoning.read`、`reasoning.read_raw`、
  `replay.execute` 细粒度权限，并写入审计日志。
- `/mcp/` 提供 Streamable HTTP MCP 网关。已发布且启用的 `tool` / `connector` 能力会动态映射为 MCP tools；
  `tools/call` 不直接执行业务函数，而是创建持久化 Run/Task，复用同一套鉴权、权限、Lease、事件、Trace、产物和回放链路。
  MCP 仅是协议适配层，不构成第二套执行运行时。

SSE 使用事件 sequence 恢复，断开客户端不会取消 Run。Router 只做 DTO、认证上下文和错误映射；业务入口位于 `application/`。

每次真正进入模型的 durable Turn 都先写入不可变 `ContextManifest`，再发出 `context.built`，最后调用
Provider。清单覆盖系统身份、Agent Revision、Memory、Skill、会话历史、当前请求、媒体、Run 指令、
Tool Schema，以及第二轮以后新增的 Tool/Assistant/跟进消息；只持久化来源、数据等级、权限、hash、
Token 估算和纳入/剔除原因，不持久化正文。Run 创建时间作为动态身份时间锚点，恢复时若 request hash
漂移会明确冲突而不会静默换上下文。写入同时校验 Run 或 Task lease version，旧 Worker 不能在接管后
继续发起模型请求。配置 `max_context_tokens` 后使用 `priority_budget_v1`：System 身份、Agent Revision、
当前请求、Run 指令、已选 Skill、授权 Tool Schema 和协议必需消息不可静默删除；历史、Memory 与 Skill
Catalog 按优先级竞争同一预算。超大的 Tool Result 可执行确定性的 `head_tail_v1` 压缩，Manifest 保留
原始 hash、有效 hash、压缩前后 Token 估算；硬约束仍无法放入时，在零次 Provider 调用下以
`budget_exceeded` 失败。Admission 同时记录未发布、被禁用或不可用 Skill 的剔除原因。

Agent 的长期记忆写入统一经过 `MemoryWriteController`。`write_mode=direct` 才能立即更新
`memory_documents`；`write_mode=candidate` 下，Memory 写入扩展和会话归档只能写入
`memory_candidates`，不会修改 PROFILE/MEMORY/HISTORY/每日记录。候选冻结 owner、Agent、目标层、
操作、正文 hash、来源 Run/Task/Turn/Action、策略快照、置信度、有效期、数据等级、证据引用和
supersedes。接受候选时，候选状态转换与文档写入位于同一个数据库事务；append 并发接受至多应用一次，
replace 使用提议时的文档 version/hash 做乐观检查，目标已变化时进入 `conflicted` 而不覆盖新内容。
候选可拒绝、过期，重复同一决议保持幂等。

同一代码可以用 `joyhousebot api --surface public|control|combined` 部署为公网数据面、私有控制面或本地一体化进程。`public` 不注册 `/v1/admin/*`，`control` 不注册用户 Run/Session/Schedule 写接口；`combined` 供内网控制台和本地试用。

## 配置发布状态机

Agent、Capability、Scenario 和远程 Capability 连接发布都不会立即覆盖 current revision：

```text
draft → staged/immutable → rollout(target worker snapshot)
                           ├─ all loaded → automatic activate
                           │             └─ or awaiting_approval → approve → activate
                           ├─ failed/timed_out → retry failed targets or cancel
                           └─ completed → rollback rollout(previous revision)
                                          └─ worker ACK → atomically activate previous
```

发布事务冻结当时健康且具备 `agent` 能力的目标 Worker。每个 Worker 的 revision-aware Runtime Catalog 主动拉取待加载版本并逐机 ACK；新请求仍按 Run snapshot 中的精确 revision 懒加载作为容错。只有全部目标成功后，PG 才原子更新 `agent_definitions.current_revision_id`。因此跨进程发布不要求重启，也不会把流量提前切给未加载版本。Agent 已发布 revision、Skill 绑定、Capability version 和 Scenario version 都不可原地修改。

Skill 不再作为 `CapabilityKind.SKILL` 的运行时特例发布。独立 `skill_definitions / skill_versions` 保存方法内容、依赖、校验证据和 `content_sha256`；Agent / Workflow 使用 `skill_id + version + content_sha256` 精确引用。Skill 发布复用 configuration rollout，Worker 预热校验成功后才切换 current version。完整契约见 [SKILLS.md](SKILLS.md)。

Agent revision 可声明精确 `plugin_requirements`。保存时会校验 PostgreSQL 存在同一
`plugin_id + version + build_digest` 的活跃发布单元；Run snapshot 固化这些依赖，Worker 在执行前比对
自身已加载插件清单，缺失时明确失败而不会换成同名新插件。Capability 发布会校验 JSON Schema 与
精确插件构建，Scenario 发布会校验其精确 Capability 依赖；三类配置使用同一 ACK、超时、取消、
失败目标重试、人工批准和回滚状态机。回滚不是直接改 current pointer：控制面会创建一个指向旧版本的
子 rollout，重新冻结目标 Worker、预热并收齐 ACK 后才切换；回滚预热失败时当前版本继续服务。
插件注册阶段将每个 Capability 绑定到 Manifest 已声明的精确 build digest；缺少 digest 或 digest 与
Entry Point 制品不一致时直接拒绝加载。

远程业务服务使用 `remote_connections` 和不可变 `remote_connection_revisions` 作为控制面事实源。
数据库只保存固定 endpoint、Capability 精确定义和 `env://VARIABLE` 引用；只有 Agent Worker 在
预热阶段解析密钥并构建 Connector。所有目标 Worker ACK 后才原子切换连接指针，再将发现的
Capability 作为独立发布单元走完 Capability rollout。因此“连通服务”不会自动向任何 Agent
授权，远程地址也不能由模型或 Run payload 动态选择。

模型控制面使用 `model_providers` 和不可变 `model_provider_revisions`。Revision 固定 Provider 扩展、
Endpoint、密钥/Header 的 `env://` 引用、请求超时和模型能力目录；密钥值只在 Agent Worker 中解析。
发布预热会构造真实 Provider Adapter、校验精确扩展构建和模型路由，但不会发送模型请求产生费用。全部
Worker ACK 后原子切换 Provider 配置；随后解析的执行使用新 Provider Runtime，切换瞬间已经发出的模型
请求不会被强制关闭。Agent Revision 仍冻结精确模型 ID、降级链和生成参数，Provider 配置不会替代 Agent
版本治理。Agent 发布必须引用生效目录中的 LLM，Provider 发布也会反向校验当前 Agent 依赖，避免一次
目录调整静默破坏正在服务的 Agent。

Plugin Manifest 使用 `runtime_api_version=v1`，冻结包 URI、签名/SBOM 引用、执行隔离、最小权限、
组件目录与 manifest SHA-256。发现插件只创建 `discovered` 发布单元；发布后依次进入 `staged → Worker
loaded ACK → active`，同一插件只有一个 active 版本。Agent、Channel、Connector、Event Trigger、
Knowledge Provider、MCP Server、Scenario、Skill、Tool 和 Workflow 都通过同一组件目录注册。

Capability Registry 同时维护两个索引：模型可见目录按 capability 名称取当前启用版本；已持久化的
Task 和 MCP 调用则按 `capability_id + version` 从版本索引取得 Adapter。后者绝不回退到当前版本。
CapabilityDefinition 还声明 data classification、connection IDs、permissions 和 cost policy；这些是
可审计的能力策略，不保存任何连接凭据正文。`capability_policy.permissions` 是 Agent revision 的
能力授权集：它在提交 Run 后被固化到 execution snapshot，Worker 将其传给 Tool context；模型工具目录
会过滤未授权能力，Dispatcher 在真正调用前再次拒绝未授权能力。支持精确权限、`namespace.*` 和 `*`，
因此元数据声明绝不会只停留在控制台展示层。

`capability_policy.mode=allowlist` 只解析 `allowed` 中的已发布能力；`mode=catalog` 也不是无限授权：
普通能力可随当前目录解析，但声明外部副作用、计费/审批策略或 restricted 数据级别的能力仍必须出现在
`allowed` 中。解析结果写入 Run snapshot 的 `resolved` 集合，Worker 对显式 Scenario、Skill 和 Tool 再做
一次确定性校验；空白白名单表示不允许任何 Tool，而不是“无限制”。能力可见性与
`capability_policy.permissions` 是两道独立门，二者都满足后才允许调用。Console 新建 Agent 默认使用
严格白名单，并在发布前提示未就绪能力和缺失权限。

业务插件（例如 Dinq）不修改核心 Agent 默认配置。插件负责发布自己的 Capability、Scenario、Skill 和
manifest；部署者创建或发布业务 Agent revision，显式写入该插件的 `plugin_requirements` 及最小
`capability_policy.permissions`。因此通用平台可以不安装 Dinq，安装后也能以最小权限运行 Dinq Agent。

已确认的 Scenario inputs 会复制到不可变 Run execution context，并传入每次 CapabilityContext metadata。
这使业务能力可以确定性执行用户确认的约束，即使模型生成 Tool 参数时漏掉字段；插件不需要读取核心表，
也不能把模型输出当成约束事实源。

## Eval、成本和持续质量闭环

Eval Suite/Case、Eval Run、Case Result、执行作业和周期策略都以 PostgreSQL 为事实源。API 创建或排队，
Scheduler 只物化到期 Eval Run；Agent Worker 以 lease/fencing claim `eval_execution_jobs`，崩溃后其他
Worker 可接管。自动 Eval 精确固定目标 revision，旧版本结果不能满足新版本发布门禁。

Suite 和 Release Gate 可同时约束通过率、平均分、总成本、P95 时延与成本观测覆盖率。供应商未返回成本
时不会伪装为已观测的零成本：结果会降低 `cost_coverage`；生产门禁可要求覆盖率为 1。发布决策把所用
Eval Run、实测值、阈值、是否自动执行和结果写入不可变 evidence，便于回放和审计。

## Artifact 与 Work

Runtime Artifact 是 append-only 证据：同一 `artifact_id` 只能精确幂等重放，换内容必须使用新身份；
恢复执行产生的协调计划按 lease version 保留。最终 Artifact 与 Run 终态同事务提交。Capability 返回的
Artifact 自动补齐 Run/Task、Capability、Invocation 与内容 hash provenance。

Work 从指定 Artifact 创建不可变版本，冻结 source digest/object version、Run execution snapshot、
Verification、Action/Invocation 与 evidence manifest。URI Artifact 没有内容 digest 和 object version
不能发布。公开或 unlisted 分享只暴露经过分级的 Work 投影；原始 Artifact URL 永远不是公开链接，
分享链接可过期、撤销并产生访问审计。

## PostgreSQL 数据模型

当前实现的专用表：

- 执行：`runtime_runs`、`runtime_tasks`、`runtime_task_dependencies`、`runtime_events`、`runtime_logs`、
  `runtime_artifacts`、`runtime_workers`、`runtime_turns`、`action_intents`、`action_observations`、
  `loop_decisions`、`approval_requests`、`operation_reconciliations`、`verification_records`。
- 能力：`capability_definitions`、`capability_versions`、`capability_invocations`。
- 场景：`scenario_definitions`、`scenario_versions`、`scenario_fields`、
  `scenario_clarification_nodes`、`scenario_clarification_edges`、`scenario_capabilities`、
  `run_scenario_states`、`run_input_requests`、`run_input_answers`。
- 会话与追踪：`conversation_sessions`、`request_trace_events`、`execution_spans`、
  `model_invocations`、`model_reasoning_segments`、`trace_blobs`、`replay_runs`、
  `model_response_cache`。
- 记忆与知识：`memory_documents`、`memory_candidates`、`knowledge_documents`、`knowledge_chunks`、
  `knowledge_asset_events`、`knowledge_bases`、`knowledge_base_documents`、`knowledge_base_events`。
- 调度：`schedules`、`schedule_occurrences`、`schedule_monitor_state`、
  `schedule_monitor_scratch_revisions`。
- Channel：`channel_leases`、`channel_outbox`、`channel_deliveries`。
- Provider：`provider_profile_health`。
- 网关准入：`api_rate_limits`。
- 平台权限：`platform_admins`、`platform_admin_events`、`api_access_tokens`、`api_access_token_events`。
- 配置发布：`configuration_events`、`configuration_rollouts`、`configuration_rollout_targets`。

业务数据表不属于 Core migration。独立业务服务拥有自己的数据库、迁移历史和部署锁；Capability 或
Connector 只能通过受治理的业务 API 访问它们，不能获得 RuntimeStore、Core 数据库连接或 Core migration
lock。Core 只保存 Run/Task、不可变执行证据、Artifact/Work 和业务写入回执。

每个领域的 migration 执行后都会向 `schema_migration_history` 表记录
`(name, version, checksum, applied_at)`（checksum 为该领域 DDL 脚本的 SHA-256）；重复启动时
checksum 一致则跳过记录，checksum 变化说明 DDL 在应用后被改动，会产生 warning 级日志提示
schema 漂移。该表和 migration lock 只属于 Core schema；扩展与业务服务不得写入 Core migration
历史，也不得借用 Core 数据库连接或锁。它们必须在自己的存储边界内独立迁移和部署。

所有 PostgreSQL schema migration 使用同一个 cluster-wide advisory lock 串行执行，避免 API、
Scheduler 与多个 Worker 并发启动时让不同领域的 DDL 交叉持锁。运行数据保留清理由 Scheduler
承担，并在事务内依次抢占 migration lock 和 purge lock；迁移进行中或已有清理者时立即跳过，
deadlock/lock-timeout 只做有界重试，不会导致执行 Worker 退出。

purge 覆盖执行、事件、日志、产物、Invocation、Schedule occurrence、诊断与追踪等全部运行数据表。
`runtime_events`/`runtime_logs` 被清理前，对应 Run 会在 metadata 写入 `events_purged` tombstone；
SSE 回放命中 tombstone 时先产出 `run.history_purged` 事件向调用方明示，而不是静默缺失时间线。
推理类表（`trace_blobs`、`model_reasoning_segments`、`model_invocations`、`execution_spans`）使用独立的
`JOYHOUSEBOT_DIAGNOSTICS_RETENTION_DAYS` 保留周期（缺省回退到全局 `JOYHOUSEBOT_RETENTION_DAYS`）；
`trace_blobs.expires_at` 是生效字段，purge 优先删除已过期 Blob，读取侧过期即视为不存在。

`JOYHOUSEBOT_DESTRUCTIVE_MIGRATE` 是仅限开发重置的逃生口：只有取值精确等于 `DROP_ALL_TABLES`
才生效（`=1` 等真值不再触发），执行前会以 critical 级日志列出将删除的 runtime 表；生产环境
绝不应设置该变量。

JSONB 只保存单实体 payload/result/options；集合、队列、lease 和状态机必须是可索引行。生产迁移使用 advisory lock；状态提交必须校验 lease owner/version。

## 全链路可解释性、诊断与回放

每个 Run 使用同一个 trace ID 串联主 Agent、子 Agent、Task、模型和 Tool Span。Span 记录父子关系、
Worker、耗时、首 Token 时间和错误；模型调用另存 provider/model/attempt、Token、成本、缓存状态、
供应商请求 ID、请求/响应 hash 与完整 payload。Tool Span 保存输入、结果和错误，Run 事件仍负责面向人的
实时进度。

推理数据必须标记真实性级别：

- `provider_native/exact`：供应商响应或流中实际返回的 thinking/reasoning 块；
- `model_declared/normalized`：模型按协议声明的计划或决策，不冒充内部状态；
- `runtime_decision`：场景路由、调度器、重试和策略引擎的确定性决定；
- `unavailable`：供应商没有返回推理。平台不会声称能够读取模型服务端未暴露的隐藏状态。

原始推理 delta 是 private event，普通用户 SSE 会去掉正文。完整 Prompt、响应、流事件和供应商错误体
保存为带 SHA-256 与大小的 Trace Blob；认证 Header/API Key 从不进入 Blob。诊断台按权限按需读取，
读取行为写审计日志。当前开发/测试配置默认开启
供应商推理参数；生产管理员应将这些表、备份和数据库访问视为最高敏感级别。诊断类数据的保留周期由
`JOYHOUSEBOT_DIAGNOSTICS_RETENTION_DAYS` 独立配置（见"PostgreSQL 数据模型"一节）。

回放分为四类：`offline` 对现有存档重新做解析/对比，`frozen` 固定使用已保存结果，`branch` 从源 Run
创建有父子关联的新 Run，`live` 使用当前外部依赖重新执行。每次回放保存发起人、覆盖项、新 Run ID 和
结果比较。精确模型缓存键包含 provider、model、完整消息/工具、参数和 Agent revision，命中也创建完整
Invocation/Span，避免缓存把追踪链路截断。

## 工具与文件安全

- Core 默认不注册任何模型 Tool；Filesystem、Shell、Research、Context Assets 和 Runtime Control
  均由独立扩展贡献，安装后仍需显式发布、授权并由 Worker ACK。
- shell 扩展只允许经 Core 隔离容器执行，不存在主机执行或自动降级路径。
- 容器不可用时命令执行失败关闭，绝不降级到宿主机。
- 可变文件按 `user_id + agent_id + root_run_id` 映射到私有临时 scratch；不允许读取另一用户或平台工作目录。
- 跨实例持久状态必须写 Memory、Knowledge、Artifact 或业务 Repository，不能把 Worker 本地文件当事实源。
- Memory/Knowledge 直接读写数据库，不启动每 Agent 文件 watcher/subprocess。

## 代码边界

```text
api / bootstrap / channel adapter
                ↓
            application
                ↓
        runtime + domain services
                ↓
       dedicated repositories
```

所有 Python 模块由架构测试限制在 650 行内。`NativeAgentExecutor` 按模型调用、工具运行、轮次引擎、消息处理和记忆生命周期组合；原生 Runtime 按提交、Agent 执行、协调、任务图与控制组合；PG Store 均按 Run、Task/Event、Operations 聚合拆分。Memory、Schedule、Channel、Knowledge、Profile Health 使用独立 Repository。

## 当前代码实现映射

- `api/`：FastAPI composition root、身份依赖、版本化 Router 和 DTO；不加载模型与 NativeAgentExecutor。
- `application/`：用户边界内的 Run、Session、Schedule 用例，以及控制面 Catalog/Rollout 用例。
- `runtime/`：Run/Graph 提交、claim、lease/fencing、执行、事件叙事、取消与恢复；Graph Capability
  Task 通过独立执行模块复用 Durable Turn/Action、审批、外部对账和 Verification。
- `agent/`：共享 NativeAgentExecutor，拆分为模型调用、轮次引擎、Tool runtime、消息处理、记忆生命周期；每次执行状态来自不可变 `RunContext`。
- `storage/`：PostgreSQL RuntimeStore；使用连接池、advisory migration lock、`SKIP LOCKED` 和 LISTEN/NOTIFY 唤醒。空闲 Worker 不做全量扫描：NOTIFY 命中立即扫描，poll 唤醒只做轻量 EXISTS 探测且间隔指数退避（0.2s 起步封顶 2s），另有 30s 深扫兜底防丢通知。
- `scheduling/`、`channels/`、`services/retrieval/`：Schedule、Channel outbox/lease、Knowledge 的专用 Repository。
- `bootstrap/`：分别组合 API、Agent Worker、Scheduler Worker 和 Channel Worker；AgentRuntimeCatalog 按不可变 revision 热加载，不共享进程内业务状态。

一次消息的真实路径是：浏览器提交 `POST /v1/runs` → API 写入 `runtime_runs` 并通知工作 → 任一 Agent Worker 原子 claim → NativeAgentExecutor 产生 Event/Log/Artifact/Task → PG 原子提交终态 → 浏览器按 sequence 通过 SSE 回放。Session 不是独立聊天进程，而是对同一 `user_id + agent_id + session_id` 下 Run 历史的投影。`conversation_sessions.state` 只是 consolidation 缓存：持久化副本只保留最新 200 条消息（`last_consolidated` 随截断平移），事实源始终是 Run 历史。

API 首先解析并冻结唯一执行模式。Agent 模式由所选 Agent 执行；Team 模式由冻结 Coordinator 生成结构化
计划并把步骤绑定到成员 Revision；Scenario 模式只使用指定的已发布版本完成字段校验、追问或固定 DAG；
Workflow 模式直接执行指定的已发布 DAG Revision。四种模式都物化到相同 Run/Task 链，Task 可由不同
Worker/Agent 并行执行并由协调或聚合节点收敛。所有模型输出使用 JSON Schema 校验，所有
Tool/Connector 调用使用 CapabilityResult 和持久 Invocation。

Team 的开放协作和 Workflow 的确定性控制不建立两套运行时。Team Coordinator 产生带
`produce / review / revise / synthesize / checkpoint` 类型的显式无环任务图；Workflow 决定 Team 在更大
流程中的先后、并行、验证、分支、循环和人工门禁。每次嵌套 Team 子 Run 使用独立
`team_workspace_run_id` 作为共享会议空间，而全局 `root_run_id` 只承担整棵执行树的隔离、追踪和审计。
父 Workflow 的取消请求递归传播给所有未终态后代 Run。

显式提交和协调器物化 Graph 时，节点、依赖边、执行设置与 pinned Agent/Capability 定义会先写入
`graph_revisions`、`graph_revision_nodes` 和 `graph_revision_edges`；revision 与 Runtime Task 在同一事务中
创建，数据库拒绝原地更新 revision。Run 和每个 Task 都保存 `graph_revision_id`，因此执行、审计和后续
frozen replay 不依赖可变请求体。安全 `branch` 的选择、分支节点完成和未选目标 `skipped` 也在同一
PostgreSQL 事务中提交，并受 Task lease/version fencing；重复 Worker 不能产生两次不同路由。

`GraphPatch` 不修改父 revision，而是创建 `revision_number + 1` 的不可变子 revision，并在同一事务中
写入 `graph_patches` ledger、切换 Run revision 指针、替换从未启动的 Runtime Task 或追加新 Task 及依赖边。
替换目标及其下游闭包必须仍为 `queued/blocked`，且 `attempt=0`、无 `started_at/result/error/lease`；已完成、
已跳过、等待中、重试过或正在执行的节点均不可改。Patch 使用 base revision 乐观并发，完全相同的请求按
内容 hash 幂等，不同 Patch 竞争同一 base 时只有一个提交。Finalizer claim 会确认没有活动 Task；反向地，
持有有效 finalization Run lease 时拒绝 Patch，因此追加节点与 Run 终态提交不会越过彼此。Patch 仍会重新
执行 DAG 无环、节点上限、foreach fan-out、配置、已发布 pinned Capability、补偿声明和父快照权限校验。

Graph Task 的 `waiting_approval` 与 `waiting_external` 是非终态：暂停由当前 Task lease owner/version
fencing，恢复不增加 attempt，并继续同一个冻结 Action。对账到期后 Worker claim 的仍是原 Task，只查询
既有 provider operation；Task 完成或再次等待时，Graph Run 的等待摘要在同一数据库事务中重新计算。
Verification 在普通 Agent Run 上受 Run lease 保护，在 Graph Task 上受 Task lease 保护。

显式 `approval` 节点复用 `approval_requests`，但 `subject_type=graph_node` 且不创建虚假的 Action；审批
冻结 Graph revision、节点、上游结果 hash、角色、风险和期限。approve 在同一事务内完成审批节点并唤醒
后继，reject/request_changes/expire 明确失败；审批解决、到期与双 Worker 竞争均由审批行和 Task 行锁
串行化。现有 owner/operator API 同时处理 Action 审批和 Graph gate，跨用户仍不可见。

显式 `verify` 节点只读取一个直连上游的冻结结果，复用 Task-lease-fenced `verification_records`，支持
schema、artifact 和 allowlist deterministic verifier；它不会修改或“修复”已完成上游，失败会保留证据并
使节点失败。通过后输出 verified `structured_output`，可安全作为后续 branch/foreach 的数据源。

显式 `compensation` 节点本质仍是一个 durable Capability Action，但提交时必须证明：源节点是直连的
Capability 节点、源 Capability 的不可变定义声明了同一个补偿 CapabilityRef、补偿版本当前可用。执行会
关联 source Task/Action 与 compensation Action，并产生 `compensation.started/completed/failed` 事件；
补偿自身仍遵守审批、幂等、对账和 Task lease 规则。Graph 的自动 Saga failure policy 会按已完成副作用
的逆拓扑顺序创建确定性补偿 Task；调度账本、状态推进和 Run 收敛都在 PostgreSQL 中完成，并由 per-run
advisory lock、行锁与 lease/version fencing 保证双 Worker 只有一个调度结果。没有显式 compensation 的
副作用不会被假装回滚，而是进入明确的 failed/escalated 状态。

`foreach` revision 冻结的是模板和 `max_items/max_concurrent`，运行时实例不是可变 GraphPatch：Worker 从
已验证的上游数组计算确定性 expansion ID、item hash 和 child Task ID，再用一个事务插入全部子 Task、
增加父节点动态依赖并更新 `total_task_count`。父节点等待子项后做确定性聚合；恢复或整图 resume 复用同一
展开，不重复创建子项。最终 Graph 聚合只消费顶层节点，动态子 Task 仍完整保留在 Task/Event/Turn/Action
时间线中，Token 与费用只按真实执行节点统计一次。

`bounded_loop` revision 冻结初始状态来源、`state_path`、安全退出条件、`max_iterations` 和单轮模板。
初始状态只能来自直连上游已经验证的 `structured_output` 或不超过 64 KiB 的静态 JSON；每轮模板必须声明
`output_schema`，退出判断仅支持 allowlist 运算符，不解释表达式，也不运行模型生成代码。Worker 每次只在
一个 PostgreSQL 事务中创建一个确定性 child Task、增加父节点动态依赖、更新 `total_task_count` 并释放父
Task lease；下一轮状态只读取该 child 已验证的 `structured_output`。循环最多 32 轮且严格串行，父节点
`max_attempts` 固定为 1，后续 claim 复用原 attempt 和已提交迭代账本。Worker 崩溃或整图 resume 会复用原
child ID；达到上限产生唯一 `loop.exhausted` 并明确失败，子迭代失败由父循环节点收敛为明确失败后再应用
Graph 的 fail-fast 策略。

`aggregate` 是独立节点而非协调 Agent 的隐式特例。它只读取冻结上游结果，支持
`structured_merge/evidence_merge/rank_and_select/raw/llm_synthesis`；确定性模式校验结构、证据与排序，
LLM synthesis 固定模型策略并保存输入摘要和验证记录。聚合输出经过 `output_schema` 后才可成为后续
branch/foreach/verify 的输入。

模型或 Agent 可以提交 GraphPatch proposal，但不能直接改运行图。proposal 冻结 base revision、差异、
理由、风险、提出者和完整校验结果；低风险策略可自动批准，高风险进入独立 approval 状态。批准后才在
同一套 GraphPatch 乐观并发与 Task/finalizer 锁下激活，不批准或过期不会改变当前 revision。

`wait_event` 使用独立 `graph_event_waits` 状态机。Task lease owner 原子创建等待并进入
`waiting_external`；token 可轮换，签发日志记录 actor 与版本，但明文不入库、不进 Event/Log。投递事务
锁定等待记录，校验当前 token、
事件类型、deadline 和 JSON Schema，再同时写入 Payload hash、完成 Task、唤醒后继和恢复 Graph Run。
deadline 到期由 PostgreSQL `SKIP LOCKED` 单赢家转为 `expired` 并使 Task 明确失败；取消 Run 会撤销所有
pending wait，旧 token 不能恢复已取消或已过期执行。

追问不是普通聊天文本，而是冻结在 Run 上的 `InputRequest` 协议：一个字段可声明文本、单选、多选、
确认或数字控件，选项标签、`Other`、最少/最多选择数和展示说明随场景版本保存。用户回答写入
`run_input_answers`，Run 从 `waiting_input` 原子恢复；DAG 边按受限条件表达式（例如
`goal == 'recruit'`、`present(city)`）和 priority 决定下一题。主协调器在未命中场景、且确实缺少
执行所需信息时，也可以产生最多四个非敏感的动态 InputRequest；回答保存在 Run metadata 后重新进入
协调，不会新建会话或丢失审计链路。

## 前端控制台

Vue 前端是平台运行、管理、监控、配置控制台，同时保留一个用户态 Agent 试用面：

- 监控概览读取平台全局 Run/User/Session/Token/Worker 指标。
- 运行中心使用管理 API 查询所有用户 Run；详情统一展示 Task、Event、Log、Artifact、
  Capability Invocation、Request Trace、模型调用、原始推理、性能瀑布、回放对比和动态子 Agent。
- 配置导航分为运行治理和通用能力配置两组。平台负责访问控制、集群发布、审计和运行摘要；插件中心、Agent、Skills、Tools、MCP Server 在配置子菜单中分别维护。核心控制台只读取通用插件 Manifest、组件、Quickstart、健康与调用，不包含 Dinq 等业务专属导航、路由或字段。
- 场景工作台负责路由、追问 DAG 与执行策略配置，可编辑单选、多选、Other、选项说明和条件边；试用页将
  InputRequest 渲染为可提交的交互卡片，并显示题目进度。
- Agent 试用仍以当前 `user_id` 提交普通用户 Run，用于验证真实业务链路，不绕过用户隔离。
- Eval 工作台维护版本化 dataset、case、确定性 scorer、观察结果和发布门禁；Agent、Scenario、Capability
  只有通过精确 revision gate 才能激活，失败继续保留旧版本。
- Work 工作台把 Run Artifact 转为不可变成果版本，支持 private/unlisted/public、数据分级、协作者、
  可撤销且可过期的固定版本分享链接和访问审计。Artifact 本身永远不因生成而自动公开。
- 代用户操作是显式动作：管理员可在密码登录时分别填写管理员账号与本次操作的个人 `user_id`，也可在
  常驻入口切换；只有两者不同时才发送 `X-Impersonate-User-ID`（选择存 sessionStorage，关标签页即
  失效，代操作中界面有常驻醒目提示）。后端只允许它改变用户数据 API 的资源归属，`auth/admin/system`
  控制面仍使用真实管理员；`/v1/me` 同时返回资源主体和管理员 actor。control token 只存
  sessionStorage，不落 localStorage。

## 已删除且不得恢复

- OpenClaw compatibility 以及旧 Gateway 风格的 device pairing、client Node 和 control plane。未来本地执行节点必须通过 Joyhousebot 自己的版本化协议、用户边界和统一 Run/Task 状态机接入。
- `/ws/rpc`、`/ws/chat`、`/ws/agent-stream` 和两套 HTTP/RPC handler。
- 进程内业务队列、通用 shared-state、旧 heartbeat scheduler。
- 单一全局 wallet/x402、旧本地 identity/task/knowledge service 入口。
- API 进程里的 NativeAgentExecutor、Cron loop 和第三方 Channel 长连接。
- 进程内子 Agent、本地 JSONL Session、主机 shell/process、外部 Agent CLI/SDK 适配。
- 动态插件主机、Browser server、Mochat 本地 cursor/polling 实现和运行时安装 Skill。
