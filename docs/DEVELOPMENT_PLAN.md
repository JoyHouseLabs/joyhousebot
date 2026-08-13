# Joyhousebot 生产化研发基线与后续计划

本文记录 2026-08-08 代码的真实状态，是当前唯一有效的研发计划。总体边界以
`ARCHITECTURE.md` 为准。项目尚未上线，不提供旧单机架构、OpenClaw、RPC、文件式 Agent
或明文 token 配置兼容层。

## 1. 已完成的生产基线

### 1.1 多用户分布式运行时

- 资源边界固定为 `user_id + agent_id + session_id`，不引入 tenant。
- API、Agent Worker、Scheduler、Channel Worker 可独立多副本部署。
- PostgreSQL 使用 lease、fencing version、`SKIP LOCKED`、advisory lock 和 NOTIFY；它是所有
  环境的唯一持久化实现。
- Run、Task、DAG、Input Request、Schedule、Channel outbox、Session、Memory、Knowledge、
  Artifact 均为专用持久表；没有进程内事实源或数据库回退。
- 子 Agent 和并行 Task 先持久化再调度，Worker 退出后可由其他实例接管。

### 1.2 主协调与业务场景

- 请求先经过确定性场景路由和结构化协调决策，再选择单 Agent、固定 DAG 或动态 Graph。
- Scenario 支持字段 Schema、单选/多选/Other 展示协议、追问节点、受限条件边、能力白名单、执行策略和 Studio 模拟；用户回答始终恢复同一个 Run。
- Tool、Skill、Agent、Workflow、Connector 使用统一 CapabilityDefinition/Invocation/Result。
- Agent、Capability、Scenario 是平台共享版本化资源，不为每个用户复制。

### 1.3 配置发布治理

- `agent_definitions` 保存稳定身份，`agent_revisions` 保存不可变策略版本。
- Skill 只能绑定 draft Agent revision；发布后正文和绑定都不可修改。
- Agent、Capability、Scenario 发布统一创建 `configuration_rollouts` 和固定的
  `configuration_rollout_targets`，不会再由 Capability/Scenario 绕过集群校验直接切换。
- Worker 的 AgentRuntimeCatalog 主动预热精确 revision、Capability 插件构建和 Scenario 依赖，
  并记录 loaded/failed ACK。
- 全部目标成功后才原子切换 current revision；失败时旧版本继续服务。
- 发布支持自动激活或人工批准、超时、显式取消、失败节点重试和显式回滚；重试保留已成功节点，
  回滚只允许切回发布前冻结的 revision。自动回滚策略在切换前失败时保护旧指针，不制造短暂错误流量。
- Run 创建时固化 Agent revision 与 Skill 绑定，回放不会被后续发布漂移。
- CapabilityRef 已固定到产生它的插件发布单元；Scenario、Graph 和 MCP 任务只持久化完整引用，不会
  因同名 capability 发布新版本而漂移。
- Agent revision 的 `plugin_requirements` 在保存、Run snapshot 和 Worker 执行三处校验精确 digest。
- Plugin release 同版本不可覆盖 build digest；Worker 观测会分别展示已加载、版本不匹配和真正
  execution-eligible 的节点。Capability 声明已包含数据分级、连接依赖与成本策略字段。
- Capability 权限已进入执行边界：Agent revision 的 `capability_policy.permissions` 随 Run snapshot
  固化，既过滤模型可见目录，也在 Dispatcher 处二次校验；业务插件不得只在 UI 元数据中声明权限。

### 1.4 控制面与认证

- HTTP surface 可选 `public`、`control`、`combined`，公网数据面不必注册管理 Router。
- Bearer Token 由数据库签发，`api_access_tokens` 只保存哈希；明文仅返回一次并可即时吊销。
- `platform_admins` 独立保存角色和细粒度权限；最后一个 `admins.write` 权限由事务保护。
- 读 Run 与取消 Run、读 Agent 与发布 Agent、Scenario draft 与 publish 均为不同权限。
- Agent/Capability 草稿与发布、逐 Worker rollout、权限目录、Token、配置事件已有管理 API。
- 配置文件拒绝明文 API key、token、password 和 database URL；密钥来自环境或
  `env://VARIABLE`。紧急 operator token 只允许 `JOYHOUSEBOT_CONTROL_TOKEN`。

### 1.5 可观测、解释与回放

- Run Event、结构化 Log、Artifact、Capability Invocation、Request Trace、执行 Span 和模型
  Invocation 形成同一 trace 时间线。
- 保存 provider/model/attempt、TTFT、duration、usage、cost、cache status、请求/响应 Blob hash。
- 供应商实际返回的 reasoning/thinking 以 `provider_native/exact` 保存；模型未暴露时明确标记
  `unavailable`，不伪造隐藏状态。
