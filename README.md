# Joyhousebot

## 个人数据与智能的云端/本地执行体

JoyhouseBot 是开源、可本地部署的长期任务执行引擎，为面向 OPC 与个人成长的 Joyhouse 提供可靠执行。
它把自然语言目标转化为持久 Run/Task，经过能力准入、定时或事件唤醒、长任务恢复、人工确认、验证、
审计和回放，最终形成可积累的 Artifact 与 Work。

> OPC 是目标用户，长期持续任务是产品机制，成果与收入增长是用户价值，JoyhouseBot 是底层执行引擎。

普通用户使用 Joyhouse 管理目标、持续任务、待确认事项和成果，不需要理解 Run、Graph、Agent 或
Capability；扩展作者和自部署用户才直接使用 JoyhouseBot。完整产品定位见
[Joyhouse OPC 产品定位](docs/PRODUCT_OPC.md)。

Core 与供应商技术扩展已经按独立制品拆分，业务 App 保持独立，完整边界和结果见
[非 Core 功能拆分台账](docs/NON_CORE_MIGRATION.md)。

App 是可以拥有独立用户、计费、业务数据库并单独售卖的业务产品；Skill 是版本化方法资产；Extension
只表示 Runtime 的技术安装制品。三者不能混用，完整协作协议见
[独立 App 与 JoyhouseBot 协作契约](docs/APP_INTEGRATION.md)。

JoyHouse Market 是独立仓库和独立部署的账号、生态目录与商业分发产品，负责作者身份、签名制品、
商业授权、更新和生态治理。Market 不读取个人运行数据，也不绕过本地安装与权限审批；JoyhouseBot
Core 不依赖官方 Market，第三方 Registry 仍可实现开放协议。目标协议见
[App Market 治理与跨实例分发协议](docs/APP_MARKET_GOVERNANCE.md)。

企业业务程序不作为 Python Extension 加载进 Runtime。它们保持自己的界面、身份、权限、数据库和事务，
通过通用的签名 HTTP Capability Connector 接入统一执行链，协议见
[远程 Capability 协议](docs/REMOTE_CAPABILITY_PROTOCOL.md)。

同一套 Runtime 可以本地一体化运行，也可以部署为云端多用户并发服务。资源始终以 `user_id` 归属，不预设企业租户模型；个人数据默认私有，只有用户主动发布的 Skill、Agent、Workflow 或 Work 才能被其他人复用和派生。

## 项目结构

```text
joyhousebot/
├── joyhousebot/              # 开源 Runtime：API、Worker、Agent、任务、工具、存储与权限
├── apps/
│   └── console/              # 运行监控、Agent 配置、场景与回放控制台
├── extensions/               # 可独立安装、发现和启用的官方扩展
├── docs/                     # 架构、部署、CLI 与集成文档
├── deploy/                   # Runtime 与控制台部署模板
└── tests/                    # Runtime 契约与集成测试
```

面向用户的 JoyHouse 产品位于相邻私有项目 `../joyhouse`；官方市场位于 `../joyhouse-market`。两者都不打包进开源 Runtime。

## 治理模型

```text
用户 / API Client
      │ 身份、权限、配额、审计
      ▼
  Agent 应用
      │ 版本、场景、追问 DAG、记忆策略
      ▼
能力目录 ─ Skills / Tools / MCP / Channels / Providers
      │ allowlist、策略、沙箱、健康检查
      ▼
运行时 ─ Run / Task / Event / Trace / Artifact / Replay
      ▼
PostgreSQL 事实源
```

治理是每次执行的默认路径：请求先经过认证和权限校验，能力必须来自已发布目录，执行过程写入可续传事件与诊断数据，结果与成本、耗时、错误和产物一起归档。

## 核心能力

### Agent、场景与版本治理

- Agent、Skill、Tool、Scenario 和 MCP Server 使用可审计的版本目录管理。
- 草稿 → 发布 → Worker 加载确认 → 生效切换是明确的 rollout 状态机；失败发布不会覆盖旧版本。
- 场景支持意图路由、字段校验、单选/多选/Other 交互输入、条件追问 DAG、能力绑定和执行策略，可在控制台模拟和发布。
- 主协调器可以路由到固定场景、在缺少必要信息时生成受控的动态追问，或创建并行 Task Graph；业务应用不需要硬编码进核心运行时。
- AI Workflow Studio 允许用户直接描述目标，由 Agent 生成可执行 DAG；流程可视化审查、自然语言修改、
  试运行和版本发布。Workflow 可组合单 Agent、冻结 AgentTeam、固定 Scenario、验证、分支、有界循环与
  人工审批；Team/Scenario 作为可恢复子 Run 执行，仍编译到统一 TaskGraph/Run 链路，不维护第二套引擎。

