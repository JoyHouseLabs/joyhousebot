# joyhousebot 文档

这里只保留开源 Agent 执行与治理 Runtime 当前有效的权威文档。joyhousebot 将长程执行、恢复、权限、
审批、审计和发布治理开放给个人、开发者和产品团队；JoyHouse 产品与 JoyHouse Market 位于相邻私有仓库，本仓库只说明它们
依赖的 Runtime 契约与开放协议。

## 核心架构

- [architecture/PRODUCT_RUNTIME_BOUNDARY.md](architecture/PRODUCT_RUNTIME_BOUNDARY.md)：Product、App、Runtime、Extension、Market 与 Console 的所有权和依赖方向。
- [architecture/AUTHORITY_MODEL.md](architecture/AUTHORITY_MODEL.md)：Owner、Installation、Host、Operator 四类 Principal 与隔离规则。
- [architecture/PUBLIC_EXECUTION_API.md](architecture/PUBLIC_EXECUTION_API.md)：EntryPoint 驱动的公共 `/v2` HTTP/SSE 与 SDK 契约。

## Runtime 与部署

- [ARCHITECTURE.md](ARCHITECTURE.md)：总体架构、身份边界、部署拓扑、公共 API 与工程约束。
- [COMPONENT_LIFECYCLE.md](COMPONENT_LIFECYCLE.md)：API、PostgreSQL 表族与后台 Worker 的成熟度和删除准则。
- [CORE_AND_EXTENSIONS.md](CORE_AND_EXTENSIONS.md)：Core、Extension 与独立 App 的代码和依赖边界。
- [DATA_AND_ECOSYSTEM_BOUNDARIES.md](DATA_AND_ECOSYSTEM_BOUNDARIES.md)：统一数据库连接、表所有权、迁移与未来拆分边界。
- [OPERATIONS.md](OPERATIONS.md)：PostgreSQL 优先的本地启动、角色扩容、健康检查与故障排查。
- [PRODUCTION_ACCEPTANCE.md](PRODUCTION_ACCEPTANCE.md)：Eval、SLO、规模压测与故障恢复验收。
- [../deploy/systemd/README.md](../deploy/systemd/README.md)：systemd 角色拆分、目录权限与升级顺序。

## 执行与能力协议

- [EXECUTION_MODES.md](EXECUTION_MODES.md)：Agent、Team、Scenario 与 Workflow 的统一 Run 提交模式。
- [AGENT_TEAMS.md](AGENT_TEAMS.md)：多 Agent Team 的版本、委派、共享 Workspace 与预算边界。
- [SKILLS.md](SKILLS.md)：声明式 Skill 的资产、发布与分发模型。
- [KNOWLEDGE_RUNTIME.md](KNOWLEDGE_RUNTIME.md)：Knowledge Core/扩展边界、版本索引和乱序保护。
- [MULTIMODAL_RETRIEVAL_EXTENSION_CONTRACT.md](MULTIMODAL_RETRIEVAL_EXTENSION_CONTRACT.md)：文档、视觉、Embedding 与 Rerank 的 Extension 契约。
- [WORK_ASSET_CLOSED_LOOP.md](WORK_ASSET_CLOSED_LOOP.md)：Artifact 成果化、Work 版本、App 交接、结果回流与审计闭环设计。
- [mcp-gateway.md](mcp-gateway.md)：Streamable HTTP MCP 网关、调用语义与部署 Origin 配置。

## App 与生态协议

- [APP_INTEGRATION.md](APP_INTEGRATION.md)：独立 App 的身份、数据、HTTP/SSE 与远程能力协作契约。
- [APP_PACKAGES.md](APP_PACKAGES.md)：App Package 的安装清单、入口、依赖锁与生命周期。
- [APP_MARKET_GOVERNANCE.md](APP_MARKET_GOVERNANCE.md)：可替换 Market Registry 的签名、授权与分发协议。
- [REMOTE_CAPABILITY_PROTOCOL.md](REMOTE_CAPABILITY_PROTOCOL.md)：签名 HTTP Remote Capability 的发布、调用与对账协议。
- [EXTENSION_DEPLOYMENT.md](EXTENSION_DEPLOYMENT.md)：Extension 的安装、发现、发布和 Worker ACK 流程。
- [EXTENSION_HOST_PROTOCOL.md](EXTENSION_HOST_PROTOCOL.md)：基于 Remote Capability v1 的通用 Extension Host Profile、Node SDK、隔离、OpenCLI/设备桥、Tool Broker 与三类运行模式。
- [DEVICE_HOST_TRANSPORT.md](DEVICE_HOST_TRANSPORT.md)：Cloud 到本机 Host 的设备身份、pull delivery、lease fencing、恢复与 Node 客户端协议。
- [HOST_MODEL_AND_TOOL_GATEWAYS.md](HOST_MODEL_AND_TOOL_GATEWAYS.md)：Host 短期模型/工具授权、预算、审批和 child Action 链。
- [PI_RUNNER.md](PI_RUNNER.md)：Pi Pilot 的 worktree、模型代理、测试证据和安全边界。

代码、接口或部署方式发生变化时，应直接更新对应的权威文档，不再新增兼容旧架构的平行说明。