- 原始 Prompt/响应/推理需要独立权限，读取行为写审计；普通用户只得到安全进度摘要。
- 支持 offline、frozen、branch、live 回放；缓存命中仍创建完整 Span/Invocation。

### 1.6 管理与试用 UI

- Overview 展示数据库全量聚合指标，不再用最近 1000 条冒充总量。
- Runs 展示瀑布、模型调用、推理、日志、Task、子 Run、Artifact、Trace Blob 和回放。
- 平台页按访问控制、集群发布、审计、运行摘要分区；插件中心、Agent、Skills、Tools、MCP Server 和 Channels 在配置子菜单中分别维护。业务插件只通过通用 Manifest、Quickstart、组件、健康与调用界面进入核心控制台。
- 管理员可用角色模板选择权限、签发/吊销 Token、创建 Agent draft、发布 revision、发布
  Capability，并观察每个 Worker 的 ACK/失败。
- Chat 是真实用户链路试用，不绕过身份、Session、Run 或 Worker。

### 1.7 Schedule 与 Agent Monitor 闭环

- Schedule、Occurrence、Run terminal projection 与 Channel outbox 已形成 PostgreSQL 闭环；提交重试、
  显式 Run 重试、misfire、overlap、quiet delivery 和 dead-letter 都有可查询状态。
- Agent Monitor 复用 Scheduler claim loop，不维护第二套 heartbeat timer；支持 Runtime attention 预检、
  版本化 scratch、busy defer、active hours 与 light context。
- Agent revision 的 `monitor_policy` 是审计化 desired state。Runtime 按用户首次使用对账一个托管
  Schedule，revision 发布更新既有用户，仍保持 `user_id + agent_id + root_run_id` 数据边界。

### 1.8 Knowledge K4 规模与质量闭环

- Embedding Profile 冻结价格、成本预算、集群请求/Token 速率和 ANN 参数；所有执行写用量证据。
- 小语料保持精确向量检索，大语料按 Profile 通过并发 DDL 构建 HNSW，索引状态由 Worker 定期对账。
- 模型/维度迁移使用 owner-scoped 重嵌入作业，具备幂等提交、lease/fencing、重试、接管、取消和终态闭合。
- Retrieval Eval 使用隔离语料和统一 Run/Task/Capability 链验证精确 Draft Profile，并可作为发布门禁。

## 2. 智能执行底座 V2 改进方案

### 2.1 目标与适用边界

V2 把当前“模型调用工具直到回复”的 Agent loop，提升为可持久化、可审批、可验证、可补偿的
工程执行闭环。目标状态如下：

```text
Goal
  │
  ▼
Plan ──▶ Turn ──▶ Action Intent ──▶ Approval ──▶ Invocation
  ▲          │             │              │             │
  │          │             └── rejected ──┘             ▼
  │          │                                      Observation
  │          │                                           │
  │          └──────── repair / replan ◀── Verification ◀┘
  │                                             │
  └──────── bounded graph patch                 ├── passed ──▶ Commit / Artifact
                                                └── failed ──▶ Compensate / Escalate
```

V2 仍保持以下边界：

- Joyhousebot 负责通用执行、治理和审计，不吸收 Smart Study 等业务项目的数据模型与页面逻辑。
- Capability 仍是唯一副作用入口；Agent 不直接连接业务数据库，也不能绕过权限、审批和幂等边界。
- PostgreSQL 仍是唯一事实源。任何恢复所需的计划、动作、观察、审批和验证结果都必须先持久化。
- 模型只提出计划、动作或 Graph Patch；数据库状态机负责决定能否执行，模型输出不能直接改变状态。
- 低风险草案与研究可以自动执行；写业务数据、对外发送、发布、支付和权限变更必须按策略审批。

### 2.2 Durable Loop：可恢复的执行循环

当前 Turn 内的 message、iteration 和待执行 tool call 主要存在于 Worker 内存。V2 增加持久化执行协议，
将一次 Agent Turn 拆成以下状态：

```text
planned → model_requested → action_proposed → approval_pending
                                     │                  │
                                     │ auto-approved    │ approved
                                     ▼                  ▼
                                  invoking → observed → verifying
                                                            │
                                  completed ◀── passed ─────┤
                                  replanning ◀─ repairable ──┤
                                  failed ◀──── terminal ─────┘
```

新增规范化记录：

- `runtime_turns`：`turn_id/run_id/task_id/turn_index/status/model/started_at/finished_at/stop_reason`。
- `action_intents`：冻结 CapabilityRef、规范化输入、风险、预算、审批策略和 `action_id`。
- `action_observations`：保存外部 operation ID、结果摘要、Artifact、错误、重试和对账状态。
- `loop_decisions`：记录 continue、finish、repair、replan、escalate 的结构化原因，不保存隐藏思维链。

