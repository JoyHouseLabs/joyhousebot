# Joyhousebot 运行手册

## 进程角色

所有角色使用同一个镜像和 PostgreSQL，通过不同命令启动：

```bash
joyhousebot api --surface combined --config ./config.json --host 0.0.0.0 --port 18790
joyhousebot worker --config ./config.json
joyhousebot scheduler --config ./config.json
joyhousebot channel-worker --config ./config.json
```

- `api` 只接受 HTTP/SSE 请求，不运行模型。`--surface public|control|combined`
  可把公网数据面与私有管理面拆成独立进程；本地控制台试用使用 `combined`。
- `worker` 从 PostgreSQL claim Run/Task，可水平扩容。
- `scheduler` 推进调度、DAG 与超时恢复。
- `channel-worker` 独占外部通道连接并提交 Run，不运行模型。

本地已有 PostgreSQL 时可直接运行 `./scripts/start-local.sh`。它会安全解析统一环境变量或旧
OpenRouter 配置中的 Key，初始化数据库，启动一个 Combined API、一个 Scheduler 和默认两个
Worker。可通过 `JOYHOUSEBOT_LOCAL_WORKERS`、`JOYHOUSEBOT_LOCAL_PORT`、
`JOYHOUSEBOT_DATABASE_URL` 覆盖本地默认值；所有组件日志写入
`~/.joyhousebot/logs/local/<timestamp>/`。

## PostgreSQL 启动

```bash
export POSTGRES_PASSWORD='choose-a-strong-password'  # 必填，compose 不提供默认值
export JOYHOUSEBOT_METRICS_TOKEN='choose-a-scrape-token'  # 必填，/metrics 未配置 token 时 fail-closed
docker compose -f docker-compose.runtime.yml up --build
```

Compose 默认挂载生产安全基线 `config.example.json`（`allowInsecureAuth=false`）。
本机开发改用 `config.dev.json`（开启 insecure auth，仅限本机，不要对外暴露）：

```bash
export JOYHOUSEBOT_CONFIG_FILE=./config.dev.json
```

生产部署须设置
`JOYHOUSEBOT_CONFIG_FILE=/absolute/path/cloud.json`，所有角色会通过
`JOYHOUSEBOT_CONFIG_PATH=/app/config.json` 读取同一份只读配置。

生产角色还必须设置 `JOYHOUSEBOT_ENVIRONMENT=production`。此时默认拒绝 `combined` API、
`allow_insecure_auth=true`、通配 CORS，以及少于 32 字符的 Metrics/紧急控制 Token。只有本地开发可以
使用 Combined；生产必须分别启动 `--surface public` 和 `--surface control`。

Compose 把 API 拆成两个角色：`api` 以 `--surface public` 只承载公网数据面
（runs/sessions/schedules/MCP，18790）；`control` 以 `--surface control` 承载
`/v1/admin/*` 与控制台 UI，默认只绑定 `127.0.0.1:18791`，不要暴露公网，通过
SSH 隧道或内网访问。

启用外部 Channel 时，Channel Worker 还必须显式注入对应的 Channel 凭据环境变量；不要把
Token、App Secret、SMTP 密码写入 JSON。当前 Channel 配置由进程启动时读取，修改凭据后需要重启
Channel Worker；控制台的 Channels 页面当前提供安全状态查看，数据库化热加载属于后续计划。

本地直接运行时设置（15432 端口仅绑定 127.0.0.1）：

```bash
export JOYHOUSEBOT_DATABASE_URL='postgresql://joyhousebot:joyhousebot-dev@127.0.0.1:15432/joyhousebot'
export LLM_PROVIDER='anthropic'
export LLM_API_KEY='your-key'
joyhousebot check --config ./config.json
```

