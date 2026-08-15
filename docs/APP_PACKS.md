# App Pack 设计与安装协议

状态：Implemented MVP（2026-08-10）

## 1. 目的

JoyhouseBot 的产品结构固定为：

```text
Core Runtime + Extensions + App Packs
```

- Core 是唯一执行与治理内核，拥有 Run / Task / Event / Trace / Approval / Action；
- Extension 是部署级技术制品，提供 Provider、Channel、Connector 或 Capability；
- App Pack 是面向用户的可安装应用清单，组合已发布的 Agent、Skill、Workflow、Scenario、
  Capability、Integration 与 Extension；
- 独立 App 可以拥有自己的界面、用户、交易和业务数据库，通过公共 HTTP/SSE 与 Remote
  Capability 协作。

App Pack 不在 API 进程中加载业务代码，也不能引入第二套任务、重试、调度或审批状态机。

## 2. 清单

清单文件名是 `joyhouse.app.json`，`app_id + version + manifest_sha256` 唯一标识一个不可变发布：

```json
{
  "schema_version": 1,
  "app_id": "app.market-radar",
  "version": "0.1.0",
  "name": "Market Radar",
  "description": "持续跟踪市场机会并形成周报。",
  "publisher": "Joyhouse",
  "core": {"min_version": "0.1.2", "max_version": ""},
  "extensions": [
    {
      "extension_id": "capability-research",
      "version": "0.1.0",
      "build_digest": "sha256:<64 hex>"
    }
  ],
  "capabilities": [],
  "assets": {
    "agents": [{"agent_id": "market-researcher", "revision_id": "agent-rev-id"}],
    "teams": [{"team_id": "team.market-research", "revision_id": "team-rev-id"}],
    "skills": [{"skill_id": "skill.market-analysis", "version": "1.0.0", "content_sha256": "sha256:<64 hex>"}],
    "workflows": [],
    "scenarios": [{"scenario_id": "weekly-market-radar", "version": 1}]
  },
  "entrypoints": [
    {
      "entrypoint_id": "weekly-research",
      "name": "生成市场机会简报",
      "default": true,
      "execution": {
        "mode": "team",
        "team_id": "team.market-research",
        "revision_id": "team-rev-id"
      },
      "interaction_mode": "background",
      "timeout_seconds": 1800
    }
  ],
  "integrations": [],
  "permissions": ["runs.submit"],
  "work_consumers": [
    {
      "consumer_id": "opportunity-tracking",
      "name": "建立机会跟进",
      "purposes": ["track_opportunity"],
      "media_types": ["application/json", "text/markdown"],
      "max_data_classification": "internal",
      "input_schema": {"type": "object"}
    }
  ],
  "secrets": [{"name": "SEARCH_API_KEY", "required": true}],
  "triggers": [],
  "evaluations": [],
  "configuration_schema": {},
  "ui": {},
  "metadata": {}
}
```

所有可执行 Capability 必须精确绑定 Extension 版本和 build digest；所有 Skill 必须精确绑定内容
摘要。清单只声明 Secret 名称，不保存值；安装配置中的敏感字段只能使用 `env://VARIABLE`。

Entry Point 是 App 对外稳定的业务动作，不是第五种执行模式。每个 Entry Point 必须精确引用同一清单
锁定的 Agent/AgentTeam Revision、Scenario Version 或 Workflow Revision；默认 Entry Point 只能有一个。
Runtime 在每次启动前重新校验安装依赖锁，并把安装、清单摘要和 Entry Point 身份冻结进 Run metadata。
Scenario Entry Point 除 `scenario_id + version` 外还必须声明 `agent_id + agent_revision_id`；固定 Scenario
生成的 Graph 顶层快照和每个 Agent Task 都使用该 Revision，不能在启动时重新选择当前 Agent。

