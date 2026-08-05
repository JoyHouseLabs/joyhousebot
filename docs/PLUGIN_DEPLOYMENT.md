# 业务插件发布与部署

Joyhousebot 的核心只提供 Agent 云运行时和治理面；业务能力以独立 Python 包发布。Dinq Discover 是第一
个采用该边界的业务插件。

## 不可变发布单元

每次插件发布必须具有新的语义版本或新的发布版本，并提供：

- `plugin_id`、`version`、wheel/image 的 `build_digest`；
- 每个 Tool、Connector、Skill 的完整 `CapabilityRef`；
- 面向业务人员的 `quickstarts`：真实用户提示、关联的 Scenario / Capability、必需连接和预期结果；
- 连接依赖、数据分级、最小权限和成本策略；
- 健康检查，只检查配置与可执行节点，不能在常规 health read 中发起搜索或泄露凭据。

`DINQ_PLUGIN_BUILD_DIGEST` 是 Dinq 部署时传入的实际制品摘要。未设置时仅使用醒目的开发默认值，不能
作为生产发布证明。上线流程应先构建 wheel/image、计算其 SHA-256、再以同一 digest 发布插件和启动
Worker。

## Dinq 接入顺序

1. 在每个 Agent Worker 安装相同版本的 `dinq-plugin`，并在 Joyhousebot 配置的
   `tools.capability_plugins` 中加入 `dinq_plugin.discover.plugin`（或采用 entry point discovery）。
2. 以相同的 `DINQ_PLUGIN_BUILD_DIGEST` 启动 Worker；Worker heartbeat 会报告加载的发布单元。
   本地检索能力使用 `DINQ_LOCAL_SEARCH_URL`（默认 `http://127.0.0.1:8200/search`）和
   `DINQ_LOCAL_SEARCH_ALLOWED_HOSTS`。后者是逗号分隔的服务 DNS/IP 白名单；默认只允许 loopback，
   Tool 输入永远不能改变目标地址。
3. 执行 `python -m dinq_plugin.discover.seed`。它只发布 Dinq Capability、Skill 和新版 Scenario；所有
   Scenario 任务持久化完整 CapabilityRef，绝不按名称选择最新 Tool。
4. 对 Dinq 服务部署，再显式执行 `python -m dinq_plugin.discover.bootstrap`。它在上述已发布 Catalog
   基础上创建 `main-coordinator:v2`，固定 `dinq.discover@<version> + build_digest` 并只授予 `dinq.*`。
   该命令是部署决策，普通 Joyhousebot 安装或仅安装插件时绝不会自动提升业务权限。
5. 等目标 Worker 均为 `execution_eligible` 后，才将该 revision 设为默认或把流量路由给它。

示例 Capability policy：

```json
{
  "mode": "catalog",
  "permissions": ["dinq.search.read", "dinq.web.read"]
}
```

业务 Agent 的默认选择是部署决策，不由插件种子过程擅自改变。插件的 `quickstarts` 会在控制台的插件
页面中通用渲染；点击后只是把业务提示、目标 Agent 和 Scenario 线索带入在线试用，仍经由正常的
Coordinator 路由、追问、权限校验和 Run 审计执行。这样通用 Joyhousebot 安装不会因某个业务插件而被
耦合，Dinq 环境也能明确审计“谁启用了哪些业务权限”。