动作身份按以下内容确定性计算：

```text
action_id = sha256(
  run_id + task_id + turn_index + action_index +
  capability_ref + canonical_json(input)
)
```

数据库对 `action_id` 建唯一约束。Capability handler 必须收到同一个 `idempotency_key`，业务插件的写接口
也必须以该键去重。Worker 恢复时从最后一个持久化状态继续：已 `observed` 的动作不能重新调用；处于
`invoking` 且外部结果未知的动作进入 `waiting_external`，通过查询 operation ID 对账，不能盲目重放。
对不支持幂等键或结果查询的外部系统，平台不能承诺 exactly-once；这类 Capability 必须声明为高风险，
默认人工确认，并在结果不确定时转人工对账，不能自动重试。

Loop 增加明确护栏：

- `max_turns`、`max_replans`、`max_repairs`、Token、费用和 wall-clock deadline 分别限制。
- 连续重复相同动作或连续无进展触发 `loop_stalled`，不能继续消耗模型。
- 达到最大轮数且没有通过验证时以 `loop_exhausted` 失败或进入人工处理，禁止提交空结果为 completed。
- 模型/Capability 错误按 retryable、side_effect、idempotent 分类；非幂等副作用不自动重试。

### 2.3 Approval：真正的人机协同状态机

现有 `waiting_approval` 状态和事件仅作为协议占位。V2 增加 `approval_requests`：

- 冻结 `run_id/action_id/capability_ref/input_hash/input_preview/risk/cost/data_classification`。
- 记录 `requested_by/required_role/status/expires_at/resolved_by/resolution_note`。
- 审批对象只能批准被冻结的动作；参数变化必须生成新 action 和新审批。
- 审批必须支持 approve、reject、request_changes、expire 和 revoke-before-invoke。
- 审批解决和实际调用之间使用同一事务/claim 约束，防止两个 Worker 重复执行。

默认策略矩阵：

| 副作用类型 | 默认策略 |
|---|---|
| 只读检索、计算、草案 | 自动 |
| 创建任务、写资料、修改计划 | 用户确认或业务策略批准 |
| 对外消息、公开发布、覆盖/删除 | 强审批 |
| 支付、权限、凭据、未成年人敏感处置 | 专门角色审批；不允许普通 Agent 自动执行 |

公共用户 API 只允许处理属于自己的审批；组织/平台审批走控制面权限。审批详情中的敏感输入按
Capability data classification 脱敏，原始参数只在有权限的诊断面可见。

### 2.4 Verification：完成必须有证据

JSON Schema 只证明输出格式合法，不能证明业务目标完成。V2 为 Agent、Task 和 Scenario 增加
`verification_policy`，支持组合验证器：

- `schema`：结构校验。
- `artifact`：要求指定名称、媒体类型、数量和 hash 的产物存在。
- `deterministic`：运行纯函数规则，例如日期、权限、去重、数据完整性。
- `capability`：调用固定版本的验证 Capability，例如测试、事实核验、业务 API 查询。
- `human`：人工验收。
- `rubric`：只用于主观质量评分；必须保存 rubric、模型和阈值，不能替代确定性业务校验。

新增 `verification_records`，记录 verifier 版本、输入摘要、证据、分数和结论。执行结果只有在所有
required verifier 通过后才能进入 completed；失败可按策略进入 bounded repair/replan，超过次数后
failed 或 waiting_approval。每次修复必须产生新 Turn，但沿用同一 Run 和不可变执行快照。

### 2.5 Context Engineering：上下文清单与预算

现有 ContextBuilder 已能组合 Agent Revision、Skill、Memory、历史和检索结果，但 V2 必须让每一段
上下文的来源、权限和预算可解释。新增 `ContextManifest`，每个 entry 至少包含：

```text
entry_id / source_kind / source_id / owner_scope / classification /
authority / freshness / content_hash / estimated_tokens / priority /
included_reason / citation_id / redaction_policy
```

上下文构建分四步：

1. Admission：按用户、Agent、Scenario、Capability 权限和数据等级筛选候选。
2. Budget：统一计算 system、Agent instructions、Skills、Memory、历史、检索和工具结果，按优先级分配。
3. Compression：优先保留规则、最新用户指令和高权威证据；压缩内容必须保留 source/citation。
4. Manifest：将最终选择、剔除原因、hash 和 token 估算写入 Run，支持回放与问题定位。

个人记忆写入改为真正的候选流程：

```text
memory_candidate → policy review → user/business approval → merged
                                      └── rejected / expired
```

