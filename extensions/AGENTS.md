# Porthouse Extension 协作说明

本目录保存可独立安装、发现、启停和升级的 Runtime 技术扩展。

## 可以包含

- 模型 Provider；
- Email、IM 等 Channel Adapter；
- MCP、HTTP 等通用 Connector；
- 可跨产品复用的原子 Capability；
- 沙箱化 Shell、文件、研究、媒体生成等技术能力。

## 不可以包含

- 拥有独立用户、订单、业务数据库或业务页面的 App；
- 某个垂直项目的硬编码 Workflow、领域表和交易逻辑；
- 第二套 Run/Task、重试、审批、幂等或审计状态机；
- 绕过 Core Dispatcher 的直接 Tool 调用；
- 明文 Secret、供应商 Key 或降级到宿主机执行的 Shell。
- 针对 Runtime 数据库的自定义表、migration、超级用户权限或直接 Repository 写入。

每个扩展必须有稳定 ID、版本、Manifest、配置 Schema、权限/出站声明和健康检查。新增扩展应能在未安装
时不影响 Core 启动，并为注册、调用、失败关闭和卸载路径提供测试。

需要专属业务持久化的 Extension 应升级为 Connector/App 服务。第一阶段官方服务使用共享
`PORTHOUSE_DATABASE_URL` 和 `app_<id>_*` 表；远程第三方服务可以使用自己的数据库。两者都应优先复用 Core
的通用 Invocation、Receipt、Outbox、Artifact 和审计机制，不能由 Extension 直接注入 DDL。
