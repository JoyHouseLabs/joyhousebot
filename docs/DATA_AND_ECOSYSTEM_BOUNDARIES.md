# 单数据库与生态扩展边界

## 1. 当前决定

HappyHouse 采用一个 PostgreSQL database 作为第一阶段的集成部署方式，不为尚未出现的规模和组织边界承担多数据库运维成本。

HappyHouse Product、Porthouse Runtime、HappyHouse Market 和官方 App 统一连接一个 PostgreSQL
database，使用同一个环境变量：

```bash
export HAPPYHOUSE_DATABASE_URL='postgresql://user:password@127.0.0.1:5432/happyhouse'
```

本阶段允许共用同一个数据库账号。统一部署使用 `HAPPYHOUSE_DATABASE_URL`；Porthouse 读取它作为共享连接，
也可在独立 Runtime 部署中使用 `PORTHOUSE_DATABASE_URL`。Product、Cloud/Market 与 App 不应读取 Runtime
专用变量。

```text
PostgreSQL database: <shared database>
├── Runtime tables              owner: Porthouse migrations/repositories
├── product_*                   owner: HappyHouse Product
├── cloud_* / market_*          owner: HappyHouse Market
└── app_<app_id>_*              owner: corresponding App
```

单数据库解决的是部署、备份、连接和本地开发复杂度，不取消模块边界。各模块仍拥有自己的表、迁移和业务状态机，
不能因为物理共库就直接修改其他模块的表。

## 2. 当前阶段与未来阶段

当前 Alpha/MVP：

- 一个 PostgreSQL database；
- 一个连接字符串；
- 可以使用一个数据库 role；
- 一次整体备份和恢复；
- 各服务按顺序执行自己的 migration；
- 优先跑通记录、机会、目标、计划、联系、执行、成果和复盘。

未来只有出现以下真实信号后才评估拆库：

- Market 与本地 Product 必须独立扩缩容或独立发布；
- 第三方 App 需要独立资源、合规、地域或数据保留策略；
- 单库连接数、写入量、备份窗口或故障域已经成为瓶颈；
- 不同团队需要数据库级权限隔离；
- 商业账本或高敏感数据需要独立安全边界。

拆库是后续部署优化，不改变产品对象和服务契约。当前不提前建设分布式事务、CDC、FDW 或多库运维平台。

## 3. 表与迁移所有权

共享数据库中必须继续保持清晰的迁移所有权：

| 模块 | 表范围 | 迁移记录 | 禁止操作 |
| --- | --- | --- | --- |
| Porthouse Runtime | Runtime 现有执行、配置、审计和成果表 | `runtime_schema_migrations` 等 Runtime 自有记录 | Product/App migration 修改 Run、Task、Schedule、Approval |
| HappyHouse Product | `product_*` | `product_schema_migrations` | 复制 Runtime 状态机或修改 Market 商业账本 |
| HappyHouse Market | `cloud_*`、`market_*` | `market_schema_migrations` | 读取私人 Product/Runtime 正文或修改 App 业务状态 |
| 官方 App | `app_<stable_app_id>_*` | `app_<stable_app_id>_schema_migrations` | 使用无前缀通用表名、修改其他 App 或 Core 表 |

规则：

1. 每个仓库/模块只维护自己的 migration chain；
2. migration 只能创建、修改或删除本模块拥有的表、索引、约束和函数；
3. 模块之间不建立数据库外键、触发器、共享视图或级联删除；
4. 跨模块关系保存稳定 ID，例如 `run_id`、`schedule_id`、`work_id`、`app_id`；
5. Runtime 已有的历史无前缀表继续由 Runtime 独占，不要求为了视觉统一进行高风险重命名；
6. 新官方 App 的 `app_id` 必须先稳定下来，再生成安全的表前缀；
7. 第三方包不能在 Runtime/Market 安装阶段执行任意 SQL。需要业务表的 App 由自己的受信服务执行迁移；
8. 测试清库脚本只能连接显式 `_test` 数据库，绝不能连接共享开发或生产库。

## 4. 启动与迁移顺序

本地和单机部署使用同一个 `HAPPYHOUSE_DATABASE_URL`，按以下顺序启动：

```text
1. 创建共享 PostgreSQL database
2. Porthouse 执行 Runtime migration
3. HappyHouse Product 执行 product_* migration
4. HappyHouse Market 执行 cloud_*/market_* migration
5. 已启用的官方 App 各自执行 app_<id>_* migration
6. 启动 API、Worker、Scheduler、Product、App 和前端
```

不要并发执行首次 DDL。长运行服务启动后应关闭自动迁移，版本发布时由一次性 migration 命令按顺序执行。
一个模块迁移失败时停止后续启动，不回滚或清理已经存在的其他模块表。

## 5. 数据所有权不因共库改变

