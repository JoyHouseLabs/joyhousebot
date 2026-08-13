# 独立 App 与 JoyhouseBot 协作契约

状态：Accepted（2026-08-10）
适用范围：独立业务 App、Joyhouse、JoyhouseBot Runtime、Console 与商业交付

## 1. 决策

App 是解决完整业务问题、可以独立部署和售卖的产品；JoyhouseBot 是 App 可选择使用的长期任务执行引擎。
两者不是父子系统，也不通过把 App 的 Python 代码加载进 Runtime 来集成。

```text
Business App                                      JoyhouseBot
UI / Users / Subscription / Domain DB             Run / Task / Schedule
Domain rules / Transactions / Product analytics   Agent / Workflow / Approval
            │                                          │
            ├── versioned HTTP + SSE ──────────────────▶│
            │◀── signed Remote Capability ──────────────┤
            │                                          │
        App-owned facts                         PostgreSQL runtime facts
```

App 可以拥有自己的品牌、用户系统、定价、会员、订单、许可证、数据库、领域逻辑和发布节奏。Runtime
只负责把需要长期、异步、可恢复和可审计的工作可靠执行，并返回 Run、事件、确认点、Artifact 和 Work。

官方 App 可与 Product、Runtime 和 Market 共用 `JOYHOUSE_DATABASE_URL`，业务表使用 `app_<id>_*` 前缀并
保留独立 migration chain。远程或独立售卖的 App 仍可使用自己的数据库。无论物理部署方式如何，App 都不能
直接读写 Runtime、Product 或 Market 表。

## 2. 统一概念

| 概念 | 定义 | 不能替代 |
| --- | --- | --- |
| App | 面向用户的完整业务产品，可独立售卖 | Extension、Agent |
| Task Pack | 可安装的持续任务产品，冻结目标、节奏、确认点、成本与输出 | App 的用户系统和业务数据库 |
| Skill | “如何完成工作”的版本化方法包，包含说明、模板、Schema、示例、依赖和 Eval | Tool、任意脚本执行 |
| Workflow | 步骤、分支、等待与状态流转结构 | Runtime 的 Run/Task 状态机 |
| Agent | 承担执行角色并按策略选择 Skill 与 Capability | 完整业务产品 |
| Capability | Runtime 可治理和调用的原子动作 | App 业务闭环 |
| Integration | 模型、渠道或既有业务系统的连接配置 | Extension 代码制品 |
| Extension | 扩展 Runtime 的技术安装单元，可提供 Provider、Channel、Connector 或 Capability | App |

“社区 Skill”导入时按真实语义分类：方法、Prompt 与模板保留为 Skill；代码/API 调用拆成 Capability；外部
账号拆成 Integration；定时、审批和恢复拆成 Task Pack/Workflow；完整业务闭环成为 App。

Skill 默认是声明式、低权限资产。它可以声明需要哪些 Capability 和 Integration，但不能绕过 Dispatcher、
审批、配额、幂等和审计。Skill 不全局注入所有 Agent，只由精确 Agent、Workflow、Task Pack 或 App 版本
按需加载，加载记录进入 Run/Event/Trace。

## 3. App 必须保留的职责

- 登录、注册、账号关系、组织或会员模型；
- 订阅、订单、许可证、退款、税务和售后；
- 业务页面、领域规则、强一致业务事务；
- 完整业务数据库、数据保留策略和隐私承诺；
- 产品埋点、商业分析、定价、品牌和发布节奏；
- App 用户与 Runtime 主体之间的稳定映射。

JoyhouseBot 不复制这些表和逻辑，也不将 App 的业务数据库变为 Runtime 的内部 repository。

## 4. Runtime 必须统一承担的职责

- Run / Task / Event / Trace / Schedule 持久状态与最终一致性；
- Agent、Workflow、多 Agent DAG、等待输入和人工审批；
- Worker Lease、fencing、故障接管、重试和恢复；
- Capability 准入、权限、配额、幂等、对账和审计；
- Artifact / Work、证据、版本、发布、分享和撤销；
- 长期运行的成本、质量、性能与 Eval 闭环。

App 只能保存 `run_id` 等外部引用和适合自身展示的投影，不能复制 Run/Task 状态机再自行判定最终状态。

## 5. App 调用 Runtime

App 后端使用版本化 HTTP 数据面提交执行：