`write_mode=candidate` 只能写候选箱，不能直接改 PROFILE/MEMORY/HISTORY。候选项包含来源 Run、事实类型、
置信度、有效期、敏感等级和 supersedes 关系；冲突事实并存并明确新旧关系，不允许静默覆盖。

知识检索已有 source trace；V2 将 citation ID 传到最终 Artifact，并可配置“无可追溯来源不得形成事实结论”。
来自网页、文档和工具的文本均按不可信数据处理，不能覆盖系统指令、Capability allowlist 或审批策略。

### 2.6 Graph V2：动态但受控的执行图

当前 Graph 是可靠静态 DAG。V2 在不引入任意代码表达式的前提下增加以下节点类型：

- `agent`：执行固定 Agent revision。
- `capability`：调用固定 CapabilityRef。
- `branch`：基于已验证结构化输出执行安全条件分支。
- `foreach`：对有界集合扇出，必须声明最大项数和最大并发。
- `bounded_loop`：显式状态迭代，必须声明最大轮数、已验证状态路径和安全退出条件。
- `aggregate`：结构化合并、证据合并、排名或 LLM synthesis。
- `verify`：执行 verification policy。
- `approval`：等待审批。
- `wait_event`：等待带 token/schema/deadline 的外部事件。
- `compensation`：对已完成副作用执行明确补偿动作。

执行图以不可变 `graph_revision` 固化。运行中的模型只能提交 `GraphPatch`，Patch 必须：

- 只追加或替换尚未开始的节点，不能修改已完成节点及其证据。
- 引用已发布、已固定版本且在 Run 快照允许范围内的 Agent/Capability。
- 通过无环、fan-out、预算、权限、数据分级和最大节点数校验。
- 保存 patch 原因、提出者、差异和验证结果；高风险 Patch 需要审批。
- 循环只能通过显式 bounded loop 表达，必须声明 `max_iterations` 和退出条件。

Graph failure policy 支持 fail-fast、continue、retry、repair、compensate、escalate。补偿采用 Saga 语义：
Capability 必须显式声明 compensation capability 或 `none`，平台不能假设任意写操作可自动回滚。

### 2.7 Eval 与持续改进

现有 offline/frozen/branch/live replay 是 Eval 的执行基础。V2 增加：

- `eval_datasets`：版本化真实脱敏案例、合成边界案例和安全红队案例。
- `eval_runs/eval_cases/eval_scores`：固定 Agent/Scenario/Capability/Provider 快照后的执行结果。
- Scorer：确定性断言、Artifact 检查、成本/延迟、人工评分和版本化 rubric judge。
- 发布门禁：Agent/Scenario/Capability revision 在激活前必须通过声明的回归集与安全阈值。
- 线上反馈只进入候选改进池，不自动修改 Prompt、Memory Policy、Graph 或 Capability 权限。

核心指标至少包括完成率、验证通过率、人工接管率、重复副作用率、恢复成功率、平均修复轮数、
Token/费用、P95 时延和用户采纳率。

### 2.8 API、事件与兼容策略

新增用户面 API：

- `GET /v1/runs/{run_id}/turns`、`actions`、`verifications`（前三者均已实现）。
- `GET /v1/runs/{run_id}/decisions`，只返回结构化原因、次数和 hash，不返回 Prompt 或完整计划。
- `GET /v1/runs/{run_id}/approvals`。
- `POST /v1/runs/{run_id}/approvals/{approval_id}/resolve`。
- `GET /v1/runs/{run_id}/operations`。
- `POST /v1/runs/{run_id}/operations/{reconciliation_id}/resolve`，支持人工确认成功、确认失败和重新查询。
- `GET /v1/runs/{run_id}/context-manifest`，普通用户只返回脱敏摘要。
- `GET /v1/runs/{run_id}/graph-patches` 与 `POST /v1/runs/{run_id}/graph-patches`（owner 受控
  append/replace-pending 已实现；模型 proposer 与独立 Patch 审批工作流后续开放）。

新增事件：`turn.*`、`action.*`、`approval.*`、`operation.reconciliation_*`、`verification.*`、`context.built`、`graph.patched`、
`compensation.*` 和 `loop.stalled/exhausted`。所有事件继续使用 sequence、visibility 和 SSE 恢复协议。

数据库迁移全部采用新增表、可空列和回填默认值：

- 旧 Run 标记 `execution_protocol_version=1`，维持当前读取与回放。
- 新 Agent revision 可显式启用 V2；稳定后再把默认版本切到 2。
- V1 Tool Adapter 可以写入兼容 action ledger，但不能伪造历史审批或验证记录。
- 已发布 Agent/Capability/Scenario 不原地修改；新字段进入新 revision。
- 插件接口保持兼容，但 V2 写能力必须消费平台传入的稳定 idempotency key。

### 2.9 实施阶段与完成定义