### 能力与安全治理

- 独立 Skill Registry 管理声明式方法资产、不可变版本、依赖和 Eval；Capability Registry 管理 Tool、Connector 和 MCP 动作，调用前执行 allowlist、权限、配额和参数校验。
- Shell 是显式安装的官方扩展，只允许经 Core 隔离 Docker 容器执行；容器不可用时失败关闭，不降级到宿主机。
- 外部 MCP Server 通过独立 MCP Client Connector 安装；HTTP 强制 SSRF 防护，stdio 默认关闭，远端 Tool 不冒充 Core 能力。
- File、Memory、Knowledge、Artifact 按 `user_id + agent_id + root_run_id` 隔离；Worker 本地磁盘不是共享事实源。
- Provider、数据库、Channel 和外部服务凭据只通过环境变量或 `env://VARIABLE` 引用，禁止明文进入配置和日志。

### 可观测、审计与可解释性

每个请求形成可续传 Run 时间线，串联父子 Run、Task、Worker、模型调用、Tool Invocation、日志、Span 和产物。控制台可以定位队列等待、claim 延迟、首 Token、工具耗时、Token、成本、重试、缓存命中、错误、场景路由和子 Agent。

诊断面可以查看供应商实际返回的 reasoning/thinking 块。平台区分 `provider_native/exact`、`model_declared/normalized`、`runtime_decision` 和 `unavailable`，不会把模型未暴露的隐藏状态伪装成思维链。原始请求、响应和推理 Blob 按权限读取，读取行为也写入审计日志。

### 回放与持续改进

支持 offline、frozen、branch 和 live 回放，用于故障分析、结果比较和受控重试。版本化 Eval dataset、
自动执行器、确定性 scorer 与精确 Agent/Scenario/Capability 发布门禁保证未通过回归的 revision 不会激活。
Agent Eval 可以在发布前执行精确 draft revision，冻结快照并保存源 Run、事件、验证、产物、成本和延迟证据；
仓库内置证据研究、受治理执行、可发布作品三套业务基准。模型缓存只复用等价请求，仍然保留 Invocation、
Span 和审计记录。

### 从产物到成果作品

Run Artifact 可进入 Work 的不可变版本链。所有者可以显式选择 private、unlisted 或 public，管理数据
分级、发布版本、协作者、可撤销/可过期的固定版本分享链接和访问审计；生成产物不会自动公开个人数据。

### 分布式执行

```text
客户端 ── HTTP / SSE ──▶ FastAPI API 副本
                              │
                              ▼
                         PostgreSQL
                    ┌─────────┼─────────┐
                    ▼         ▼         ▼
              Agent Worker  Scheduler  Channel Worker
```

- PostgreSQL 是唯一运行时事实源；SQLite 不支持。
- Worker 使用 lease、fencing version、`FOR UPDATE SKIP LOCKED` 和 PostgreSQL `LISTEN/NOTIFY`，不依赖进程内队列。
- API 负责认证、提交和查询；模型与工具只在 Worker 执行。
- 同一会话的顶层 Run 串行，不同用户、会话和子任务可以并发。
- Redis 不是必需依赖；如引入，只能作为缓存或唤醒加速层，不能替代 PostgreSQL 状态机。

### 统一执行入口

公共协议是版本化 HTTP + SSE。聊天、定时任务、签名 Webhook、Channel 入站、版本化 AgentTeam、
多 Agent DAG 和 MCP
`tools/call` 都进入同一套 Run/Task 链路，不维护第二套 RPC 或 MCP 执行引擎。自动化中心可以管理
Schedule 的启停、补跑和触发历史，也可以把带 Secret、Event Type 与 Idempotency-Key 的外部事件
可靠映射为用户自己的 Agent Run。

## 身份与权限

当前核心模型不引入 `tenant_id`：资源归属由认证主体 `user_id` 表达，会话边界是 `user_id + agent_id + session_id`；Agent、Skill、Tool 和子 Agent 是平台共享能力。平台管理员存放在独立的 `platform_admins` 表中，与普通用户身份分离。

