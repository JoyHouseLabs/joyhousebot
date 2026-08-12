# JoyhouseBot 测试说明

本目录验证 Runtime 契约、状态机、安全边界和 PostgreSQL 集成。

- 数据库测试只允许使用显式测试数据库；不得连接开发库或生产库。
- 状态机测试应覆盖成功、失败、取消、超时、重试、接管和幂等重复提交。
- 公共 API、事件、Manifest、Market Protocol 和 App SDK 改动必须有契约测试。
- 测试不得依赖执行顺序、真实供应商 Key、外部付费服务或宿主机 Shell。
- `tests/core_only/` 必须证明未安装可选 Extension 时 Core 仍可导入和启动。

优先复用 `tests/support/` 的测试存储和工厂，不在单个测试中复制迁移或清库逻辑。