#### 阶段 A：执行完整性（P0）

- Durable Turn、Action Intent、Observation 和确定性幂等键。
- Worker crash 后从动作状态恢复，不重复已完成副作用。
- loop stalled/exhausted、max_replans/max_repairs 和明确失败终态。
- ApprovalRequest、参数冻结、审批 API 和恢复执行。
- Schema/Artifact/Deterministic verification 及失败修复链。

完成定义：故障注入覆盖“动作前、外部调用中、调用成功未落终态、审批后执行前”四个时间点；重启后
结果确定，副作用最多一次，未知结果进入对账而不是重放。

#### 阶段 B：上下文与 Graph（P1）

- ContextManifest、全量预算、来源引用和 memory candidate。
- branch/foreach/wait_event/approval/verify/compensation 节点。
- Graph Revision、受控 Patch、bounded loop 和 Saga failure policy。

完成定义：同一 Run 的 frozen replay 可以重建同一上下文清单与 Graph revision；条件、扇出、等待、
审批和补偿均有 PostgreSQL 双 Worker 并发测试。

当前进度：ContextManifest 第一条纵切已完成。每个模型 Turn 在 Provider 调用前保存来源级 hash、权限、
分类、Token 估算、纳入/剔除原因及 Tool/后续消息，并由 Run/Task lease fencing；提供 owner 隔离且脱敏的
`GET /v1/runs/{run_id}/context-manifest` 与确定性 `context.built` 事件。动态时间已锚定 Run 创建时间，
恢复时上下文漂移会失败而不是静默替换。全量 `priority_budget_v1` 已覆盖 System、Agent、Skill、
Memory、历史、当前请求、Tool Schema 和后续 Tool/Assistant 消息；低优先来源会从真实请求中移除，
Tool Result 可做保留原始 hash 的确定性首尾压缩，硬约束溢出在 Provider 调用前明确失败。Memory
Candidate 纵切也已完成：`write_mode=candidate` 的 Memory 写入边界与会话归档统一写入 PostgreSQL 候选箱，
候选冻结来源、策略、事实属性、正文/基线 hash 和过期时间；owner API 支持查看、接受、拒绝，接受与
文档合并同事务，重复接受幂等，替换目标发生并发变化时进入冲突而不静默覆盖。Graph V2 第一条纵切
也已完成：显式提交与协调器物化都会先冻结不可变 Graph revision，Run、节点、边和 Runtime Task 在
同一事务中关联，数据库拒绝 revision 原地更新；owner API 可查询完整冻结定义。`branch` 节点只读取
直连上游经过 `output_schema` 验证的 `structured_output`，只支持 allowlist 运算符，并原子完成分支节点、
跳过未选的冻结目标；双 Worker 由 Task lease/version fencing 决出唯一结果。Graph V2 第二条纵切也已
完成：`foreach` 将冻结模板按已验证数组有界展开，最多 64 项、节点并发最多 32，并在 PostgreSQL 事务中
创建确定性 child Task、动态父依赖与准确总数；两个 Runtime 共享节点并发门禁，失败后整图 resume 复用
原展开。`wait_event` 已有独立 PostgreSQL 状态机、owner 查询和 token 轮换 API、无用户 Bearer 的专用
投递入口、token hash 存储和带 actor/version 的签发审计、事件类型/Schema/deadline 校验、重复投递 hash 冲突保护以及到期双 Worker
单赢家。Graph V2 第三条纵切也已完成：显式 `approval` gate 冻结上游 hash 并复用 owner/operator 审批
API，approve/reject/expire 与 Task 状态在同一事务提交；显式 `verify` 对一个冻结上游执行 Task-fenced
schema/artifact/deterministic 验收并输出可供 branch/foreach 使用的 verified structured output；显式
`compensation` 仅允许调用源 Capability 不可变定义声明的 pinned 补偿版本，复用 Action、审批、对账和
幂等链路，并关联 source/compensation Action 及专用事件。Graph V2 第四条纵切也已完成：owner 可基于当前
base revision 原子追加节点或替换未启动节点；每次 Patch 都创建不可变子 revision，保存原因、提出者、完整
变更、diff、风险与校验结果，并重新验证 DAG、fan-out、预算边界、发布版本和父 Run 快照范围。重复请求按
内容 hash 幂等，并发 base revision 只有单赢家；目标、下游 Task 与 finalizer Run lease 的数据库锁阻止
修改已启动工作或越过新节点提前完成。高风险 Patch 要求 owner 在请求中显式确认。Graph V2 第五条纵切
也已完成：`bounded_loop` 冻结最多 32 轮的状态机、已验证输入/输出路径、allowlist 退出条件与单轮模板；
每轮只原子创建一个确定性 child Task，并由 Task lease/version fencing 提交，双 Worker、租约恢复和整图
resume 都复用已提交迭代，不重复执行成功轮次；耗尽产生唯一 `loop.exhausted` 明确失败。模型 proposer、
独立 Patch approval 状态机、自动 Saga failure policy 和独立 aggregate 节点均已完成：Agent proposal
只生成持久 proposal，批准后才激活不可变子 revision；Saga 按逆拓扑顺序调度明确补偿并以 per-run
advisory lock/行锁防止双 Worker 死锁或重复补偿；aggregate 支持确定性结构/证据/排名合并及受控 LLM
synthesis。阶段 B 的核心执行闭环已关闭，剩余工作转为规模压测、更多业务 verifier 与运维演练。

