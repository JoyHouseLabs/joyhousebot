# Joyhousebot 文档

这里只保留开源企业级 Agent 执行与治理 Runtime 当前有效的权威文档。JoyhouseBot 将长程执行、恢复、权限、
审批、审计和发布治理开放给开发者；JoyHouse 产品与 JoyHouse Market 位于相邻私有仓库，本仓库只说明它们
依赖的 Runtime 契约与开放协议。

### 架构与部署

- [ARCHITECTURE.md](ARCHITECTURE.md)：总体架构、身份边界、部署拓扑、公共 API 与工程约束。
- [CORE_AND_EXTENSIONS.md](CORE_AND_EXTENSIONS.md)：Core、Extension 与独立 App 的代码和依赖边界。
- [DATA_AND_ECOSYSTEM_BOUNDARIES.md](DATA_AND_ECOSYSTEM_BOUNDARIES.md)：统一数据库连接、表所有权、迁移与未来拆分边界。
- [OPERATIONS.md](OPERATIONS.md)：PostgreSQL 优先的本地启动、角色扩容、健康检查与故障排查。
- [PRODUCTION_ACCEPTANCE.md](PRODUCTION_ACCEPTANCE.md)：Eval、SLO、规模压测与故障恢复验收。

### 执行与能力协议

- [EXECUTION_MODES.md](EXECUTION_MODES.md)：Agent、Team、Scenario 与 Workflow 的统一 Run 提交模式。
- [AGENT_TEAMS.md](AGENT_TEAMS.md)：多 Agent Team 的版本、委派、共享 Workspace 与预算边界。
- [SKILLS.md](SKILLS.md)：声明式 Skill 的资产、发布与分发模型。
- [KNOWLEDGE_RUNTIME.md](KNOWLEDGE_RUNTIME.md)：Knowledge Core/扩展边界、版本索引和乱序保护。
- [mcp-gateway.md](mcp-gateway.md)：Streamable HTTP MCP 网关、调用语义与部署 Origin 配置。

### App 与生态协议

- [APP_INTEGRATION.md](APP_INTEGRATION.md)：独立 App 的身份、数据、HTTP/SSE 与远程能力协作契约。
- [APP_PACKS.md](APP_PACKS.md)：App Pack 的安装清单、入口、依赖锁与生命周期。
- [APP_MARKET_GOVERNANCE.md](APP_MARKET_GOVERNANCE.md)：可替换 Market Registry 的签名、授权与分发协议。
- [REMOTE_CAPABILITY_PROTOCOL.md](REMOTE_CAPABILITY_PROTOCOL.md)：签名 HTTP Remote Capability 的发布、调用与对账协议。
- [PLUGIN_DEPLOYMENT.md](PLUGIN_DEPLOYMENT.md)：Extension 的安装、发现、发布和 Worker ACK 流程。

代码、接口或部署方式发生变化时，应直接更新对应的权威文档，不再新增兼容旧架构的平行说明。
