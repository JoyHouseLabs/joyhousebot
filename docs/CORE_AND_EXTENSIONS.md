# Core 与扩展包边界设计

状态：Accepted（2026-08-09）
适用范围：JoyhouseBot Runtime、Console、官方扩展与第三方扩展

## 1. 决策

JoyhouseBot 聚焦个人与 OPC 的长期智能执行基座。Core 负责把目标可靠地变成可追踪、可恢复、可验证的
执行；外部产品、供应商协议、垂直业务和模型可调用的具体能力全部通过独立扩展包接入。

首个推荐组合是：

```text
joyhousebot core + model provider extension + email channel extension
```

项目尚未发布，不保留旧 Channel、Provider、Tool、MCP Client 的导入路径、配置字段或运行时翻译层。
旧配置会校验失败，部署必须从当前 `config.example.json` 开始。

产品分工见 [Joyhouse OPC 产品定位](PRODUCT_OPC.md)，逐项拆分结果见
[非 Core 功能拆分台账](NON_CORE_MIGRATION.md)。

## 2. Core 必须拥有

- `user_id + agent_id + root_run_id` 个人数据与执行隔离；
- Run / Task / Event / Trace / Approval / Action 状态机与最终一致性；
- Agent、Workflow、多 Agent DAG、人工反馈和长任务恢复；
- Capability Registry、权限、配额、审批、幂等与写入回执；
- PostgreSQL repositories、Outbox、Lease、Schedule、重试、对账与审计；
- Artifact / Work 的版本、证据、发布、分享和撤销；
- HTTP / SSE 公共执行入口和对外 MCP Gateway；
- Eval、成本、质量、性能和可观测闭环；
- 扩展 Manifest、发现、发布、Worker 加载确认与回滚；
- 扩展使用的窄 SDK，以及 Run-scoped filesystem、sandbox、memory、knowledge、delivery、schedule 服务。

Core 不选择供应商或默认模型。空 Agent 目录只接受 `runtime.bootstrapModel` / `LLM_MODEL` 提供的精确
模型 ID；未配置时写入 `unconfigured/model`，不会暗中选择某家供应商。

空数据库只种入一个无产品人格、无默认 Tool、默认关闭 Memory 的 `default` Agent，以保证 Runtime
能够启动。Joyhouse 的人格、记忆策略、提示词 Skill 和持续任务必须通过产品发布物安装，不能写回 Core。

## 3. 扩展必须拥有

- Channel 协议、供应商 SDK、Webhook、OAuth、轮询、媒体转换和 sidecar；
- Anthropic、OpenAI-compatible 等模型协议和供应商端点元数据；
- Shell、Filesystem、Research、Context Assets、Runtime Control 等具体模型能力；
- 外部 MCP Client、CRM、日历、GitHub、文档、表单、支付和内容平台连接；
- 垂直业务的 Skill、Scenario、Workflow、外部读模型、Task Pack 和业务 UI；
- 供应商配置 Schema、依赖、权限和运维文档。连通性检查必须作为 Worker 内的可审计 Run，不能由 API 临时加载扩展执行。

独立业务产品保留自己的界面，只通过版本化 HTTP/SSE、MCP 或 Capability 进入统一 Run/Task 链路。
业务路由、数据库模型和页面不得写入 Core。`smart-study` 保持独立。

## 4. 判定规则

模块只有同时满足以下条件才可进入 Core：

1. 缺少它会破坏所有执行的正确性、安全性或公共协议；
2. 不依赖某个渠道、模型、SaaS 或垂直业务供应商；
3. 不安装任何官方扩展时，最小 Runtime 仍可独立测试和运行。

依赖方向固定为：

```text
business app / extension -> joyhousebot.extension_sdk -> core contracts/runtime
core runtime -X-> extension implementation / vendor SDK
```

扩展不能 import `joyhousebot.api`、`storage`、`runtime` 等内部包，不能持有数据库连接，不能创建第二套
Run/Task、重试或持久化状态机。

## 5. Extension SDK 与安装协议

`joyhousebot.extension_sdk` 是扩展唯一 Python 导入面，提供：

- `ExtensionManifest`、`PluginManifest` 与版本化组件引用；
- Channel envelope、`ChannelPlugin`、`RunAdapter`；
- Capability definition/context/result 与 `WriteReceipt`；
- `CapabilityServiceBroker` 及其 Context、Scratch、Sandbox、Runtime Control 窄端口；
- `ToolConnectorExtension` 与生命周期契约。