#### 阶段 C：Eval 与发布门禁（P1）

- Eval dataset、Scorer、对比报告和回归阈值。
- Agent/Scenario/Capability 发布前门禁和失败保留旧版本。
- 控制台展示动作、审批、验证、上下文清单、Graph Patch 和 Eval 差异。

完成定义：任一生产 revision 都能追溯其测试集、分数、审批者、插件 digest 和 rollout 结果；未通过门禁
不能激活。

当前进度：版本化 Eval dataset/case、确定性 scorer、人工 observation/finalize、精确 Agent/Scenario/
Capability release gate 及控制台工作台已经完成。成果层也已完成 Artifact → Work 的不可变版本、发布版本
指针、private/unlisted/public、数据分级、协作者、固定版本分享、过期/撤销和访问审计。后续主要是增加
领域回归集、rubric judge 的版本治理、基线对比报告和大规模线上反馈候选池。

Smart Study 已决定保持独立，不再作为 JoyhouseBot 的默认插件、参考实现或迁移目标。该决定不改变
JoyhouseBot 的通用 Capability、Durable Action 与 Artifact/Work 契约；后续业务接入必须独立评估，不能
把 Smart Study 的数据模型、页面或流程复制进 Core。

#### 阶段 A 实施进度（2026-08-08）

阶段 A 的主要执行完整性纵切已经进入代码：

- 已新增 `runtime_turns`、`action_intents`、`action_observations` 及 PostgreSQL migration/store contract。
- Turn 和 Action 使用确定性身份；Capability 收到稳定 `idempotency_key`，已观察结果在恢复时直接复用。
- 模型响应在执行动作前持久化；Worker 重新进入同一 Run 时从该响应重建后续消息，不重复请求已冻结动作。
- 调用处于 `invoking` 且数据库与 Capability invocation 都没有结果时，Run 进入 `waiting_external`，不盲目重放。
- 连续两轮提出完全相同的工具动作时，在第二次调用前产生 `loop.stalled` 并失败，防止重复副作用与空转。
- 达到 `max_turns` 且没有最终结果时产生 `loop.exhausted`，Run 以 `loop_exhausted` 失败，不再提交空完成结果。
- 已覆盖稳定 action identity、动作完成后崩溃恢复、调用中断结果未知、waiting_external、loop stalled 和
  loop exhausted 回归。
- 已新增 `approval_requests` 及独立 PostgreSQL 状态机；审批冻结 Action、CapabilityRef、input hash、
  脱敏预览、风险、数据分级、要求角色和有效期，参数变化不能复用原审批。
- 模型选择的 side-effect Capability 会先停在 `waiting_approval`；approve 后恢复同一个 Turn/Action，
  reject、request_changes、expire 和 revoke-before-invoke 均进入明确终态。
- 审批消费和 Action 执行权在同一数据库事务中抢占；两个 Worker 只能有一个取得执行权，撤销与执行
  抢占也由同一行锁排序，不会在撤销成功后继续调用。
- 用户审批 API 已按 Run owner 隔离；高风险 Capability 可要求 operator，confidential/restricted 输入只
  返回字段清单。官方 Filesystem、Shell、Runtime Control 和 Context Assets 扩展已发布带版本的副作用声明，
  缺少可信副作用元数据的 MCP Tool 默认进入审批。
- 已覆盖暂停前不执行、批准恢复同一 Action、双 Worker 单消费者、参数不可变、跨用户不可见、operator
  角色、批准后执行前撤销和审批过期回归。
- 已新增 `operation_reconciliations` PostgreSQL 状态机。Capability 返回 `accepted + operation` 后，Run
  进入 `waiting_external`；到期后 Worker 只调用可选的 `reconcile_operation` 查询既有操作，不重新调用
  `execute`。对账使用独立租约、fencing version、退避、deadline 和最大次数，双 Worker 只有一个查询者。