1. `GET /v1/apps` 读取当前用户已启用的安装和声明式 Entry Point；
2. `POST /v1/apps/{installation_id}/runs` 选择 Entry Point，并携带稳定 `Idempotency-Key`；
   Runtime 将其解析到已冻结的 Agent、Team、Scenario 或 Workflow，再进入唯一 Run 提交入口；
3. 保存返回的 `run_id`，通过 SSE 或版本化查询跟踪状态；
4. 将等待输入、审批和结果映射成 App 自己的产品界面；
5. 只把业务需要的 Artifact/Work 投影回 App，保留 Runtime 证据引用。

版本冻结贯穿整条执行链：App 安装保存依赖锁；Entry Point 传递精确 Revision；Scenario 生成的 Graph、
Workflow 的协调 Agent、Agent 节点、Team/Scenario 子 Run 与 bounded-loop Agent 模板都保存 Revision；
Run execution snapshot 最终再次冻结实际加载版本。发布新的 Agent Revision 不会静默改变已安装 App 或已发布
Workflow；主动停用依赖则会让新执行失败关闭并要求升级，而不是回退到“当前版本”。

`X-User-ID` 只适用于本机开发。生产环境不得让 App 使用一个高权限服务 Token 配合任意用户头代理请求。
生产委托链路已经固定为：平台管理员为一个 `app_id` 创建 App Client；用户只对自己的一个安装授权；
App 后端用 `client_id + client_secret + grant_id` 在 `/v1/app-auth/token` 交换最长一小时的短期 Token。
授权 scope 同时受 Client allowlist 与安装权限上限约束，Token 绑定 `user_id + installation_id`，不能直接
提交任意 `/v1/runs`，也不能读取该用户的非 App Run。缩权、重新授权、撤销 Grant 或撤销 Client 都会
立即使既有委托 Token 失效，并保留审计事件。Client Secret 只在创建或轮换响应中返回一次；
`POST /v1/admin/apps/clients/{client_id}/rotate-secret` 会立即撤销该 Client 的全部存量 Token，但不会替用户
撤销 Grant。App 必须先把新 Secret 写入秘密管理系统，再重新交换 Token，不能并行长期保留两把 Secret。

App 传给 Runtime 的 `user_id` 应是稳定、无 PII 的主体标识。未关联 Joyhouse 账号时，建议使用 App
命名空间下的不透明映射，例如 `app:<app_id>:<opaque_subject>`；映射关系由 App 保存。用户主动关联
Joyhouse 账号时，必须通过明确授权流程合并主体，不能按邮箱自动合并。

## 6. Runtime 调用 App

当 Agent 需要读取或写入 App 的真实业务系统时，App 通过通用 Remote Capability 协议暴露窄接口：

1. 运维人员在 Console 创建远程连接 Revision，密钥只以 `env://VARIABLE` 引用；
2. App 发布输入/输出 Schema、权限、数据分级、副作用和对账契约；
3. Runtime 在审批后冻结 `action_id/idempotency_key` 并签名调用；
4. App 在自己的身份、授权和数据库事务中再次校验；
5. 同步写操作返回稳定 WriteReceipt；异步操作返回 operation 引用并支持查询；
6. Runtime 负责重试、最终状态对账、审计和 Artifact，不要求 App 实现第二套 Agent Runtime。

完整请求签名、重放保护、回执和异步对账见 [远程 Capability 协议](REMOTE_CAPABILITY_PROTOCOL.md)。

Run 完成通知与业务 Capability 是两个不同契约。App 可为自己的已启用安装登记公网 HTTPS 回调：

- 只允许 `env://VARIABLE` 引用签名密钥，明文不进入数据库；
- Run 的终态与 Callback Outbox 在同一 PostgreSQL 事务内提交；
- Scheduler Worker 使用 `event_id` 作为幂等键，发送时间戳与 HMAC-SHA256 签名，禁止重定向并执行
  SSRF/DNS pinning；
- 非 2xx 响应进入带 lease version fencing 的指数退避，达到上限进入 `dead`；
- Payload 只含 App 身份、`run_id`、终态、结果地址和错误码，不携带私有结果或 Artifact；App 必须用
  用户委托 Token 读取 `/v1/runs/{run_id}`；