`LLM_PROVIDER` + `LLM_API_KEY` 是单供应商本地部署的统一入口，可选
`LLM_API_BASE` 覆盖服务地址。`LLM_PROVIDER` 缺省为 `anthropic`；一旦显式设置，
它也是模型请求的首选路由，所以 `openrouter` 可以承载 `anthropic/...`、`openai/...`
等模型 ID，不会被其他供应商环境变量抢占。需要在同一 Worker 按模型前缀直连多个
供应商时，不设置统一变量，改用 `ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、
`OPENROUTER_API_KEY` 等专用变量。同一供应商的专用变量优先于 `LLM_API_KEY`。

所有环境必须配置 `JOYHOUSEBOT_DATABASE_URL`；本地测试也使用 PostgreSQL。

## 健康检查

- `GET /healthz`：API 进程存活。
- `GET /readyz`：数据库可用且 schema 就绪（仅返回 `{"ok": ...}`）。
- `GET /v1/system/health`：需认证的详细存储健康信息。
- `GET /v1/me`：验证 Bearer Token 绑定的 `user_id`。
- `GET /v1/admin/overview`：验证数据库管理员权限和平台全局监控面。
- `GET /metrics`：Prometheus 文本格式指标；包含 Run/Task/Worker、Provider 平均/P95 延迟、TTFT、费用、队列年龄、租约过期、重试和 Channel outbox 聚合。采集结果按进程缓存 5 秒，避免高频抓取放大 PostgreSQL 查询压力。
  数据库暂时不可用时仍返回 `joyhousebot_up 0` 和 HTTP 503，便于区分进程存活与数据面就绪。
  该端点 fail-closed：未设置 `JOYHOUSEBOT_METRICS_TOKEN` 时一律返回 404；设置后必须携带
  `Authorization: Bearer <token>`，否则返回 401。`ops/prometheus/prometheus.yml` 已按
  `credentials_file` 方式配置 bearer 抓取。
- `GET /v1/system/metrics`：管理员控制台使用的同源 JSON 指标。

Grafana 可直接导入 `ops/grafana/joyhousebot-overview.json`；Prometheus 抓取示例位于
`ops/prometheus/prometheus.yml`，告警规则位于 `ops/prometheus/joyhousebot-alerts.yml`。规则覆盖 API、
队列年龄、租约、重试、Worker、Provider 和 Channel outbox。

开发模式（显式 `gateway.allowInsecureAuth=true`）下，UI 默认使用 `local-dev` 并通过 `X-User-ID` 发送。
API 首次启动会把它写入 `platform_admins`，标记为测试管理员。其他开发 `user_id` 不会自动获得权限。
生产令牌由 `/v1/admin/access-tokens` 签发，数据库只保存哈希；管理员身份和细粒度权限由
`/v1/admin/users/{user_id}` 管理。令牌本身还有独立 scope（如 `runs.read`、`runs.write`、
`admin.read`、`admin.write`、`mcp.invoke`），用于收窄账号权限。服务令牌禁止 `*` scope 且必须过期；
管理 API 默认签发 90 天有效、60 天轮换提醒的用户令牌。`/v1/admin/access-token-events` 提供签发/吊销
审计。紧急 operator 凭据只能通过进程环境变量 `JOYHOUSEBOT_CONTROL_TOKEN` 注入，不能写进 JSON 配置。

配置文件中的 `apiKey`、`token`、`password`、`databaseUrl` 等敏感字段不接受明文；应留空并由标准环境变量注入，或写成 `env://VARIABLE`。启动时引用的环境变量不存在会直接失败。

首次生产引导使用一次性紧急 operator token：

```bash
export JOYHOUSEBOT_CONTROL_TOKEN="$(openssl rand -base64 36)"

curl -X PUT http://127.0.0.1:18790/v1/admin/users/platform-admin \
  -H "Authorization: Bearer $JOYHOUSEBOT_CONTROL_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"role":"admin","permissions":["*"],"enabled":true}'

curl -X POST http://127.0.0.1:18790/v1/admin/access-tokens \
  -H "Authorization: Bearer $JOYHOUSEBOT_CONTROL_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"platform-admin","label":"initial-admin"}'
```

第二个响应中的 `token` 只出现一次。保存后从部署环境移除 `JOYHOUSEBOT_CONTROL_TOKEN` 并滚动
重启 API；日常操作改用数据库 Token。不要把上面变量或响应写进仓库、Shell history 或日志。