- Capability 的 operation descriptor 会补齐稳定 `action_id/invocation_id/idempotency_key`。即使 Worker
  在外部提交后、写 observation 前崩溃，恢复也可按幂等键查询；明确终态会原子更新 reconciliation、
  Capability invocation、Action observation 和 Action 状态。
- 不提供查询能力、查询返回 unknown 或超过自动对账上限的操作进入 `manual_required`，不会自动重放。
  用户 API 按 Run owner 隔离，高风险记录可要求 operator；人工确认或 retry 继续同一冻结 Action。
- 内置 `spawn` 已改为可对账异步 operation：子 Run 身份由父 Action 幂等键确定，父 Run 等待子 Run
  明确终态后继续，不再把“已入队”当作“已完成”。
- 已覆盖自动恢复、外部调用崩溃窗口、双 Worker 对账 claim、人工确认跨用户隔离、人工 retry 不重建
  Action，以及完整 Runtime 自动唤醒回归。
- 已新增 `verification_records` PostgreSQL 状态机。每条记录冻结 verifier ID/type/version、policy、输入
  hash、required/repairable、证据和结论；Agent Run 由 Run lease fencing，Graph Task 由 Task lease
  fencing，旧 Worker 不能写入或覆盖新 Worker 的验收结论。
- Agent Run 支持 `schema`、`artifact` 和 allowlist `deterministic` verifier。Artifact 可按名称、媒体类型、
  最小数量和内容 hash 验收；公开 API 只返回 hash、计数和错误摘要，不返回 policy 正文、Worker 或租约。
- Native Agent 的最终回答在 Turn 完成前验证；required verifier 失败时，同一 Run 追加新 Turn 修复，
  并同时受 `max_repairs`、`max_turns`、Token、费用和 deadline 约束。达到上限后明确失败，不创建最终产物，
  不提交 completed。
- 已实现 `GET /v1/runs/{run_id}/verifications` 及 owner 隔离；`POST /v1/runs` 可提交
  `output_schema`、`verification_policy` 和 `max_repairs`，Agent revision 的 `output_policy` 也会进入执行快照。
- 已覆盖 Schema 修复成功、修复耗尽、Artifact/Deterministic 证据、终态提交前恢复、Run lease 双 Worker
  fencing 和跨用户查询隔离。
- Graph 的 Agent Task 与直接 Capability Task 已接入同一 Durable Turn/Action ledger。直接 Capability
  使用 `run_id + task_id + attempt + pinned CapabilityRef + frozen input` 产生稳定身份；审批或外部对账
  只暂停 Task，不增加业务 attempt，恢复时复用同一个 Action，成功副作用不会因结果验证失败被重放。
- `runtime_tasks` 已增加 `waiting_approval/waiting_external` 非终态。Task lease fencing 与 Run 等待摘要在
  同一事务中更新；同一 Graph 出现多个等待 Action 时，解除一个等待不会错误恢复或终结其余 Task。
- Graph 外部 operation 到期后由任一 Worker claim 同一等待 Task，只调用 `reconcile_operation`；人工确认、
  retry 和自动确认都会回到冻结 Action。Graph Agent Task 支持 schema/artifact/deterministic verification
  与 bounded repair，直接 Capability Task 也写入 Task-fenced verification evidence。
- `POST /v1/runs/graphs` 的每个 Task 可冻结 `output_schema`、`verification_policy` 和 `max_repairs`。
  已覆盖 Graph 审批暂停、双 Worker 批准后单执行、外部 operation 不重复提交，以及 Task lease 下验证修复。
- 已新增 append-only `loop_decisions` ledger，记录 continue、replan 和 escalate 的结构化原因、输入/输出
  hash、attempt、上限与 Run lease fencing，不保存 Prompt、隐藏推理或未脱敏模型正文。
- 主协调器的每次规划尝试使用独立 durable Turn scope。结构化计划失败时先持久化 replan decision，再发起
  替换计划；Worker 在 decision 后崩溃或租约转移时从下一 attempt 恢复，不重复失败尝试。
- `POST /v1/runs` 可提交 `max_replans`（0–10），未提交时读取冻结 Agent revision 的
  `planning_policy.max_replans`，再回退到 2。达到上限产生 `loop.exhausted`，Run 以
  `max_replans_exhausted` 失败；`GET /v1/runs/{run_id}/decisions` 按 owner 隔离并隐藏内部 details。
- 动态澄清后的新用户输入形成新的 planning scope，不误复用澄清前计划；规划 Turn 与后续根执行 Turn
  也使用不同 scope，恢复时不会把协调器 JSON 当成最终业务响应。

阶段 A 的代码纵切已经完成：`max_turns`、`max_repairs`、`max_replans`、审批、验证、外部对账和 Graph
Task 传播均进入 PostgreSQL 状态机。生产开放写业务数据前仍须完成组织策略批准、正式身份和发布门禁。