`work_consumers` 是可选的成果输入声明，不是对用户所有成果的读取许可。安装处于 `active` 时，Runtime
才会按 Work 的 `media_type` 与数据分级匹配消费者；用户从 Work 页面确认交接后，App 才能以绑定安装的
委托 Token 读取该版本的最小投影。完整状态机与 HTTP 契约见
[Work 成果资产闭环](WORK_ASSET_CLOSED_LOOP.md)。

## 3. 发布和安装状态机

App 发布状态：

```text
draft -> dependency validation -> published -> retired
```

用户安装状态：

```text
published release -> installed -> active <-> disabled -> uninstalled
                         |
                         +-> upgrade -> installed -> active
                                      -> rollback(previous) -> disabled
```

发布校验和每次启用前校验都会重新核对精确 Extension 构建、Capability 来源、Agent Revision、
AgentTeam Revision、Skill 摘要、Scenario 版本、用户 Workflow Revision 和 Integration Revision。安装保存依赖锁；升级
保存上一版本；回滚不会删除历史 Run、Artifact 或安装审计事件。

安装、发布、启用彼此独立：安装 App Pack 不会自动扩大 Agent allowlist，不会自动解析密钥，也不会
绕过 Capability Dispatcher、审批、配额和 Action 幂等。

## 4. API 与 Console

控制面 API 位于 `/v1/admin/apps`：

- 保存、校验和发布 App Pack Release；
- 按当前 `user_id` 安装或升级；
- 启用、停用、回滚和软卸载；
- 查询安装依赖锁和安装事件。

用户/App 数据面位于：

- `GET /v1/apps`、`GET /v1/apps/{installation_id}`：列出当前主体可见的已启用安装；
- `POST /v1/apps/{installation_id}/runs`：按 Entry Point 提交统一 Run，必须携带幂等键；
- `/v1/admin/apps/clients`、`/v1/apps/{installation_id}/delegations`、`/v1/app-auth/token`：完成
  Client 注册、用户授权、短期 Token 交换与撤销；
- `/v1/apps/{installation_id}/callbacks`：登记/撤销签名终态回调；
- `GET /v1/apps/{installation_id}/usage`：查询安装级 Run、Token 与模型成本归因；
- `GET /v1/runs/{run_id}/app-callbacks`：查看重试、成功或死信状态；
- `POST /v1/runs/{run_id}/app-callbacks/{event_id}/replay`：用幂等键创建不可变人工重放；
- `/v1/admin/apps/clients/{client_id}/rotate-secret`：轮换一次性 Client Secret，并立即撤销旧 Token。

Console 的“构建中心 → Apps”同时提供清单编辑、依赖校验、发布、安装生命周期和安装级治理；治理页
集中展示用量、Client、Grant、Callback、死信诊断与人工重放。业务用户最终应使用面向具体 App 的产品
界面；此处是开发者和运营控制面。

运行中的 App Entry Point 如果等待补充信息或人工审批，会进入标准 Run 状态机，并由
[统一待办与审批视图](HUMAN_ACTIONS.md) 聚合展示；App Pack 不维护第二套用户待办表。

## 5. 当前边界与后续

MVP 引用已有发布资产，不复制发布者的私有上下文。Workflow 仍是用户拥有的资产，因此清单中的
Workflow 引用只会在安装用户拥有同一已发布 Revision 时通过。面向市场分发的下一步是 App 包内携带
脱离私有数据的资产源，安装时导入 Draft，经目标环境验证后再激活。

通用 AgentTeam Revision、共享 Workspace、任务委派和预算已进入 Core，协议见
[AgentTeam 协作协议](AGENT_TEAMS.md)。垂直客服、运维、研发和风控仍不写入 Core；具体角色、Prompt、
Skill、Workflow 和页面由 App Pack 交付。

远程目录、作者身份与签名、在线购买、Entitlement、更新订阅、评价、最小化用量和创作者结算不属于
本地 App Pack 状态机。目标协议见 [App Market 治理与跨实例分发协议](APP_MARKET_GOVERNANCE.md)：
Market 负责发现、交易与分发，本地 Runtime 仍独立完成签名验证、权限批准、Worker 加载确认、启用和回滚。
