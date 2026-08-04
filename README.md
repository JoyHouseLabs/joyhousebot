# Joyhousebot Cloud

Joyhousebot 是面向多用户并发的分布式 Agent 云运行平台。它提供 FastAPI HTTP/SSE 网关、持久化 Run/Task 状态机、多 Agent DAG、独立 Worker/Scheduler/Channel Worker，并直接基于 PostgreSQL 协调执行，不依赖外部 Agent SDK。

系统没有 `tenant_id`。用户资源使用认证主体派生的 `user_id` 隔离，会话由 `user_id + agent_id + session_id` 唯一确定；Agent、Skill 和 Tool 是服务所有用户的平台级能力。

## 架构

```text
Browser / API Client
       │ HTTP + SSE
       ▼
  FastAPI replicas ─────────────┐
                                ▼
                           PostgreSQL
                 ┌──────────────┼──────────────┐
                 ▼              ▼              ▼
            Agent Worker    Scheduler     Channel Worker
```

- 公共协议只有 `/v1` HTTP + SSE，没有 WebSocket RPC。
- API 只提交和查询；模型与 Tool 只在 Worker 执行。
- PostgreSQL 是唯一的运行时事实源；没有 SQLite 运行时后端。
- Run Event、Log、Artifact、执行 Span 和模型调用可审计、可恢复、可回放。
- 普通用户只接收结构化进度；具备专门权限的诊断台可查看完整模型请求/响应，以及供应商实际返回的原始推理块。
- `/mcp/` 是 MCP Streamable HTTP 适配层；MCP 工具调用仍会创建统一的持久化 Run/Task，不维护第二套执行运行时。

完整说明见 [架构文档](docs/ARCHITECTURE.md) 和 [开发计划](docs/DEVELOPMENT_PLAN.md)。

## 快速启动

已有本地 PostgreSQL 时，可以一条命令启动 API、Scheduler 和两个 Worker。脚本会优先读取
`LLM_API_KEY`，其次读取 `OPENROUTER_API_KEY`，最后只在内存中迁移旧
`~/.joyhousebot/config.json` 的 OpenRouter Key：

```bash
./scripts/start-local.sh
```

业务插件不写入核心包目录。以本机的 Dinq 插件为例，在 `config.json` 显式启用模块，并将其作为
editable package 安装到本地运行环境：

```json
{
  "tools": {
    "capability_plugins": ["dinq_plugin.discover.plugin"]
  }
}
```

```bash
export JOYHOUSEBOT_LOCAL_PLUGIN_PACKAGES='/Users/joyhouse/workspace/dinq-plugin'
./scripts/start-local.sh
dinq-plugin-seed
```

插件以进程镜像/依赖的一部分部署；`capability_plugins` 是明确的 allowlist，默认不扫描环境中其他
Python 包的 entry point。Dinq 人才 Capability 另需只读的 `DINQ_TALENT_DATABASE_URL`；内部用户检索
另需 `DINQ_INTERNAL_USER_SEARCH_URL`。

浏览器打开 `http://127.0.0.1:18790/ui/`，按 `Ctrl+C` 会关闭全部子进程。组件日志保存在
`~/.joyhousebot/logs/local/`。

使用 Docker Compose 启动整套依赖：

```bash
export LLM_PROVIDER='anthropic'
export LLM_API_KEY='your-key'
export POSTGRES_PASSWORD='choose-a-strong-password'
uv sync
docker compose -f docker-compose.runtime.yml up --build
```

Compose 默认挂载 `config.example.json`，其中显式开启 `allowInsecureAuth`，仅供本机开发。生产环境必须关闭该选项，并从控制台或 `/v1/admin/access-tokens` 签发数据库哈希令牌。配置文件拒绝明文密钥；Provider、数据库和 Channel 凭据使用外部环境变量或 `env://VARIABLE` 引用。

本地直接运行可先执行 `cp config.example.json config.json`。`config.json` 已被 `.gitignore` 忽略，不要提交真实配置。

或连接已有 PostgreSQL，分别启动角色：

```bash
export JOYHOUSEBOT_DATABASE_URL='postgresql://joyhousebot:password@127.0.0.1:5432/joyhousebot'
export JOYHOUSEBOT_CONFIG_PATH="$PWD/config.json"
uv run joyhousebot check
uv run joyhousebot api --surface combined --port 18790
uv run joyhousebot worker
uv run joyhousebot scheduler
```

也可以给每个命令传入 `--config ./config.json`。显式配置路径不存在或仍包含旧版单机客户端字段时，进程会直接失败，不会静默使用其他配置。

API 文档：`http://127.0.0.1:18790/docs`；UI：`http://127.0.0.1:18790/ui/`。

Channel 连接器在控制台的“配置 → Channels”查看启用状态和运行边界；当前凭据仍由环境变量或 `env://VARIABLE` 引用提供，连接配置尚未迁移为数据库热加载。

前端开发：

```bash
cd frontend
npm install
npm run dev
```

监控台开发地址为 `http://localhost:5178/ui/overview`，Agent 试用地址为 `/ui/chat`；Vite 代理 `/v1`、`/healthz` 和 `/readyz` 到 18790。

## API 示例

```bash
curl -X POST http://127.0.0.1:18790/v1/runs \
  -H 'Content-Type: application/json' \
  -H 'X-User-ID: local-user' \
  -d '{"agent_id":"joy","session_id":"demo","input":{"content":"分析这个任务"}}'
```

生产环境应使用数据库签发的 Bearer Token；PostgreSQL 只保存令牌 SHA-256 指纹，明文仅在签发响应中出现一次。`X-User-ID` 仅在显式设置 `gateway.allowInsecureAuth=true` 的开发模式生效；没有有效令牌时默认拒绝认证（401）。

## 许可证

本项目采用 Apache License 2.0，可用于商业产品。使用和再分发时请保留许可证和版权声明，并遵守 Apache License 2.0 的专利与 NOTICE 条款。

## 开发验证

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check joyhousebot tests
cd frontend && npm run build
```

部署与故障排查见 [运行手册](docs/OPERATIONS.md)。