控制台支持管理员密码和 Google Authenticator/TOTP 登录：密码使用 Scrypt 加盐哈希，浏览器只持有短期
会话 Token，数据库保存 Token 指纹；TOTP 密钥由独立部署密钥加密，恢复码仅在激活时显示一次。生产环境
没有代码内置固定密码，首次管理员由环境变量引导并强制改密。本地 `config.dev.json` 的默认管理员为
`joyhousebot / joyhousebot`，仅用于回环地址开发，首次登录后必须立即改密。

登录控制台时可另外选择本次操作的个人 `user_id`。管理员账号负责认证和平台权限，操作 `user_id` 负责
个人 Run、记忆、自动化与成果归属；控制面接口不会随操作用户切换。代操作需要
`users.impersonate` 权限并由界面持续提示，普通 API Token 始终只能访问自身资源。

自动化与业务客户端继续使用数据库签发的 Bearer API Token；`X-User-ID` 仅在显式开发模式生效。账号
RBAC 与令牌 scope 分层校验，服务令牌必须使用最小 scope 和有效期；签发、使用时间、轮换期限、吊销人
及事件均可审计。权限按操作拆分，例如 `runs.read`、`runs.cancel`、`agents.publish`、
`reasoning.read_raw` 和 `replay.execute`，管理操作全部产生审计事件。

## 代码边界与业务集成

```text
api / bootstrap / channel adapters
                ↓
            application
                ↓
       runtime + domain services
                ↓
       module-owned PostgreSQL repositories
```

业务 App（例如 Dinq Discover）应保留自己的界面、用户、计费、领域服务和数据库，通过 HTTP/SSE 与
Remote Capability 协议使用 JoyhouseBot。只有 Provider、Channel、Connector 和原子 Capability 等技术
能力才通过 Extension 安装，不把业务代码写入 `joyhousebot` 核心包。Core 与扩展的判定、依赖方向和迁移规则见
[Core 与扩展包边界设计](docs/CORE_AND_EXTENSIONS.md)、
[AgentTeam 协作协议](docs/AGENT_TEAMS.md)、
[Run 顶层执行模式](docs/EXECUTION_MODES.md)。Smart Study 保持独立项目，不作为默认集成。

第一阶段 Runtime、JoyHouse Product、Market 和官方 App 共用一个 PostgreSQL database 和
`JOYHOUSE_DATABASE_URL`，通过表所有权与独立迁移链保持模块边界；Skill 不拥有业务表，Extension 不得向
Core 注入自定义 DDL。完整规范见
[数据库与生态扩展边界](docs/DATA_AND_ECOSYSTEM_BOUNDARIES.md)。

## 控制台示例

Joyhousebot 自带用于试用、运维和问题定位的管理控制台：

- **运行监控**：查看 API、PostgreSQL、Worker 集群、运行数量和资源用量。
- **运行中心**：分页查看 Run，按状态、Agent、Session 或摘要筛选，并进入独立详情页。
- **全流程回放**：查看事件时间线、模型调用、工具调用、HTTP Trace、日志、产物、人工反馈和输入输出。
- **Eval 与成果**：维护回归门禁，并把已验证 Artifact 形成可版本化、可分享、可撤销的作品。

![运行监控概览](docs/pictures/ScreenShot_2026-08-05_230508_778.png)

![运行中心](docs/pictures/ScreenShot_2026-08-05_230608_764.png)

![Run 详情与执行时间线](docs/pictures/ScreenShot_2026-08-05_230625_459.png)

## JoyHouse 产品接入

JoyHouse Desktop、Web、Mobile、官网与浏览器扩展属于独立闭源产品仓库。它们通过版本化 HTTP/SSE、
App SDK 与 Market 协议使用 JoyhouseBot，不复制 Run/Task 状态机，也不让产品身份、计费和同步逻辑进入
开源 Core。产品与 Runtime 的完整边界维护在相邻项目 `../joyhouse/docs/ARCHITECTURE.md`。

## 快速启动

本地需要 PostgreSQL：

```bash
cp config.dev.json config.json
export LLM_PROVIDER="openrouter"
export LLM_API_KEY="your-key"
export LLM_MODEL="openrouter/openai/gpt-4.1-mini"
export JOYHOUSE_DATABASE_URL="postgresql://joyhouse:password@127.0.0.1:5432/joyhouse"
./scripts/start-local.sh
```