## 扩容与故障恢复

API 与 Worker 可独立扩容。Worker claim 使用 lease 和 fencing；实例退出后，过期工作可由其他 Worker 接管。SSE 客户端使用事件 sequence 断点续传，刷新页面不会取消 Run。

Run/Task、提交配额和 API 限流均由 PostgreSQL 原子协调，不要求 Redis。只有在实时广播或
热点缓存经压测成为瓶颈时才部署可选 Redis；Redis 故障不得影响已提交任务的恢复。

## 诊断数据与回放

运行中心可查看模型调用、原始推理、Span 瀑布、完整请求/响应和回放结果。普通平台只读权限不足以
读取敏感正文；管理员需显式具备：

- `reasoning.read`：查看供应商返回的推理段；
- `reasoning.read_raw`：读取完整 Prompt、模型响应和错误 Trace Blob；
- `replay.execute`：创建 offline、frozen、branch 或 live 回放。

原始读取和回放都会写 runtime audit log。开发模式引导的 `local-dev` 管理员拥有 `*`，便于联调；
生产不得照搬该授权。Trace 表包含用户输入、上下文、Tool 数据和供应商推理，应使用数据库 TLS、磁盘/
备份加密、最小权限与独立保留策略。`purge_old_runtime_data` 会一起清理模型缓存、推理、Span、Blob、
回放、Event、Log 和 Request Trace；执行前应先按合规要求归档。

需要复现问题时优先使用 `offline`/`frozen`，它们不再次访问外部系统；只有确认允许产生新的模型成本、
Tool 副作用和外部流量后，才使用 `branch`/`live`。供应商没有在 API 响应中返回的服务端内部状态无法
被平台采集，诊断台会明确显示 `unavailable`。

HTTP 客户端需要短等待时可发送 `Prefer: wait=20`；最大值为 30 秒。后台任务应发送
`execution_mode=background` 并通过 Run 查询或 SSE 获取进度。

## Eval 与生产验收

内置业务 Eval 安装和执行：

```bash
joyhousebot eval-bootstrap --config ./config.json
joyhousebot eval-execute evalrun_<id> --config ./config.json
```

三套数据集覆盖证据研究、受治理执行和可发布作品。候选 Agent draft 只能在目标 ID、revision 与 active
Eval run 完全匹配时执行；所用 revision 会冻结到 Run snapshot，旧发布版本的成绩不能冒充候选版本。

数据库协调演练会写入带唯一 `drill:*` 用户的合成 Run/Task，默认完成后精确清理：

```bash
joyhousebot durability-drill \
  --confirm WRITE_SYNTHETIC_RUNTIME_DATA \
  --tasks 500 --claim-concurrency 16 \
  --config ./config.json
```

API 规模演练必须使用仅有 `runs.read`、`runs.write` 的专用服务令牌，令牌从环境读取，永不进入参数或报告：

```bash
export JOYHOUSEBOT_LOAD_TOKEN='one-time-scoped-service-token'
joyhousebot load-test --base-url https://api.example.com \
  --count 200 --concurrency 16 --wait
unset JOYHOUSEBOT_LOAD_TOKEN
```

两种命令都会把机器可读 JSON 写入 `artifacts/drills/` 并以退出码表示 PASS/FAIL。完整分阶段矩阵、SLO 和
故障注入步骤见 [生产验收手册](PRODUCTION_ACCEPTANCE.md)。

出现 `Too many open files` 时：

1. 确认启动的是当前 `joyhousebot api`，而不是旧 WebSocket Gateway 或旧安装目录。
2. 使用 `lsof -p <pid>` 按类型统计句柄；PG 连接池会在角色关闭时统一释放，Agent 也不再启动 knowledge watcher/subprocess。
3. 检查是否意外把 Channel 角色与 API 混合启动，以及第三方 SDK 是否反复重连。
4. 开发机可检查 `ulimit -n`，但提升限制不能替代定位泄漏。

命令执行默认依赖 Docker 沙箱；Docker 不可用时工具返回错误，不会在 API/Worker 宿主机执行。
