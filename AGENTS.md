# JoyhouseBot 项目协作说明

本文件适用于整个 `joyhousebot` 仓库。进入子目录后，如存在更近的 `AGENTS.md`，以更近文件的补充规则为准。

## 1. 项目定位

JoyhouseBot 是面向个人数据与智能的开源云端/本地执行体：帮助每个人把自然语言目标转化为可追踪、可恢复、可验证的执行，并把数据、经验、技能和成果沉淀为属于用户的长期资产。多用户并发是 Runtime 能力，但当前产品首先解决个人问题，不预设企业租户模型，也不引入 `tenant_id`。核心不是单一聊天客户端或模型 SDK，而是 PostgreSQL-first 的 Agent Runtime 与控制面：能力准入、版本发布、多 Agent 协作、长任务、人工反馈、审计、回放和持续优化都进入统一 Run/Task 链路。

个人数据、记忆、会话和执行产物默认私有。用户主动分享时，分享对象应是脱离私有上下文、经过版本化和验证的 Skill、Agent、Workflow 或 Work；其他用户可以复用或派生发布物，但不能因此获得发布者的个人数据。

业务项目应通过独立插件包注册 Scenario、Capability、Tool、Skill 或 MCP Server；不要把某个业务项目的路由、页面、数据模型或硬编码流程写入 `joyhousebot` 核心包。

## 2. 目录职责与边界

- `joyhousebot/`：核心 Python Runtime。包括 API、Worker、Agent、场景编排、能力目录、权限、存储、渠道、任务、审计和回放。它是可复用框架，不承载具体业务应用代码。
- `joyhousebot/api/`、`application/`、`domain/`、`runtime/`、`storage/`：遵循 API/适配层 → application → runtime/domain services → PostgreSQL repositories 的单向边界。API 负责认证、提交与查询；模型和工具只在 Worker 执行。
- `apps/console/`：Vue/Vite 管理控制台，用于运行监控、Agent 配置、能力目录、场景、执行时间线和回放。它只调用版本化 HTTP/SSE API，不直接读写数据库或实现运行时逻辑。
- `apps/joyclaw/`：面向个人用户的极简执行入口，用于提交自然语言目标、查看需要关注的执行、浏览成果和个人自动化。它复用 JoyhouseBot 公共 API，不复制 Agent 配置、运行状态机或治理逻辑；高级设置必须链接到 `apps/console/`。
- `apps/website/`：Nuxt 静态官网，用于介绍 JoyhouseBot、文档入口、下载和产品引导；不承载 Runtime 控制台或登录后业务流程。
- `apps/browser-extension/`：浏览器智能外挂，独立 Git 仓库的子模块（`JoyHouseLabs/ext-joyhousebot`）。修改扩展应在子模块仓库中提交、发布，再在此仓库更新固定 commit；不要把扩展源码复制进 Runtime 仓库。
- `bridges/`：独立渠道桥接实现。新增渠道优先保持为适配层，通过公共执行入口接入，不得创建第二套 Run/Task 状态机。
- `docs/`：架构、部署、CLI、集成与运维文档。涉及公共协议、权限、存储、执行模型或发布流程的重大改动必须同步更新文档。
- `deploy/`、`ops/`：Runtime、控制台和官网的部署模板、nginx/systemd/监控配置。线上真实配置优先于示例文件；发布前必须核对目标主机实际配置。
- `scripts/`：本地启动、构建、预发布检查和打包脚本；调整目录、构建产物或启动方式时必须同步更新。
- `tests/`：Runtime 契约与集成测试。涉及 PostgreSQL 的测试使用显式测试数据库，不能对开发或生产数据库运行。

## 3. 运行时不变量

