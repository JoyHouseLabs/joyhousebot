# 非 Core 功能拆分台账

状态：Completed（2026-08-09）
原则：具体供应商、外部协议和模型可调用能力均为独立制品；不保留旧入口。

## Core 最终保留面

- 身份、权限、密钥引用与个人数据隔离；
- Run / Task / Event / Trace / Approval / Action；
- PostgreSQL Outbox、Lease、Schedule、恢复、对账和审计；
- Agent/Workflow、Capability Dispatcher、版本发布与 Worker ACK；
- Artifact / Work、证据、分享和撤销；
- HTTP/SSE、对外 MCP Gateway 与扩展公共 SDK；
- fail-closed sandbox 和 Run-scoped 数据服务；
- 成本、Eval、质量和可观测契约。

## 已拆分制品

| 类型 | 独立制品 | Core 保留 |
| --- | --- | --- |
| Email | `joyhousebot-channel-email` | Envelope、RunAdapter、Outbox、Lease |
| QQ | `joyhousebot-channel-qq` | 同上 |
| DingTalk | `joyhousebot-channel-dingtalk` | 同上 |
| Feishu | `joyhousebot-channel-feishu` | 同上 |
| Slack | `joyhousebot-channel-slack` | 同上 |
| Telegram | `joyhousebot-channel-telegram` | 同上 |
| Discord | `joyhousebot-channel-discord` | 同上 |
| WhatsApp | `joyhousebot-channel-whatsapp`，含独立 Node bridge | 同上 |
| Anthropic | `joyhousebot-provider-anthropic` | Provider contract、路由、审计 |
| OpenAI-compatible | `joyhousebot-provider-openai-compatible` | 同上 |
| Groq Transcription | `joyhousebot-capability-groq-transcription` | Capability 治理 |
| Media Generation | `joyhousebot-capability-media-generation`，含 Seedream、Seedance、即梦适配 | Capability、Action、审批、异步对账与 Artifact |
| Research/Web | `joyhousebot-capability-research` | 网络安全策略与 Dispatcher |
| Context/Knowledge Tool | `joyhousebot-capability-context-assets` | Memory/Knowledge 事实源和窄服务 |
| Filesystem Tool | `joyhousebot-capability-filesystem` | Run scratch 隔离服务 |
| Shell Tool | `joyhousebot-capability-shell` | fail-closed sandbox 与命令策略 |
| Runtime Control Tool | `joyhousebot-capability-runtime-control` | Schedule、Outbox、child Run、Monitor 服务 |
| 外部 MCP Client | `joyhousebot-connector-mcp-client` | 对外 MCP Gateway、SSRF 和 Dispatcher |
| 企业业务程序 | `joyhousebot-connector-http-capability` | Capability、Action、审批、对账和审计；业务代码保持独立进程 |

## 已删除的 Core 面

- `joyhousebot.channels.plugins.builtin.*` 与 WhatsApp bridge；
- `joyhousebot.providers.anthropic/openai_compatible/transcription`；
- `joyhousebot.agent.tools` 下的 Shell、Filesystem、Web、Retrieve、Memory、Runtime Control、MCP；
- Core 默认提示词 Skill、产品化 JoyAgent 和无查询入口的 Plugin Projection 执行链；
- Provider/Channel/Tool/MCP Server 的供应商专用配置模型；
- Console 的独立 MCP Server 管理页和八渠道内置展示；
- 旧 import 转发、命名 Provider 字段、任意模块路径加载和配置翻译。

## 唯一启用规则

- 安装：独立 distribution 提供 entry point；
- Channel/Capability/Connector 部署准入：`extensions.allowedIds`；运行启停：PostgreSQL extension inventory；
- Channel 和普通扩展部署设置：`extensions.settings.<extension-id>`；
- 通用 HTTP Capability Connector：部署准入在 `extensions.allowedIds`，具体远程服务配置进入
  PostgreSQL `remote_connection` Revision，并由 Console/API 发布；
- Provider 部署准入：完整 ID 进入 `extensions.allowedIds`，例如 `provider-anthropic`；
- Provider 设置：`providers.settings.<provider-name>`；
- 模型初始化：精确 `runtime.bootstrapModel` / `LLM_MODEL`；
- 旧配置直接校验失败，不迁移、不翻译。

## 下一阶段

拆包完成后不再继续扩充 Core 能力。后续工作集中在：

1. 独立发布 Extension SDK 和官方扩展 wheel；
2. 补齐安装、签名、SBOM、Worker exact-build ACK 和回滚体验；
3. 以 Email + 市场研究 Task Pack 形成 OPC 首个可用组合；
4. 新增 CRM、日历、内容平台时，只新增 Connector/Task Pack，不回填 Core；
5. 用架构测试持续阻止供应商依赖、旧入口和业务 UI 回流。

App Pack 的 Manifest、依赖锁、发布、安装、启停、升级、回滚与审计已进入 Core 控制面，协议见
[App Pack 设计与安装协议](APP_PACKS.md)。垂直角色、页面和业务规则仍必须由独立 App Pack 提供。

## Core-only 发布门禁

CI 的 `core-only` 作业只执行 `uv sync --extra dev --frozen`，不会安装 `extensions/` 下任何发行包，
并断言四类 extension entry point 均为空、空配置不会导入扩展。全量扩展契约测试才单独运行
`scripts/install-test-extensions.sh`。