## 3. 当前发布状态机

```text
save draft
   │
   ▼
publish request ── transaction ──▶ immutable staged revision
                                      │
                                      ▼
                              snapshot healthy Agent Workers
                                      │
                     ┌────────────────┴────────────────┐
                     ▼                                 ▼
                loaded ACK                         failed ACK
                     │                                 │
          all targets loaded                retry failed targets / cancel
                     │                                 │
                     ▼                                 ▼
       automatic activate or await approval       retain old current
                     │
                     ▼
          activate revision ── explicit rollback ──▶ previous revision
```

目标 Worker 集合在发布事务中冻结，新扩容 Worker 不阻塞既有 rollout；它首次执行 Run 时仍会按
snapshot revision 懒加载。Worker 重启后 worker_id 改变，旧目标若在发布期间消失，rollout 会在
deadline 到达后进入 `timed_out`；管理员可以取消、只重试失败/超时目标，或发起替代发布。人工模式在
全部 ACK 后进入 `awaiting_approval`，批准才切换 current pointer。

## 4. 当前控制面 API

- 运行：`/v1/admin/overview`、`runs/*`、diagnostics、reasoning、blobs、replays。
- 集群：`/v1/admin/workers`、`rollouts`。
- Agent：`/v1/admin/agents`、`agents/{id}/revisions/*`、Skill binding。
- Capability：`/v1/admin/capabilities/*`。
- Scenario：`/v1/admin/scenarios/*`。
- 用户 Workflow：`/v1/workflows/*`，包括设计 Run、不可变 revision、发布和编译到 TaskGraph 执行。
- 访问：`/v1/admin/users`、`access-tokens`、`permissions`。
- 审计：`/v1/admin/configuration-events`、`access-events`。
- 安全摘要：`/v1/admin/config`，永不返回凭据正文。

## 5. 上线前剩余工作

以下不是当前实现的隐含能力，完成后才可按相应规模上线。

### P0：执行完整性

- 将 side-effect 策略从当前自动/owner/operator 扩展到业务策略批准与显式禁止，并为组织角色和控制台
  增加管理入口。

### P0：身份和边界

- 接入正式 OIDC/OAuth2 issuer，使用短期 access token；数据库 token 保留给服务账号和应急联调。
- 将 control surface 放在独立私网入口，并为原始推理/Trace Blob 增加二次认证和导出审批。
- 为 API token 增加作用域、到期策略、轮换提醒和签发事件 UI。

### P0：发布控制

- 发布前进一步执行真实 provider 连通性、模型账号权限和外部 Connector 健康检查；当前已检查
  Agent 模型/插件可解析、Capability JSON Schema 与精确插件构建、Scenario 精确能力依赖。
- 增加跨 Agent/Capability/Scenario 的 change-set，把必须一起生效的多个 revision 作为原子发布单元。

### P1：密钥和业务配置

- 接入 Vault/KMS/云 Secret Manager；数据库只保存 `credential_ref`。
- 将 Provider endpoint、Channel 定义、配额和平台策略做成数据库 revision；启动配置只保留连接
  PG 和 Secret Manager 所需的最小参数。
- 增加配置 change-set，把多个 Agent/Scenario/Capability revision 作为一个原子发布单元。

### P1：可观测和容量

- 输出 OpenTelemetry trace/metric/log，接入 Prometheus/Grafana 和告警。
- 增加 queue lag、lease contention、provider p95/TTFT、cache hit、token/cost、rollout duration SLO。
- 用真实用户公平性模型压测多进程 API、Agent Worker、Graph Worker 和 SSE 长连接。

### P1：数据治理

- Trace/Reasoning/Memory/Artifact 分级保留、对象存储迁移、加密备份与删除证明。
- 对敏感 Blob 做字段级加密和访问审计查询；回放前显示外部副作用与预计成本。

## 6. 验收门槛

- Python：全量测试、ruff、架构边界测试全部通过。
- PostgreSQL：真实 PG migration、双 Store claim/fencing、staged rollout、token 哈希认证通过。
- 前端：`vue-tsc --noEmit` 与生产 build 通过，控制台不显示任何凭据 hash/正文。
- 故障演练：Worker kill/restart、PG 短断、provider timeout、rollout failure、SSE reconnect、重复
  idempotency key、调用成功未落终态和审批后执行前重启均有确定结果。
- 安全：关闭 insecure auth；public surface 不含 admin route；最后管理员、Token 吊销和敏感读取
  权限有自动化测试。
- 执行：无验证通过不得 completed；非幂等副作用最多一次；未知外部结果进入对账；loop 耗尽明确失败。
- 上下文：frozen replay 可重建 ContextManifest；候选记忆未经策略/审批不得写入长期记忆。