| 系统 | 拥有的数据 | 明确禁止拥有的数据 |
| --- | --- | --- |
| Porthouse Runtime | Run、Task、Event、Trace、Schedule、Approval、Agent/Team/Workflow/Skill/Capability、Artifact/Work、审计和执行成本 | HappyHouse 目标、联系人、个人问题、Market 订单、App 私有领域状态 |
| HappyHouse Product | 用户、设备、Source、Contact、Opportunity、Challenge、Goal、Plan、Action、Metric、Review、Handoff 和 Runtime 引用 | Runtime lease/retry/终态、Market 支付账本、App 专用生产状态 |
| HappyHouse Market | Catalog、Offer、订单、支付事件、Entitlement、治理和结算 | Prompt、私人资料、Run/Memory/Artifact 正文、App 业务正文 |
| 独立 App | 自己的领域规则、专用页面、生产资产、外部回执和领域业务状态 | Runtime 执行状态机、Product 通用个人经营事实、Market 支付账本 |

模块只能通过自己的 Repository 写自己的表。跨模块协作仍使用 application service、版本化 HTTP/SSE、
Callback、Handoff 或稳定引用。禁止为图省事在 Product 查询中 JOIN Runtime 内部表并据此重新判断 Run 终态。

## 6. App 的共库规则

第一阶段官方 App 默认使用 `HAPPYHOUSE_DATABASE_URL`，不再要求每个 App 创建数据库和账号。

App 仍需要：

- 独立、稳定的 `app_id` 和表前缀；
- 自己的 migration chain 和 Repository；
- 明确输入输出 Schema；
- 接收 Runtime 冻结的 `action_id/idempotency_key`；
- 异步外部动作保存 operation ID、状态、取消和对账信息；
- 通过 Handoff 读取本次选择的 Product 上下文；
- 结果形成 Artifact/Work 或 Product ResultRef；
- 卸载后保留历史结果可读，不盲目删除业务表。

App 如果只是 UI + Skill/Workflow、没有专用业务状态，就不创建任何表。只有真实业务闭环需要时才增加
`app_<id>_*` 表。

远程第三方 App 可以继续使用自己的数据库。统一数据库是当前官方一体化部署的默认值，不要求外部 SaaS 把数据
迁入 HappyHouse。

## 7. Skill 与 Extension

Skill 是版本化方法资产，不拥有业务表。执行产生的状态进入 Runtime Run/Task/Artifact/Work 或由 App
Capability 写入 App 自有表。

Extension 是 Provider、Channel、Connector 或原子 Capability 技术制品，默认无业务状态。它不能：

- 在 Core 启动时执行自定义 DDL；
- 绕过 Dispatcher 直接写 Run/Task/Approval/Audit；
- 创建第二套 Invocation、重试、审批或调度状态机；
- 借共库直接读取 Product、Market 或 App 私有表。

确实需要业务持久化的 Extension 应升级为 App/Connector 服务，并遵守 `app_<id>_*` 表前缀与迁移规则。

## 8. 跨模块一致性

物理共库不意味着所有服务共享一个跨模块事务。当前仍使用简单、可恢复的协作方式：

- 写请求携带稳定 idempotency key；
- 权威模块在自己的事务中写业务事实和 Outbox；
- Callback/Event 带 `event_id`、对象版本、时间和摘要；
- 消费方按 `event_id` 去重并保存投影；
- 丢失通知可以按稳定 ID 查询和 reconciliation；
- Product 保存 Runtime 状态摘要时标记最后同步时间，不把投影当权威事实。

第一阶段不建设 Kafka、CDC 或分布式事务。对于同一 Product 模块内的 Goal、Plan、Action 等事实，可以正常使用
一个本地事务完成业务写入。

## 9. 备份、恢复和测试

当前阶段数据库作为一个整体备份和恢复：

- 一份物理/逻辑备份覆盖 Runtime、Product、Market 和官方 App 表；
- 恢复后逐模块运行 schema/version 检查；
- 不支持只回滚某个模块的数据库快照；应用回滚必须兼容当前 Schema；
- 删除、清理和数据修复命令必须限定表前缀和对象 ID；
- 测试继续使用专用数据库，例如 `happyhouse_test`、`porthouse_test`，并验证名称以 `_test` 结尾；
- 自动化测试不得使用 `HAPPYHOUSE_DATABASE_URL` 或 `PORTHOUSE_DATABASE_URL` 指向的开发/生产数据库执行 TRUNCATE。

## 10. 未来拆库准备

当前只保留低成本、不会阻碍业务开发的拆库准备：

- 表名前缀和独立 migration chain；
- 无跨模块外键、触发器和直接 JOIN；
- 稳定对象 ID；
- API/Handoff/Callback 契约；
- Repository 不接受其他模块的表写权限；
- 每个模块可以列出自己拥有的表。

未来拆分时，将某一前缀表复制到新数据库、切换该模块的连接、对账后停止旧表写入即可。当前不为这个未来过程
实现双写和数据同步。

## 11. 新增模块验收清单

新增 Product 模块或 App 时必须回答：

1. 它是否真的需要持久化业务状态？
2. 表前缀和 migration chain 由谁拥有？
3. 是否错误地复制了 Runtime、Product 或 Market 已有状态机？
4. 输入输出、幂等、重试、取消和对账如何表达？
5. 是否存在跨模块外键、触发器、直接 JOIN 或越权 Repository？
6. 如何形成 Artifact/Work、ResultRef 和执行证据？
7. 禁用后历史数据是否仍可安全读取？
8. 测试是否使用专用 `_test` 数据库？
9. 没有该模块时 Core 是否仍能启动？
10. 未来拆库时是否只需迁移该前缀表并切换连接，而不必重写业务链路？
