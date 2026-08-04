# 业务插件发布与部署

Joyhousebot 的核心只提供 Agent 云运行时和治理面；业务能力以独立 Python 包发布。Dinq Discover 是第一
个采用该边界的业务插件。

## 不可变发布单元

每次插件发布必须具有新的语义版本或新的发布版本，并提供：

- `plugin_id`、`version`、wheel/image 的 `build_digest`；
- 每个 Tool、Connector、Skill 的完整 `CapabilityRef`；
- 连接依赖、数据分级、最小权限和成本策略；
- 健康检查，只检查配置与可执行节点，不能在常规 health read 中发起搜索或泄露凭据。

`DINQ_PLUGIN_BUILD_DIGEST` 是 Dinq 部署时传入的实际制品摘要。未设置时仅使用醒目的开发默认值，不能
作为生产发布证明。上线流程应先构建 wheel/image、计算其 SHA-256、再以同一 digest 发布插件和启动
Worker。

## Dinq 接入顺序

1. 在每个 Agent Worker 安装相同版本的 `dinq-plugin`，并在 Joyhousebot 配置的
   `tools.capability_plugins` 中加入 `dinq_plugin.discover.plugin`（或采用 entry point discovery）。
2. 以相同的 `DINQ_PLUGIN_BUILD_DIGEST` 启动 Worker；Worker heartbeat 会报告加载的发布单元。
   若检索服务不与 Worker 同机，还要设置 `DINQ_PLATFORM_SEARCH_URL` 和
   `DINQ_PLATFORM_SEARCH_ALLOWED_HOSTS`。后者是逗号分隔的服务 DNS/IP 白名单；默认只允许 loopback，
   Tool 输入永远不能改变目标地址。
3. 执行 `python -m dinq_plugin.discover.seed`。它发布 Dinq Capability、Skill 和新版 Scenario；所有
   Scenario 任务持久化完整 CapabilityRef，绝不按名称选择最新 Tool。
4. 通过控制面创建一个 Dinq Agent revision，并显式要求
   `dinq.discover@0.4.0 + build_digest`；按实际启用能力至少授予
   `dinq.search.read`，需要公网来源时再授予 `dinq.web.read`。
5. 等目标 Worker 均为 `execution_eligible` 后，才将该 revision 设为默认或把流量路由给它。

示例 Capability policy：

```json
{
  "mode": "catalog",
  "permissions": ["dinq.search.read", "dinq.web.read"]
}
```

业务 Agent 的默认选择是部署决策，不由插件种子过程擅自改变。这样通用 Joyhousebot 安装不会因某个业务
插件而被耦合，Dinq 环境也能明确审计“谁启用了哪些业务权限”。