- `GET /v1/runs/{run_id}/app-callbacks` 返回投递状态，Prometheus 暴露 pending/dead 和最老等待时间。
- Owner 修复接收端后可调用
  `POST /v1/runs/{run_id}/app-callbacks/{event_id}/replay`，且必须提供稳定 `Idempotency-Key`。重放会创建
  带 `replay_of_event_id` 和递增 `replay_sequence` 的新投递，原始 sent/dead 记录不可修改；相同请求键
  只产生一次新投递。

Python App 可以直接使用 `joyhousebot.app_sdk.AppRuntimeClient` 完成 Token 交换、安装查询、幂等启动和
终态等待；`verify_app_callback` 负责 canonical JSON、时间戳、签名和事件身份校验。单元测试使用
`AppRuntimeSimulator` 的 `httpx.MockTransport`，它不启动数据库，也不能代替真实 Runtime 集成测试：

```python
from joyhousebot.app_sdk import AppRuntimeClient, AppRuntimeSimulator

simulator = AppRuntimeSimulator()
async with AppRuntimeClient(
    "https://runtime.example.com",
    client_id=simulator.client_id,
    client_secret=simulator.client_secret,
    grant_id=simulator.grant_id,
    transport=simulator.transport(),
) as runtime:
    run = await runtime.launch(
        simulator.installation_id,
        "生成本周经营复盘",
        idempotency_key="weekly-review:2026-W33",
    )
    result = await runtime.wait_run(run["run_id"])
```

## 7. 商业交付模式

App 与 Runtime 解耦后可以采用三种模式：

1. **独立 SaaS**：App 自己获客、收费和托管，后端调用官方托管或自有 JoyhouseBot Runtime；
2. **Runtime 随产品交付**：App 套餐包含托管 Runtime，最终用户只感知 App 品牌和价值；
3. **Bring Your Own Runtime**：客户提供自己的 Runtime 地址和授权，App 只销售业务产品与持续任务。

App License、Runtime 资源消耗和第三方模型/搜索成本必须分别计量。App 可以把 Runtime 用量包含在套餐中，
但不能依赖绕过审计的共享密钥。Task Pack 与 Skill 可以在 App 内售卖或通过市场分发；购买它们不会自动
获得外部账号授权或高风险 Capability 权限。

Owner 可通过 `GET /v1/apps/{installation_id}/usage?since=<ISO8601>&until=<ISO8601>` 查询安装级 Run、终态、
Entry Point、模型调用、Token 和模型成本归因；默认窗口为最近 30 天、单次最长 366 天。该端点不向委托
App Token 开放，避免 App 自行扩大计费可见范围。Manifest 的 `metering.source_event` 会映射到受支持的
Runtime 事实计量项；许可证、订单、税务和最终账单仍由 App/Market 持有，Runtime 用量不能直接冒充账单。
Token 同时返回逻辑 `input/output_tokens` 和当前调用实际 `billed_input/output_tokens`；缓存命中只增加逻辑
工作量，不增加计费量。App/Market 如按模型资源结算，应使用 `model.billed_input_tokens`、
`model.billed_output_tokens` 与成本完整性状态，不应把逻辑工作量直接当作供应商账单。

跨实例市场采用独立的 Registry、作者签名和 Entitlement，不把支付、评价或结算塞入 Runtime。完整的
信任、安装、更新、撤销、计量和创作者治理见
[App Market 治理与跨实例分发协议](APP_MARKET_GOVERNANCE.md)。

## 8. 当前能力与后续门槛

当前已有：版本化 App Entry Point/Run HTTP/SSE、用户/服务 Token 与 scope、App Client/Grant 短期
委托与 Secret 轮换、安装级 Run 隔离、幂等 Run、终态签名 Callback Outbox 与人工不可变重放、安装级
用量/模型成本归因、Python SDK/无数据库模拟器、审批恢复审计、Remote Capability Connector、签名、
`action_id`、写回执和异步对账。

规模化生态仍需要真实部署和跨主体协作才能完成的部分包括：生产支付/税务/KYC 适配、第三方 Market
互操作认证、目标区域的容量基线，以及在实际基础设施上持续执行的备份恢复和跨区域灾备演练。Core 已提供
这些验收所需的冻结版本、幂等、审计、用量和故障恢复机制，但不能用仓库内单元测试冒充某个部署环境的
SLO 或灾备证据。

这些能力进入 Core 的前提是对所有 App 通用；任何具体 App 的会员表、订单表、领域模型和页面继续留在
App 仓库。
