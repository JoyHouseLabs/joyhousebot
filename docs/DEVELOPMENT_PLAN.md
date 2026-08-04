# Joyhousebot 生产化研发基线与后续计划

本文记录 2026-08-04 代码的真实状态，是当前唯一有效的研发计划。总体边界以
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
- Scenario 支持字段 Schema、追问节点、条件边、能力白名单、执行策略和 Studio 模拟。
- Tool、Skill、Agent、Workflow、Connector 使用统一 CapabilityDefinition/Invocation/Result。
- Agent、Capability、Scenario 是平台共享版本化资源，不为每个用户复制。

### 1.3 Agent 配置发布

- `agent_definitions` 保存稳定身份，`agent_revisions` 保存不可变策略版本。
- Skill 只能绑定 draft Agent revision；发布后正文和绑定都不可修改。
- 发布创建 `configuration_rollouts` 和固定的 `configuration_rollout_targets`。
- Worker 的 AgentRuntimeCatalog 主动预热精确 revision，并记录 loaded/failed ACK。
- 全部目标成功后才原子切换 current revision；失败时旧版本继续服务。
- Run 创建时固化 Agent revision 与 Skill 绑定，回放不会被后续发布漂移。

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
- 平台页按访问控制、集群发布、审计、运行摘要分区；Agent、Skills、Tools、MCP Server 和 Channels 在配置子菜单中分别维护，Dinq 运维保持独立入口。
- 管理员可用角色模板选择权限、签发/吊销 Token、创建 Agent draft、发布 revision、发布
  Capability，并观察每个 Worker 的 ACK/失败。
- Chat 是真实用户链路试用，不绕过身份、Session、Run 或 Worker。

## 2. 当前发布状态机

```text
save draft
   │
   ▼
publish request ── transaction ──▶ immutable published revision
                                      │
                                      ▼
                              snapshot healthy Agent Workers
                                      │
                     ┌────────────────┴────────────────┐
                     ▼                                 ▼
                loaded ACK                         failed ACK
                     │                                 │
          all targets loaded                         retain old current
                     │                                 │
                     ▼                                 ▼
              activate revision                  rollout.failed event
```

目标 Worker 集合在发布事务中冻结，新扩容 Worker 不阻塞既有 rollout；它首次执行 Run 时仍会按
snapshot revision 懒加载。Worker 重启后 worker_id 改变，旧目标若在发布期间消失会使 rollout
保持进行中，管理员可以据此判断需要恢复实例或执行后续的显式终止/替代发布。

## 3. 当前控制面 API

- 运行：`/v1/admin/overview`、`runs/*`、diagnostics、reasoning、blobs、replays。
- 集群：`/v1/admin/workers`、`rollouts`。
- Agent：`/v1/admin/agents`、`agents/{id}/revisions/*`、Skill binding。
- Capability：`/v1/admin/capabilities/*`。
- Scenario：`/v1/admin/scenarios/*`。
- 访问：`/v1/admin/users`、`access-tokens`、`permissions`。
- 审计：`/v1/admin/configuration-events`、`access-events`。
- 安全摘要：`/v1/admin/config`，永不返回凭据正文。

## 4. 上线前剩余工作

以下不是当前实现的隐含能力，完成后才可按相应规模上线。

### P0：身份和边界

- 接入正式 OIDC/OAuth2 issuer，使用短期 access token；数据库 token 保留给服务账号和应急联调。
- 将 control surface 放在独立私网入口，并为原始推理/Trace Blob 增加二次认证和导出审批。
- 为 API token 增加作用域、到期策略、轮换提醒和签发事件 UI。

### P0：发布控制

- 增加 rollout 超时、显式取消、重试失败 Worker、批准后激活和自动回滚策略。
- Capability/Scenario 采用与 Agent 相同的目标校验与 rollout（当前已有不可变发布和审计，只有
  Agent 需要 Worker 预热 ACK）。
- 发布前执行 provider 连通性、模型权限、Tool 依赖和 Schema compatibility 检查。

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

## 5. 验收门槛

- Python：全量测试、ruff、架构边界测试全部通过。
- PostgreSQL：真实 PG migration、双 Store claim/fencing、staged rollout、token 哈希认证通过。
- 前端：`vue-tsc --noEmit` 与生产 build 通过，控制台不显示任何凭据 hash/正文。
- 故障演练：Worker kill/restart、PG 短断、provider timeout、rollout failure、SSE reconnect、重复
  idempotency key 均有确定结果。
- 安全：关闭 insecure auth；public surface 不含 admin route；最后管理员、Token 吊销和敏感读取
  权限有自动化测试。
