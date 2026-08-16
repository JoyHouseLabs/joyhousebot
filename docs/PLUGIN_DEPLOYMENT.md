# 扩展发布与部署

Core、官方扩展和业务扩展遵守同一套安装、启用和发布规则。完整边界见
[Core 与扩展包边界设计](CORE_AND_EXTENSIONS.md)。

## 1. 独立制品

当前由 Worker 进程内加载的扩展都是独立 Python distribution，不进入 Core wheel。Entry point 名必须等于
Manifest 的完整 `extension_id`：

```toml
[project.entry-points."joyhousebot.capabilities"]
capability-market-research = "market_research:create_plugin"
```

扩展从 Core 只能 import `joyhousebot.extension_sdk`，不能 import `api`、`application`、`runtime`、`storage`
或其他 Core 内部模块。若 Extension 复用另一个技术 Extension 的纯解析库，必须声明精确的 distribution
依赖（如 `capability-context-assets` 对 `capability-document-processing`），不能依赖未声明的目录顺序或进程
内全局注册表。Core-only CI 会在不安装任何扩展的隔离环境中运行。

需要 Node.js、其他语言或独立依赖树的扩展使用独立 bundle 或 OCI image，由通用 Extension Host 管理，
不注册 Python entry point，也不进入 Core wheel。它们复用 Remote Capability v1 的签名、连接 Revision、
Capability rollout 和 Worker ACK；长任务、渠道与事件源的具体边界见
[Extension Host Profile v1](EXTENSION_HOST_PROTOCOL.md)。Node SDK、签名 transport、独立进程 Supervisor、
operation 恢复和 OpenCLI Host Pilot 已实现；Device Transport、Model Gateway、Tool Broker 等后续阶段见
[Polyglot Extension Host 实施计划](POLYGLOT_EXTENSION_HOST_PLAN.md)。

## 2. 不可变发布身份

每个版本必须声明：

- 完整 extension ID、语义版本和 distribution 名；
- 实际制品的 `sha256:<64 hex>` build digest；
- Runtime API 与 Extension SDK 版本；
- 扩展类型、执行隔离、最小权限和外部依赖；
- 非敏感配置 Schema；
- Capability 扩展拥有的 Tool、Connector、Scenario、Workflow 或 Agent 组件；可分发的 Skill 作为独立资产导入 Skill Registry，再经过校验与发布，不能作为 Capability 特例直接生效。

异构 Host Extension 还必须声明语言和运行时版本、Host protocol、Node/语言 SDK 版本、entrypoint、
lockfile digest、资源上限、网络/workspace/secret 权限和运行 Profile。Host bundle、Extension bundle、
Manifest、SBOM 和签名共同构成不可变发布身份。

同一 `extension_id + version` 的 Manifest、digest 和组件目录不可原地修改。实现有任何变化都发布新版本。

## 3. 可用、安装、准入与生效是四个状态

扩展源码目录只增加“可用”元数据，安装 wheel 才增加 distribution entry point。Core 按
`extensions.allowedIds` 建立不可由 Console 扩大的部署安全边界；运行期启停写入 PostgreSQL，
Worker 加载确认后才算实际生效：

```json
{
  "extensions": {
    "catalogDirectories": ["./extensions"],
    "allowedIds": [
      "provider-openai-compatible",
      "channel-email",
      "capability-market-research"
    ],
    "initiallyActive": ["provider-openai-compatible"],
    "allowConsoleActivation": true,
    "settings": {
      "channel-email": {
        "imapPassword": "env://EMAIL_IMAP_PASSWORD"
      }
    }
  }
}
```

`initiallyActive` 只在 inventory 首次建行时播种，之后 Console 中的期望状态不会被重启覆盖。
配置引用未安装 ID、entry point 与 Manifest ID 不一致、SDK/API 不兼容或 digest 无效时，负责该扩展的
Worker 直接启动失败。目录扫描始终不执行扩展代码；API 进程也不会 import 扩展。

对 Host Extension，“安装”表示已验证并落盘精确 bundle/image；“准入”仍由部署允许列表与已发布
Remote Connection 决定；“生效”仍要求 Host preflight、连接 rollout 和 Capability Worker ACK。Console
不能上传任意 npm 包并立即执行，也不能扩大部署允许列表。

OpenCLI 按同一规则作为 `capability-opencli` Host Extension 发布。它自己的命令/插件扫描结果不能直接成为
active Capability：安装器先从固定 OpenCLI 版本和命令 Manifest 生成目录草稿，部署者选择允许的命令、
域名、浏览器 Profile 要求和审批等级，再冻结为新的 Capability Revision。生产环境禁止 OpenCLI 自动更新
插件、动态扫描任意 JS/TS 或通过 `external register` 暴露宿主机命令。当前 Pilot 固定 Node 24.19.0 LTS、
OpenCLI 1.8.6、npm/入口/Manifest/Extension/lockfile 摘要，并可用
`extensions/capability-opencli/dist/cli.js render-host-extension` 生成经重新校验的 Supervisor 配置片段。

## 4. 进程边界

| 扩展类型 | 加载进程 | 不会加载的进程 |
| --- | --- | --- |
| Provider | Agent Worker | API、Control、Scheduler、Migrator、Channel Worker |
| Capability / Connector | Agent Worker | API、Control、Scheduler、Migrator、Channel Worker |
| Channel | Channel Worker | API、Control、Scheduler、Migrator、Agent Worker |
| Host-managed Invocation Extension | 独立 Host child process/OCI；第一版由 Agent Worker 的现有 reconciliation 路径协调 | API、Control、Scheduler、Migrator |
| Host-managed Channel/Event Source | 独立 Host child process/OCI；Runtime 只接收标准 Channel/Trigger envelope | API、Control、Migrator、Agent Worker |

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

Host Extension 不创建平行发布状态机。Host 服务先以 Remote Connection Revision 完成身份、协议、密钥和
精确构建预热，随后它暴露的 Capability 再独立完成 `discovered → staged → Worker ACK → active`。
Channel/Event Source 的 Driver 发布同样必须绑定精确 bundle digest，并只由对应运行角色确认。Host 或
Extension 升级失败时旧版本继续生效。

`execution_isolation` 可为 `in_process`、`subprocess`、`container` 或 `mcp`。处理不可信二进制输入的
`capability-document-processing` 使用 `subprocess` 作为本地默认，并允许部署者选择 fail-closed 的无网络
`container` 后端；它只能读取当前 Run 已冻结的 Input Asset，输出只能是私有 Artifact。

以上字段是当前 Python Extension Manifest 的已实现取值。Node Host 不通过给该字段写入 `node` 来绕过
校验；它使用独立 Host Manifest，并通过 Remote Connection/Capability 精确身份与 Runtime 关联。

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
正式发布使用 `./scripts/release-candidate-check.sh`：该命令额外要求工作区无改动，从精确 Git commit 构建
wheel 并验证 Compose 插值；它不能替代 staging 的 Eval、故障演练和备份恢复验收。

Host 实现完成后，Node/异构 Extension 的发布检查还必须验证 bundle/image 签名、lockfile digest、SBOM、
SDK/Host protocol 兼容性、进程/容器隔离、重启恢复、stale lease fencing、密钥脱敏和独立回滚。通过这些
门槛前，不能把第三方 npm 安装入口开放到 Console 或 Market。
