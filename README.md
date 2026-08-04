# Joyhousebot

## 企业 Agent 应用治理平台

Joyhousebot 不是单 Agent 客户端，也不是模型供应商 SDK。它解决企业把 Agent 应用投入真实业务后最难管理的问题：权限边界、能力准入、版本发布、并发执行、故障恢复、全流程审计、结果回放，以及成本和性能治理。

它提供一个 PostgreSQL-first 的控制面与运行面，让企业可以统一构建、发布、运行和治理多个 Agent 应用。

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
- 场景支持意图路由、字段校验、追问节点与边、能力绑定和执行策略，可在控制台模拟和发布。
- 主协调器可以路由到固定场景、生成追问或创建并行 Task Graph；业务应用不需要硬编码进核心运行时。

### 能力与安全治理

- Capability Registry 统一登记 Tool、Skill、Connector 和 MCP 能力，调用前执行 allowlist、权限、配额和参数校验。
- Shell 只允许在隔离 Docker 容器中执行；容器不可用时失败关闭，不降级到宿主机。
- File、Memory、Knowledge、Artifact 按 `user_id + agent_id + root_run_id` 隔离；Worker 本地磁盘不是共享事实源。
- Provider、数据库、Channel 和外部服务凭据只通过环境变量或 `env://VARIABLE` 引用，禁止明文进入配置和日志。

### 可观测、审计与可解释性

每个请求形成可续传 Run 时间线，串联父子 Run、Task、Worker、模型调用、Tool Invocation、日志、Span 和产物。控制台可以定位队列等待、claim 延迟、首 Token、工具耗时、Token、成本、重试、缓存命中、错误、场景路由和子 Agent。

诊断面可以查看供应商实际返回的 reasoning/thinking 块。平台区分 `provider_native/exact`、`model_declared/normalized`、`runtime_decision` 和 `unavailable`，不会把模型未暴露的隐藏状态伪装成思维链。原始请求、响应和推理 Blob 按权限读取，读取行为也写入审计日志。

### 回放与持续改进

支持 offline、frozen、branch 和 live 回放，用于故障分析、结果比较和受控重试。模型缓存只复用等价请求，仍然保留 Invocation、Span 和审计记录。

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

公共协议是版本化 HTTP + SSE。聊天、定时任务、Channel 入站、多 Agent DAG 和 MCP `tools/call` 都进入同一套 Run/Task 链路，不维护第二套 RPC 或 MCP 执行引擎。

## 身份与权限

当前核心模型不引入 `tenant_id`：资源归属由认证主体 `user_id` 表达，会话边界是 `user_id + agent_id + session_id`；Agent、Skill、Tool 和子 Agent 是平台共享能力。平台管理员存放在独立的 `platform_admins` 表中，与普通用户身份分离。

生产环境使用数据库签发的 Bearer Token，数据库只保存 SHA-256 指纹；`X-User-ID` 仅在显式开发模式生效。权限按操作拆分，例如 `runs.read`、`runs.cancel`、`agents.publish`、`reasoning.read_raw` 和 `replay.execute`，管理操作全部产生审计事件。

## 代码边界与业务集成

```text
api / bootstrap / channel adapters
                ↓
            application
                ↓
       runtime + domain services
                ↓
       dedicated PostgreSQL repositories
```

业务项目（例如 Dinq Discover）应通过独立插件包注册 Scenario、Capability、Tool、Skill 或 MCP Server，不把业务代码写入 `joyhousebot` 核心包。

## 快速启动

本地需要 PostgreSQL：

```bash
cp config.example.json config.json
export LLM_PROVIDER="openrouter"
export LLM_API_KEY="your-key"
export JOYHOUSEBOT_DATABASE_URL="postgresql://joyhousebot:password@127.0.0.1:5432/joyhousebot"
./scripts/start-local.sh
```

打开 `http://127.0.0.1:18790/ui/`；OpenAPI 在 `/docs`，健康检查为 `/healthz` 和 `/readyz`。`config.json` 已被 Git 忽略，真实配置和密钥不要提交。

Docker Compose：

```bash
export LLM_PROVIDER="openrouter"
export LLM_API_KEY="your-key"
export POSTGRES_PASSWORD="choose-a-strong-password"
uv sync
docker compose -f docker-compose.runtime.yml up --build
```

详细部署和故障排查见 [运行手册](docs/OPERATIONS.md)，完整边界和数据模型见 [架构文档](docs/ARCHITECTURE.md)。

## API 示例

```bash
curl -X POST http://127.0.0.1:18790/v1/runs \
  -H 'Content-Type: application/json' \
  -H 'X-User-ID: local-dev' \
  -d '{"agent_id":"main-coordinator","session_id":"demo","input":{"content":"分析这个任务"}}'
```

生产请求应使用数据库签发的 Bearer Token。

## 验证

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check joyhousebot tests
cd frontend && npm run build
```

## 许可证

本项目采用 Apache License 2.0，可用于商业产品。再分发时请保留许可证、版权声明，并遵守 Apache 2.0 的专利与 NOTICE 条款。
