# Joyhousebot 文档

这里只保留当前云平台仍有效的文档：

- [ARCHITECTURE.md](ARCHITECTURE.md)：唯一总体架构、身份边界、部署拓扑、公共 API 与工程约束。
- [DATA_AND_ECOSYSTEM_BOUNDARIES.md](DATA_AND_ECOSYSTEM_BOUNDARIES.md)：第一阶段统一数据库连接、表所有权、迁移与未来拆分边界。
- [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md)：破坏性重构阶段、验收标准和完成定义。
- [OPERATIONS.md](OPERATIONS.md)：PostgreSQL 优先的本地启动、角色扩容、健康检查与故障排查。
- [PRODUCTION_ACCEPTANCE.md](PRODUCTION_ACCEPTANCE.md)：业务 Eval、SLO、规模压测与故障恢复验收。
- [KNOWLEDGE_RUNTIME.md](KNOWLEDGE_RUNTIME.md)：Knowledge Core/扩展边界、版本索引和乱序保护。

代码、接口或部署方式发生变化时，应直接更新这三份文档，不再新增兼容旧架构的平行说明。