- PostgreSQL 是唯一运行时事实源；不支持 SQLite。Redis 如被使用，只能做缓存或唤醒加速，不能取代 Run/Task 状态机。
- 公共执行入口是版本化 HTTP + SSE。聊天、定时任务、渠道入站、多 Agent DAG 和 MCP `tools/call` 必须进入同一 Run/Task/Event/Trace 链路。
- Agent、Skill、Tool、Scenario、MCP Server 的版本发布必须可审计。草稿 → 发布 → Worker 加载确认 → 生效切换是明确状态机；失败发布不得覆盖已生效版本。
- 能力调用必须经过 Capability Registry 的 allowlist、权限、配额和参数校验。Shell 只能在隔离容器中运行；容器不可用时失败关闭，不能降级到宿主机。
- 外部业务插件必须收到 Runtime 冻结的 `action_id/idempotency_key`，并把同一键传给业务写接口；插件不得自行生成进程内幂等身份。Tool 与 Connector 都走同一 Dispatcher、审批、对账和审计链。
- 业务输出先形成 Artifact；成为可分享成果时必须进入 Work 的不可变版本、分级、发布、分享链接、撤销和审计链，不能把原始私有 Artifact URL 直接当公开链接。
- API 仅处理认证、提交和查询；LLM、工具和长任务由 Worker 执行。不得把模型/工具调用重新塞回 HTTP 请求线程。
- 数据和产物按 `user_id + agent_id + root_run_id` 隔离。密钥只通过环境变量或 `env://VARIABLE` 引用，禁止提交、打印或写入日志。
- 管理员密码只能保存为自带随机盐的慢哈希；浏览器会话、MFA challenge、API Token 和恢复码只能保存指纹。TOTP shared secret 必须由独立环境密钥加密。生产环境禁止源码默认密码，bootstrap 密码只允许首次引导并强制改密。
- 不能将供应商未暴露的内部推理伪装成思维链；推理原文、请求与响应 Blob 均按权限读取并产生审计事件。

## 4. 本地开发与验证

运行 Runtime 前需要 PostgreSQL，并使用 `config.dev.json` 创建本地 `config.json`。`allowInsecureAuth` 与 `X-User-ID` 仅限本机开发，不能用于可访问公网的环境。

```bash
uv sync
./scripts/start-local.sh

# Runtime 测试与静态检查
.venv/bin/python -m pytest
.venv/bin/ruff check joyhousebot tests

# 控制台
cd apps/console && npm install && npm run build

# JoyClaw 个人入口
cd apps/joyclaw && npm install && npm run build

# 官网静态生成
cd apps/website && pnpm install && pnpm generate
```

涉及 PostgreSQL 集成测试时，使用项目约定的测试库和 `scripts/test-postgres.sh`；不得指向未确认的数据库。修改 Runtime、权限、任务、存储或 API 契约时，必须至少运行相关测试与 Ruff；修改 Web 应用时必须运行对应构建。

## 5. 前后端与扩展约定

- 控制台的 API 类型、请求和错误适配集中在 `apps/console/src/api/`；不要从 Vue 组件直接调用数据库、绕开 HTTP 客户端，或复制 Runtime 业务规则。
- Runtime 内置 UI 静态产物由 `scripts/build-ui.sh` 生成/同步；不要手工编辑构建后的 `joyhousebot/static/ui/` 文件。
- 官网通过 `pnpm generate` 生成静态内容。官网文案、下载链接与公开产品承诺必须与实际开源项目和发布版本一致。
- 浏览器扩展使用子模块方式引用。更新前检查子模块状态；发布扩展包、许可证与安装说明在扩展仓库内维护。
- 公共 API 与事件字段应保持向后兼容。必须破坏兼容性时，先设计版本迁移、文档和回滚方案，再修改实现。

## 6. 发布与安全纪律

用户明确要求发布时，按以下顺序执行：

1. 检查主仓库与子模块状态，保留所有用户未提交改动。
2. 运行相关 Python 测试、Ruff、控制台构建和官网静态生成。
3. 执行 `scripts/pre_release_check.sh`；必要时使用 `scripts/build-and-package.sh` 生成可校验发布包。
4. 只读核对目标环境的 PostgreSQL、服务、nginx/反向代理和当前版本，再备份将被替换的配置/产物和数据库。
5. 先发布并健康检查 Runtime 与 Worker，再切换控制台或官网静态产物；验证 `/healthz`、`/readyz`、核心 Run 路径和服务日志。

不要提交 `.env`、`config.json`、Bearer Token、LLM API key、数据库 URL、用户数据、构建缓存或本地数据库。不要执行 `git reset --hard`、`git checkout --` 或清理整个工作区；仓库可能包含其他人未提交的修改。

## 7. 交付说明

完成任务时应明确说明：修改了哪些目录和边界、执行了哪些验证、是否已更新子模块或发布、是否触及 Runtime/API 兼容性，以及任何与本次无关的既有失败或风险。