`config.dev.json` 开启了 `allowInsecureAuth`（仅凭 `X-User-ID` 头即可认证），仅限本机开发，不要用于任何可对外访问的部署；`config.example.json` 是生产安全基线模板。

Core 默认不安装或启动任何渠道。个人/OPC 推荐先安装 Email 官方扩展：

```bash
uv pip install -e extensions/channel-email
```

然后把 `channel-email` 加入部署配置的 `extensions.allowedIds`，执行 `joyhousebot
discover-extensions`，再到 Console“扩展中心”激活。IMAP/SMTP 凭据仍只使用
`env://VARIABLE` 引用。其他渠道按需安装，不属于默认产品组合。

独立企业程序通过唯一的通用远程业务连接器接入：

```bash
uv pip install -e extensions/connector-http-capability
```

把 `connector-http-capability` 加入 `extensions.allowedIds`，发现并在扩展中心激活；随后通过 Console
“集成中心 → 远程能力”管理 PostgreSQL 中的连接 Revision、`env://` 密钥引用、能力目录、逐 Worker
预热和回滚。所有写操作必须回传 Runtime 冻结的 `action_id/idempotency_key`，异步操作必须支持查询
和对账。

图片与视频生成也是可卸载扩展，不进入 Core：

```bash
uv pip install -e extensions/capability-media-generation
uv run joyhousebot discover-extensions --config config.json
```

启用 `capability-media-generation` 后，可在 Console 的扩展详情中配置 `image.generate`、
`image.edit` 和 `video.generate` 的默认供应商与模型。扩展内置火山方舟 Seedream/Seedance 和即梦
适配器；密钥仅从 Worker 环境读取。完整安装、审批、异步对账与临时媒体 URL 说明见
[`extensions/capability-media-generation/README.md`](extensions/capability-media-generation/README.md)。
目录发现只把已安装、已启用扩展的不可变 Manifest、组件和 Capability Draft 写入 PostgreSQL，
不会启动 Agent、调用模型或执行工具；Extension 与 Capability 真正生效仍需健康 Worker 预热并 ACK。

文本 LLM 的运行参数在 Console“集成中心 → Models”统一管理：Provider Endpoint、API Key 环境变量引用、
附加 Header 引用、请求超时和模型目录进入 PostgreSQL 不可变 Revision，经 Agent Worker 逐机预热后生效。
密钥值仍只存在于 Worker 环境；Agent Studio 从已生效模型目录选择 Primary/Fallback Model，并把
Temperature、Max Tokens、推理预算和缓存策略冻结进 Agent Revision。

打开 `http://127.0.0.1:18790/ui/`；OpenAPI 在 `/docs`，健康检查为 `/healthz` 和 `/readyz`。`config.json` 已被 Git 忽略，真实配置和密钥不要提交。

Docker Compose：

```bash
export LLM_PROVIDER="openrouter"
export LLM_API_KEY="your-key"
export LLM_MODEL="openrouter/openai/gpt-4.1-mini"
export POSTGRES_PASSWORD="choose-a-strong-password"
export JOYHOUSEBOT_METRICS_TOKEN="choose-a-scrape-token"
uv sync
docker compose -f docker-compose.runtime.yml up --build
```

Compose 起两个 API 角色：`api`（公网数据面，18790）和 `control`（管理面与控制台 UI，默认只绑 `127.0.0.1:18791`，不要暴露公网）。

详细部署和故障排查见 [运行手册](docs/OPERATIONS.md)，完整边界和数据模型见 [架构文档](docs/ARCHITECTURE.md)。

## API 示例

```bash
curl -X POST http://127.0.0.1:18790/v1/runs \
  -H 'Content-Type: application/json' \
  -H 'X-User-ID: joyhousebot' \
  -d '{"execution":{"mode":"agent","agent_id":"default"},"session_id":"demo","input":{"content":"分析这个任务"}}'
```

生产请求应使用数据库签发的 Bearer Token。Agent、Team、Scenario 和 Workflow 的显式入口见
[Run 顶层执行模式](docs/EXECUTION_MODES.md)。

## 验证

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check joyhousebot tests extensions/*/src
cd apps/console && npm run build
```

## 许可证

本项目采用 Apache License 2.0，可用于商业产品。再分发时请保留许可证、版权声明，并遵守 Apache 2.0 的专利与 NOTICE 条款。
