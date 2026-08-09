# 业务插件发布与部署

Joyhousebot 的核心只提供 Agent 云运行时和治理面；业务能力以独立 Python 包发布。Dinq Discover 是第一
个采用该边界的业务插件。

Smart Study 是完整业务闭环插件的参考实现，源码位于相邻业务仓库
`smart-study/packages/joyhousebot-smart-study`。它证明业务数据库无需进入核心：插件只声明版本化
Connector，通过固定、受白名单保护的 HTTP origin 调用 Smart Study API，并沿用 Runtime 传入的
`action_id/idempotency_key`。

## 不可变发布单元

每次插件发布必须具有新的语义版本或新的发布版本，并提供：

- `plugin_id`、`version`、wheel/image 的 `build_digest`；
- 每个 Tool、Connector、Skill 的完整 `CapabilityRef`；
- 面向业务人员的 `quickstarts`：真实用户提示、关联的 Scenario / Capability、必需连接和预期结果；
- 连接依赖、数据分级、最小权限和成本策略；
- 健康检查，只检查配置与可执行节点，不能在常规 health read 中发起搜索或泄露凭据。

Manifest 必须声明 `runtime_api_version=v1` 和执行隔离策略；生产发布还应提供制品 URI、签名/签名键引用
与 SBOM。Runtime 会冻结 Manifest SHA-256 以及 Agent、Channel、Connector、Event Trigger、Knowledge
Provider、MCP Server、Projection、Scenario、Skill、Tool、Workflow 的完整组件目录。组件版本不可原地
改写。

发现/安装不是生效。标准状态为：

```text
discovered → staged → Worker exact version/build preheat ACK → active
                                      └─ failure: previous active remains
```

控制台插件详情可提交 discovered 版本；平台治理页面负责查看 rollout、人工批准、重试与安全回滚。同一
插件只有一个 active 版本。回滚也必须经过新的 Worker ACK rollout，不能直接修改数据库状态。

`DINQ_PLUGIN_BUILD_DIGEST` 是 Dinq 部署时传入的实际制品摘要。未设置时仅使用醒目的开发默认值，不能
作为生产发布证明。上线流程应先构建 wheel/image、计算其 SHA-256、再以同一 digest 发布插件和启动
Worker。

## Dinq 接入顺序

1. 在每个 Agent Worker 安装相同版本的 `dinq-plugin`，并在 Joyhousebot 配置的
   `tools.capability_plugins` 中加入 `dinq_plugin.discover.plugin`（或采用 entry point discovery）。
2. 以相同的 `DINQ_PLUGIN_BUILD_DIGEST` 启动 Worker；Worker heartbeat 会报告加载的发布单元。
   本地检索能力使用 `DINQ_LOCAL_SEARCH_URL`（默认 `http://127.0.0.1:8200/search`）和
   `DINQ_LOCAL_SEARCH_ALLOWED_HOSTS`。后者是逗号分隔的服务 DNS/IP 白名单；默认只允许 loopback，
   Tool 输入永远不能改变目标地址。
3. 执行 `python -m dinq_plugin.discover.seed`。它只发现并登记 Dinq Capability、Skill 和新版 Scenario；
   再从插件控制面提交发布并等待 Worker ACK。所有 Scenario 任务持久化完整 CapabilityRef，绝不按名称
   选择最新 Tool。
4. 对 Dinq 服务部署，再显式执行 `python -m dinq_plugin.discover.bootstrap`。它在上述已发布 Catalog
   基础上创建当前版本的 `main-coordinator` Revision，固定 `dinq.discover@<version> + build_digest` 并只授予 `dinq.*`。
   该命令是部署决策，普通 Joyhousebot 安装或仅安装插件时绝不会自动提升业务权限。
5. 等目标 Worker 均为 `execution_eligible` 后，才将该 revision 设为默认或把流量路由给它。

示例 Capability policy：

```json
{
  "mode": "catalog",
  "permissions": ["dinq.search.read", "dinq.web.read"]
}
```

业务 Agent 的默认选择是部署决策，不由插件种子过程擅自改变。插件的 `quickstarts` 会在控制台的插件
页面中通用渲染；点击后只是把业务提示、目标 Agent 和 Scenario 线索带入在线试用，仍经由正常的
Coordinator 路由、追问、权限校验和 Run 审计执行。这样通用 Joyhousebot 安装不会因某个业务插件而被
耦合，Dinq 环境也能明确审计“谁启用了哪些业务权限”。

Dinq 人才搜索、业务 Run 工作台和投影 API 客户端由 `dinq-plugin/apps/console-extension/` 保存和维护。
Joyhousebot 核心 Console 不注册 Dinq 导航或路由。插件专属 UI 只有在通用扩展契约定义清楚后才能由
插件声明并挂载；在此之前，标准入口是插件中心的 Quickstart、Agent 试用和运行中心。

## 插件 Schema Migration

插件拥有独立业务表时，DDL 必须与核心 migration 共用同一把 cluster-wide advisory lock，
防止插件 DDL 与核心 DDL 在并发启动时交叉持锁：

- 持有 RuntimeStore 的插件：在 `store.schema_migration_lock()` context manager 内执行 DDL，
  然后调用 `store.record_plugin_migration(name="plugin:<plugin_id>", version=N, ddl=...)`
  把 `(name, version, checksum, applied_at)` 写入统一的 `schema_migration_history` 表；
- 自建数据库连接的插件（如 Dinq）：直接对 `joyhousebot.storage.postgres_locks` 的
  `SCHEMA_MIGRATION_LOCK_ID` 执行 `pg_advisory_xact_lock`。事务级 advisory lock 与核心
  `schema_migration_lock()` 的 session 级锁使用同一 lock ID，二者互相排斥。

Dinq 的 `dinq` schema 由 `dinq_plugin.discover.postgres_store.DinqPostgresStore.migrate()` 按
第二种方式接入。DDL 变更后 `schema_migration_history` 中 checksum 不一致会产生 warning 日志，
用于发现 schema 漂移。

## Smart Study 接入顺序

1. 构建并安装 `joyhousebot-smart-study` wheel，以实际 SHA-256 设置
   `SMARTSTUDY_PLUGIN_BUILD_DIGEST`。
2. 设置固定 `SMARTSTUDY_API_URL`、`SMARTSTUDY_API_ALLOWED_HOSTS` 和
   `SMARTSTUDY_BRIDGE_TOKEN`；Capability 输入不能覆盖它们。
3. 开启 entry point discovery，或在 `tools.capability_plugins` 显式加入
   `smartstudy_joyhousebot.plugin`。
4. 在 Smart Study API 设置对应 `JOYHOUSEBOT_BRIDGE_TOKEN` 并升级迁移；内部 API 为空时 fail closed。
5. 运行插件 `configuration` 与 `api` 诊断；确认所有 Worker heartbeat 的插件版本和 digest 一致。
6. 发布只含所需 `smartstudy.*` 权限的新 Agent revision。读取可自动执行，写行动、打卡和复盘按业务
   场景要求用户确认。

Smart Study 的身份契约是 JoyhouseBot `user_id` 等于十进制 Smart Study `User.id`。写请求携带 Durable
Action 的稳定 operation ID，Smart Study 的 `joyhousebot_operations` 再冻结参数 hash 和结果；重复请求
回放原结果，同键换参数拒绝。复盘和个人成果作为 confidential Artifact 返回，公开分享只能走
JoyhouseBot Work 的版本、分级、发布、撤销与审计链。
