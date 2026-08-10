# 扩展发布与部署

Core、官方扩展和业务扩展遵守同一套安装、启用和发布规则。完整边界见
[Core 与扩展包边界设计](CORE_AND_EXTENSIONS.md)。

## 1. 独立制品

每个扩展都是独立 Python distribution，不进入 Core wheel。Entry point 名必须等于 Manifest 的完整
`extension_id`：

```toml
[project.entry-points."joyhousebot.capabilities"]
capability-market-research = "market_research:create_plugin"
```

扩展只能 import `joyhousebot.extension_sdk`，不能 import `api`、`application`、`runtime`、`storage`
或其他 Core 内部模块。Core-only CI 会在不安装任何扩展的隔离环境中运行。

## 2. 不可变发布身份

每个版本必须声明：

- 完整 extension ID、语义版本和 distribution 名；
- 实际制品的 `sha256:<64 hex>` build digest；
- Runtime API 与 Extension SDK 版本；
- 扩展类型、执行隔离、最小权限和外部依赖；
- 非敏感配置 Schema；
- Capability 扩展拥有的 Tool、Connector、Skill、Scenario、Workflow 或 Agent 组件。

同一 `extension_id + version` 的 Manifest、digest 和组件目录不可原地修改。实现有任何变化都发布新版本。

## 3. 安装不等于启用

安装 wheel 只增加 entry point metadata。Core 会先按 `extensions.enabled` 过滤 metadata，只有完整 ID
明确启用后才 import 扩展代码：

```json
{
  "extensions": {
    "enabled": [
      "provider-openai-compatible",
      "channel-email",
      "capability-market-research"
    ],
    "settings": {
      "channel-email": {
        "imapPassword": "env://EMAIL_IMAP_PASSWORD"
      }
    }
  }
}
```

配置引用未安装 ID、entry point 与 Manifest ID 不一致、SDK/API 不兼容或 digest 无效时，负责该扩展的
Worker 直接启动失败。未启用的已安装包不会被 import。

## 4. 进程边界

| 扩展类型 | 加载进程 | 不会加载的进程 |
| --- | --- | --- |
| Provider | Agent Worker | API、Control、Scheduler、Migrator、Channel Worker |
| Capability / Connector | Agent Worker | API、Control、Scheduler、Migrator、Channel Worker |
| Channel | Channel Worker | API、Control、Scheduler、Migrator、Agent Worker |

API 只查询 PostgreSQL 中的 Manifest、组件、Worker 和调用记录。API health read 不执行扩展回调，外部
连通性检查必须提交为可审计 Run。

## 5. 发布与回滚

标准状态机是：

```text
discovered → staged → 目标 Worker exact version/build ACK → active
                                      failure → previous active remains
```

Channel release 只等待 Channel Worker；Provider、Capability 和 Connector release 只等待 Agent Worker。
回滚同样创建新的 rollout，不能直接改数据库中的 active 标记。Agent Revision 引用 Capability 扩展时，
还必须固定 extension ID、版本和 build digest。

## 6. 数据与副作用

扩展不会收到 RuntimeStore、数据库连接或任意 Core Service Locator。Capability 只能使用 SDK 中按职责
拆分的 Context、Scratch、Sandbox、Delivery、Child Run 和 Schedule 端口。

外部业务系统继续拥有自己的数据库和迁移。写入必须通过 Connector/Capability 接收 Runtime 冻结的
`action_id` 与 `idempotency_key`，把同一键传给业务写接口，并返回 `WriteReceipt`。业务服务不能创建
第二套 JoyhouseBot Run/Task 状态机，也不能根据调用内容自行选择其他用户上下文。

## 7. 构建与验收

本仓库开发环境先安装官方扩展：

```bash
uv sync --extra dev --frozen
./scripts/install-test-extensions.sh
./scripts/pre_release_check.sh
```

生产环境应分别构建 Core wheel 与所需扩展 wheel，记录各自 SHA-256、签名和 SBOM，再以明确组合构建
Runtime 镜像。Docker 可用 `JOYHOUSEBOT_EXTENSIONS` build arg 选择扩展，空值生成 Core-only 镜像。