官方扩展是 `extensions/` 下的独立 Python distribution。每种扩展只能通过对应 entry point 被 Core
发现，不接受配置中的任意模块路径：

```toml
[project.entry-points."joyhousebot.channels"]
channel-email = "joyhousebot_channel_email:create_plugin"

[project.entry-points."joyhousebot.capabilities"]
capability-research = "joyhousebot_capability_research:create_plugin"

[project.entry-points."joyhousebot.model_providers"]
provider-anthropic = "joyhousebot_provider_anthropic:create_extension"
```

安装只表示代码可发现。所有扩展（包括 Provider）都必须以完整 extension ID 出现在
`extensions.enabled`；Provider 的凭据和端点另外放在 `providers.settings`。Core 先按 entry point
metadata 过滤，只有显式启用后才 import 扩展代码。Manifest ID、entry point 名、API/SDK 版本和
不可变 build digest 任一不匹配时直接拒绝加载。

## 6. 唯一配置结构

```json
{
  "providers": {
    "defaultProvider": "anthropic",
    "settings": {
      "anthropic": {"apiKey": "env://ANTHROPIC_API_KEY"}
    }
  },
  "extensions": {
    "enabled": [
      "provider-anthropic",
      "channel-email",
      "capability-research",
      "capability-context-assets"
    ],
    "discoverEntryPoints": true,
    "settings": {
      "channel-email": {
        "consentGranted": true,
        "imapPassword": "env://EMAIL_IMAP_PASSWORD",
        "smtpPassword": "env://EMAIL_SMTP_PASSWORD"
      }
    }
  }
}
```

- `providers.anthropic`、`channels.email`、`tools.exec`、`tools.retrieval` 均不存在；
- `extensions.modules`、`tools.capability_plugins` 均不存在；
- `LLM_API_KEY` / `LLM_API_BASE` 必须同时用 `LLM_PROVIDER` 指明协议；
- Secret 只允许 `env://VARIABLE` 或进程环境变量。

## 7. Channel 闭环

Core 负责入站去重、Run 提交、PG Outbox、Lease、fencing、投递重试、死信和审计。Channel 扩展只负责
协议转换、连接和一次投递，返回供应商 message id、错误类别与重试提示。

Email 是 OPC 第一阶段唯一推荐 Channel，但仍是可卸载扩展。其他 Channel 均为独立 distribution，
Console 不把未安装扩展描述为内置能力。

## 8. 高风险能力闭环

- Shell 扩展只能调用 Core 的 fail-closed container sandbox，容器不可用时失败；
- Filesystem 扩展只能访问当前 Run 的隔离 scratch，不能把 `memory/` 当宿主文件；
- Context Assets 通过窄服务访问用户 Memory/Knowledge，写入必须携带冻结 Action 和回执；
- Runtime Control 通过窄服务管理 Schedule、Outbox、Monitor scratch 和 child Run；
- MCP Client 是 `connector-mcp-client`，HTTP 受 SSRF/DNS pinning 约束，stdio 默认关闭；
- 所有副作用继续进入统一 Dispatcher、审批、Action、对账与审计链。

## 9. 发布状态

扩展发布沿用统一状态机：

```text
installed -> discovered -> staged -> Worker exact build ACK -> active
                                      failure: previous active remains
```

Manifest 至少包含不可变 ID/版本/build digest、运行时 API、SDK 版本、扩展类型、权限、依赖、配置
Schema 和隔离策略。发布失败不能覆盖当前 active 版本。

## 10. 验收标准

1. `pip install joyhousebot` 不安装任何渠道或模型供应商 SDK；
2. 无扩展时 API/Worker 可启动，Core 使用 fail-closed 的 `unconfigured/model` 占位实现，模型调用会明确失败，Tool 目录为空；
3. 安装但未启用的扩展不执行、不注册模型可见能力；
4. 扩展只 import `joyhousebot.extension_sdk`；
5. 旧导入、旧配置和任意模块加载入口不存在；
6. 所有入站、出站和副作用进入统一 Runtime 链路；
7. API 进程不 import 或执行扩展实现，模型、Tool 和扩展诊断只能在 Worker 链路发生；
8. Ruff、Core-only 门禁、Runtime 测试、扩展契约测试、Console build 和预发布检查通过。

扩展集成测试前运行 `./scripts/install-test-extensions.sh`。它只向开发虚拟环境注册独立包；Core 的
`uv sync --frozen` 最小依赖仍不包含供应商依赖。
