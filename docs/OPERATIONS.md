# HappyHousebot 运行手册

## 进程角色

所有角色使用同一个镜像和 PostgreSQL，通过不同命令启动：

```bash
porthouse api --surface combined --config ./config.json --host 0.0.0.0 --port 18790
porthouse worker --config ./config.json
porthouse scheduler --config ./config.json
porthouse channel-worker --config ./config.json
```

生产启动前先由唯一迁移任务执行一次 `PORTHOUSE_AUTO_MIGRATE=true porthouse check`；随后所有
长运行角色设置 `PORTHOUSE_AUTO_MIGRATE=false`。Compose 已用一次性 `migrate` 服务和
`service_completed_successfully` 依赖固化这条顺序，避免多角色冷启动时 DDL 与目录初始化写入交叉。
已经写入 `schema_migration_history` 的迁移不可原地修改；checksum 不一致会让迁移任务失败关闭。
修复方式是恢复已发布 DDL 并新增 migration version，不能手工覆盖历史 checksum。

- `api` 只接受 HTTP/SSE 请求，不运行模型。`--surface public|control|combined`
  可把公网数据面与私有管理面拆成独立进程；本地控制台试用使用 `combined`。
- `worker` 从 PostgreSQL claim Run/Task，可水平扩容。
- `scheduler` 推进调度、DAG 与超时恢复。
- `channel-worker` 独占外部通道连接并提交 Run，不运行模型。

本地已有 PostgreSQL 时可直接运行 `./scripts/start-local.sh`。它会安全解析统一环境变量或旧
OpenRouter 配置中的 Key，初始化数据库，启动一个 Combined API、一个 Scheduler 和默认两个
Agent Worker，以及一个 Channel Worker。可通过 `PORTHOUSE_LOCAL_WORKERS`、`PORTHOUSE_LOCAL_PORT`、
`HAPPYHOUSE_DATABASE_URL` 覆盖本地默认值；Product、Market 和官方 App 使用相同连接。Porthouse 会把它映射为
自身的 `PORTHOUSE_DATABASE_URL`。所有组件日志写入
`~/.porthouse/logs/local/<timestamp>/`。

`runtime.store.blobDirectory` 可把超过 `blobInlineThresholdBytes`（默认 64 KiB）的 Trace/Artifact 正文移出
PostgreSQL 行。单机默认目录为 `~/.porthouse/runtime-blobs`；容器或多主机部署必须挂载持久化共享卷，且所有
读取这些 Run/Work 的 API、Worker 与 Scheduler 使用同一路径。备份和恢复必须把 PostgreSQL 快照与 Blob 目录
视为一个恢复点；只恢复数据库会保留 URI 但无法读取正文，只恢复目录则不会恢复运行状态。运行数据 purge 使用
两阶段、24 小时宽限的未引用对象回收；不要绕过 PostgreSQL 直接删除目录内容。

`runtime.store.inputAssetDirectory`（默认 `~/.porthouse/input-assets`）保存上传后冻结到 Run 的二进制
输入，`inputAssetMaxBytes` 默认 25 MiB。目录应为 `0700`，对象为 `0600`；多主机 API/Worker 必须使用共享、
持久且可备份的挂载或对象存储适配器。Runtime 保留清理不会触碰非终态 Run 的输入，终态引用过期后先软删除
数据库资产，再以 24 小时宽限回收无引用对象。不要把 Product Vault 目录直接配置成 Input Asset 目录。

## PostgreSQL 启动

```bash
export POSTGRES_PASSWORD='choose-a-strong-password'  # 必填，compose 不提供默认值
export PORTHOUSE_METRICS_TOKEN='choose-a-scrape-token'  # 必填，/metrics 未配置 token 时 fail-closed
export PORTHOUSE_AUTH_ENCRYPTION_KEY="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n')"  # 必填，持久化保存
docker compose -f docker-compose.runtime.yml up --build
```

Runtime Compose 使用 `pgvector/pgvector:pg17`，并会在**新建**数据目录时创建 `vector` 扩展。已有
PostgreSQL 数据卷升级镜像后不会重跑 init 脚本，需由数据库管理员在确认目标库后执行一次：

```bash
docker compose -f docker-compose.runtime.yml exec -T postgres \
  psql -U porthouse -d porthouse -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

若目标 PostgreSQL 不提供 `vector` 扩展，全文检索仍可用，但 Embedding Profile 发布和 HNSW 索引会明确
失败关闭；不要在生产 Runtime 启动后再临时编译数据库扩展。

Compose 默认挂载 `deploy/config.runtime.json`（`allowInsecureAuth=false`）；它是容器部署的生产安全基线。
本机开发改用 `config.dev.json`（开启 insecure auth，仅限本机，不要对外暴露）：

```bash
export PORTHOUSE_CONFIG_FILE=./config.dev.json
```

生产部署须设置
`PORTHOUSE_CONFIG_FILE=/absolute/path/cloud.json`，所有角色会通过
`PORTHOUSE_CONFIG_PATH=/app/config.json` 读取同一份只读配置。

生产角色还必须设置 `PORTHOUSE_ENVIRONMENT=production`。此时默认拒绝 `combined` API、
`allow_insecure_auth=true`、通配 CORS，以及少于 32 字符的 Metrics/紧急控制 Token。只有本地开发可以
使用 Combined；生产必须分别启动 `--surface public` 和 `--surface control`。

Compose 把 API 拆成两个角色：`api` 以 `--surface public` 只承载公网数据面
（runs/sessions/schedules/MCP，18790）；`control` 以 `--surface control` 承载
`/v1/admin/*` 与控制台 UI，默认只绑定 `127.0.0.1:18791`，不要暴露公网，通过
SSH 隧道或内网访问。

### systemd 部署

非 Docker 环境可使用仓库提供的 `deploy/systemd/` units。它们先运行一次 `porthouse-migrate.service`，
再启动 Public API、Worker 和 Scheduler；迁移 unit 使用 `RemainAfterExit=yes`，因此升级时必须显式重启迁移
unit，然后重启各长运行角色。`deploy/config.runtime.json` 已把 scratch、Blob 和 Input Asset 目录放在
`/var/lib/porthouse`，与 unit 的 `ProtectHome=true`、`ReadWritePaths` 一致。安装顺序、目录权限和环境文件
位置见 [systemd README](../deploy/systemd/README.md)。

启用外部 Channel 时，Channel Worker 还必须显式注入对应的 Channel 凭据环境变量；不要把
Token、App Secret、SMTP 密码写入 JSON。当前 Channel 配置由进程启动时读取，修改凭据后需要重启
Channel Worker；控制台的 Channels 页面当前提供安全状态查看，数据库化热加载属于后续计划。

本地直接运行时设置（15432 端口仅绑定 127.0.0.1）：

```bash
export PORTHOUSE_DATABASE_URL='postgresql://porthouse:porthouse-dev@127.0.0.1:15432/porthouse'
export LLM_PROVIDER='anthropic'
export LLM_API_KEY='your-key'
export LLM_MODEL='anthropic/claude-sonnet-4-5'
porthouse check --config ./config.json
```

`LLM_PROVIDER` + `LLM_API_KEY` 是单供应商本地部署的统一入口；`LLM_MODEL` 必须给出精确模型 ID，可选
`LLM_API_BASE` 覆盖服务地址。`LLM_PROVIDER` 没有默认值，而且对应 Provider 扩展必须同时出现在
`extensions.allowedIds` 并在扩展中心处于启用状态。一旦显式设置，它也是模型请求的首选路由，所以 `openrouter` 可以承载 `anthropic/...`、`openai/...`
等模型 ID，不会被其他供应商环境变量抢占。需要在同一 Worker 按模型前缀直连多个
供应商时，不设置统一变量，改用 `ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、
`OPENROUTER_API_KEY` 等专用变量。同一供应商的专用变量优先于 `LLM_API_KEY`。

全新数据库若未设置 `runtime.bootstrapModel`，会先生成使用 `unconfigured/model` 的惰性默认 Agent。
该占位 Agent 不能调用模型；控制面允许首个 Provider 在保持它不可执行的前提下完成发布，随后必须从
已生效模型目录为默认 Agent 发布一个新 Revision。`/healthz` 只表示 API 存活，产品在提交模型任务前还应
确认 `/v1/agents` 中所选 Agent 的模型不是 `unconfigured/model`。
该接口还返回 `execution_ready` 和 `execution_blockers`；产品应在提交 Run 前检查它们，而不是只依赖
`/healthz`。内置 `default` Agent 是无业务偏好的直接执行器；需要开放规划时应显式发布 coordinator
Agent，Team 则使用 Team Revision 中冻结的协调器。

一体化部署统一配置 `HAPPYHOUSE_DATABASE_URL`。独立 Porthouse 部署则配置 `PORTHOUSE_DATABASE_URL`。
本地测试使用显式专用 PostgreSQL 测试库，不能使用共享开发或生产连接。

## 健康检查

- `GET /healthz`：API 进程存活。
- `GET /readyz`：数据库可用且 schema 就绪（仅返回 `{"ok": ...}`）。
- `GET /v1/system/health`：需认证的详细存储健康信息。
- `GET /v1/me`：验证 Bearer Token 绑定的 `user_id`。
- `GET /v1/admin/overview`：验证数据库管理员权限和平台全局监控面。
- `GET /metrics`：Prometheus 文本格式指标；包含 Run/Task/Worker、Provider 平均/P95 延迟、TTFT、费用、队列年龄、租约过期、重试和 Channel outbox 聚合。采集结果按进程缓存 5 秒，避免高频抓取放大 PostgreSQL 查询压力。
  数据库暂时不可用时仍返回 `porthouse_up 0` 和 HTTP 503，便于区分进程存活与数据面就绪。
  该端点 fail-closed：未设置 `PORTHOUSE_METRICS_TOKEN` 时一律返回 404；设置后必须携带
  `Authorization: Bearer <token>`，否则返回 401。`ops/prometheus/prometheus.yml` 已按
  `credentials_file` 方式配置 bearer 抓取。
- `GET /v1/system/metrics`：管理员控制台使用的同源 JSON 指标。

Grafana 可直接导入 `ops/grafana/porthouse-overview.json`；Prometheus 抓取示例位于
`ops/prometheus/prometheus.yml`，告警规则位于 `ops/prometheus/porthouse-alerts.yml`。规则覆盖 API、
队列年龄、租约、重试、Worker、Provider 和 Channel outbox。

## 配置发布治理

Agent、Capability 与 Scenario 共用配置发布状态机。发布时冻结当前健康 Agent Worker 集合；每个
Worker 预热精确 revision 或插件依赖并提交 ACK。自动模式在全部 ACK 后切换 current pointer；人工模式
进入 `awaiting_approval`，由有 `rollouts.write` 权限的管理员批准。默认要求至少一个健康目标 Worker；
只有明确设置 `require_healthy_workers=false` 的受控引导/测试流程才允许空目标发布，生产不要使用该例外。

控制台“平台 → 集群发布”提供批准、取消、失败目标重试和回滚。对应 API 为：

- `POST /v1/admin/rollouts/{rollout_id}/approve`
- `POST /v1/admin/rollouts/{rollout_id}/cancel`
- `POST /v1/admin/rollouts/{rollout_id}/retry`
- `POST /v1/admin/rollouts/{rollout_id}/rollback`

`timed_out` 表示 deadline 前未收齐 ACK。重试会保留已经成功的 ACK；仍在线的失败/超时 Worker 会重新
进入 `pending`，已经离线的旧 Worker 目标标记为 `superseded`，并把当前健康的替代 Worker 纳入同一
rollout。这样 Worker 重启或实例替换后不会把发布永久卡在已经消失的 worker_id 上。发布失败或超时时
旧 current pointer 始终保留。显式回滚只允许已完成且目标 revision 仍为 current 的 rollout；操作
会创建一个指向 frozen previous revision 的子 rollout，重新等待 Worker ACK，成功后才切换。回滚失败时
当前版本不变。不要直接更新 definition 的 current pointer 或 rollout 表。

## 定时任务与投递排障

`GET /v1/schedules/runs?schedule_id=<id>` 是定时任务闭环的首选诊断入口。状态应按三段理解：

- `claimed / submitting / submitted`：Scheduler 正在 claim、提交，或已等待 Agent Worker 执行 Run；
- `retry_wait / error / completed / failed / cancelled / timed_out`：等待退避重试、提交最终失败，
  或已经收到 Run 终态；
- `skipped_misfire / skipped_overlap / skipped_busy / skipped_unchanged`：按触发、会话占用或
  Monitor 预检策略主动跳过，不是 Worker 故障。

同一响应中的 `attempt` 是完整 Run 的执行次数，`submitAttempt` 是当前 Run attempt 的提交次数；
`runIds` 保留该 Occurrence 的所有执行。启用 `payload.deliver` 时，还应检查
`deliveryStatus`：`pending` 会继续重投，`sent` 已确认，`dead` 表示达到
`gateway.channelSendMaxAttempts`，`deliveryError` 保存最近一次失败原因。

排障时不要直接修改 `schedules`、`schedule_occurrences` 或 `channel_outbox`。先确认 Scheduler、
Agent Worker、Channel Worker 都在注册和续租，再依据 Occurrence 的 `runId` 查询 Run events/logs；
只有提交阶段失败才没有 Run ID。Run 重试默认关闭，若任务可能产生外部副作用，启用
`max_run_retries` 前必须确认下游使用 Runtime 冻结的幂等键。

控制台自动化中心提供任务创建、启停、补跑和历史查询。手动补跑失败时，先检查 Schedule 是否正在被
其他 Scheduler claim，再检查返回的 Occurrence 是否已经获得 `runId`；禁止为“补跑”直接修改
`next_run_at_ms`。

## Webhook 入口排障

- 管理端使用 `GET /v1/event-triggers` 和 `GET /v1/event-trigger-deliveries`；两者按当前 `user_id`
  隔离，API Token 需要 `automation.read/write` scope。
- 外部投递必须发送 `X-HappyHouse-Webhook-Secret` 与稳定的 `Idempotency-Key`。创建和轮换响应中的
  明文密钥只显示一次；遗失后只能轮换，不能从数据库或控制台回显。
- 404 通常表示规则不存在或密钥错误，409 表示规则停用或相同幂等键对应不同 Payload，422 表示
  Event Type、Payload 大小或请求契约不符合规则。
- `processing` 表示请求已冻结但尚未完成 Run 提交；`submitted` 可以沿 `run_id` 进入运行中心；
  `failed` 会保留错误摘要，相同键和相同 Payload 可安全重试。
- 投递表不保存原始业务 Payload。需要留存原始事件时，应由业务系统保留并使用稳定事件 ID 对账。

## App Run 终态回调排障

App 出站终态通知与上面的入站业务 Webhook 是两个方向、两套密钥。Owner 使用
`POST /v1/apps/{installation_id}/callbacks` 登记公网 HTTPS 地址与 `env://VARIABLE`；该环境变量必须
在所有 Scheduler Worker 上存在且至少 32 bytes，不能只配置在 API 进程。

Scheduler 发送以下验证材料：

- `Idempotency-Key` 与 `X-HappyHouse-Event-ID`：稳定事件 ID，消费者据此去重；
- `X-HappyHouse-Timestamp`：签名时间戳；
- `X-HappyHouse-Signature: v1=<hex>`：对 `timestamp + "." + canonical_json_body` 的
  HMAC-SHA256；
- `X-HappyHouse-Event-Type`：`run.completed/failed/cancelled/timed_out`。

消费者应在恒定时间比较签名、限制时间戳偏差并持久化 event ID。Runtime 采用 at-least-once 投递；
HTTP 2xx 才算成功，3xx 不跟随，其他状态或网络错误按指数退避，达到登记的 `max_attempts` 后进入
`dead`。使用 `GET /v1/runs/{run_id}/app-callbacks` 查看单个 Run；Prometheus 中
`porthouse_app_callback_deliveries_total{status=...}` 和
`porthouse_app_callback_oldest_pending_seconds` 用于告警。Callback Payload 不含 Result/Artifact，
App 应用短期委托 Token 读取 Location，避免私有成果落入通用 Webhook 日志。

常见故障：

- 一直 retry 且错误为 secret 环境变量缺失：补齐 Scheduler 环境并重启；
- 地址被 SSRF policy 拒绝：必须使用公网 HTTPS，内网服务改走 Remote Capability/受控网络出口；
- 2xx 后 App 仍重复处理：App 未按 Event ID 做持久幂等；
- `dead` 持续增长：先修复接收端，再由 Owner 在 Console 的 App 治理页重放，或携带稳定
  `Idempotency-Key` 调用
  `POST /v1/runs/{run_id}/app-callbacks/{event_id}/replay`。重放创建新 Event，原记录保持 sent/dead；
  禁止直接修改 Outbox 状态。

App Client Secret 轮换属于控制面操作：调用
`POST /v1/admin/apps/clients/{client_id}/rotate-secret` 后，新 Secret 仅显示一次，全部旧委托 Token 与旧
Secret 立即失效，用户 Grant 保留。应先确保 App 后端可以原子更新秘密管理系统；若轮换后未保存响应，
只能再次轮换，不能从数据库恢复明文。安装级 Run/模型成本可在 App 治理页或
`GET /v1/apps/{installation_id}/usage` 对账；它是 Runtime 事实用量，不是支付账单。

模型用量排查优先查看 Console **运行中心 → 模型调用**。每次调用会同时展示工作 Token 与计费 Token、
缓存/推理明细、`usage_source/usage_status` 和成本完整性。`missing` 表示 Provider 没有返回可核实用量，
`partial` 表示流在失败前只返回了部分用量，不能按 0 当作完整数据。模型目录可为 LLM 配置每百万普通输入、
输出、缓存读取和缓存写入 Token 价格；Provider 未返回成本时 Runtime 才使用该冻结价格计算。未配置足够
价格时 `billing_status=missing`，即使 `cost_usd` 聚合数值为 0，也不代表免费。
配置了 `max_cost_usd` 的 Run/Task/Graph 遇到缺失成本会失败关闭，因为 Runtime 无法证明仍在预算内；要么
让 Provider 返回成本，要么在已发布模型目录补全所需价格并重新发布。

### Agent Monitor

Agent Monitor 通过普通 Schedule API 创建，只需把 `payload.kind` 设为 `agent_monitor`：

```json
{
  "name": "attention-monitor",
  "agent_id": "joy",
  "schedule": {"kind": "every", "every_ms": 300000},
  "payload": {
    "kind": "agent_monitor",
    "message": "检查失败任务和需要我关注的新事项。",
    "session_mode": "isolated",
    "quiet_token": "NO_ACTION",
    "defer_when_busy": true,
    "busy_backoff_ms": 60000,
    "preflight_mode": "runtime_attention",
    "context_mode": "light",
    "active_hours": {"start": "08:00", "end": "22:00", "timezone": "Asia/Shanghai"},
    "deliver": true,
    "channel": "telegram",
    "to": "12345"
  }
}
```

使用 `GET /v1/schedules?kind=agent_monitor` 只列出 Monitor。正常无事时 Occurrence 为
`completed` 且 `deliveryStatus=suppressed`；这不是投递丢失。`retry_wait` 且错误为
`target monitor session is busy` 表示会话忙碌延后，超过宽限期后变为 `skipped_busy`。

`context_mode=light` 不加载会话历史、持久记忆和 Skill Prompt，适合高频健康检查；需要近期对话或自动
记忆时才使用 `full`。自动 tick 落在 `active_hours` 外会记为 `skipped_inactive_hours`，不是 Scheduler
故障；手动 Run Now 仍会运行。跨午夜窗口（例如 `22:00` 到 `07:00`）受支持，时区必须是 IANA 名称。

`preflight_mode=always`（默认）每次有效 tick 都提交 Run；`runtime_attention` 只在当前用户的待处理审批、
最近七天非 Monitor Run 失败或 dead Channel 投递快照发生变化时提交。首次没有信号或后续未变化时，
Occurrence 为 `skipped_unchanged`，`monitorObservationHash` 和 `monitorObservation` 给出当次确定性证据。
该预检不调用模型、不执行 Tool，也不支持任意脚本；业务系统变化应通过正式 Connector/Capability 接入。

Monitor scratch 是私有的完整替换文档，最大 16 KiB，并使用 revision 防止覆盖并发更新：

```text
GET  /v1/schedules/{schedule_id}/monitor-scratch
PUT  /v1/schedules/{schedule_id}/monitor-scratch
     {"content":"last cursor: 17","expected_revision":0}
GET  /v1/schedules/{schedule_id}/monitor-scratch/revisions
```

建议 PUT 同时发送 `Idempotency-Key`；相同键和正文可安全重放，过期的 `expected_revision` 返回 409。
每个实际执行的 Occurrence 会冻结 `monitorScratchRevision`。Agent 内部更新使用同一
`monitor_scratch` Capability，普通 Run 或其他用户无法读取该状态。

`session_mode=main` 会读取并延续指定用户会话，可能增加上下文、Token 和记忆污染风险；除非 Monitor
确实需要近期对话，生产默认使用 `isolated`。Monitor 仍受 Agent Capability allowlist 和工具审批约束，
不能用它绕过 Dispatcher 直接执行宿主机脚本。

### Agent revision 托管 Monitor

管理员可在 Agent Draft 的 Monitor 页配置并发布 `monitor_policy`。常用配置如下：

```json
{
  "enabled": true,
  "schedule": {"kind": "every", "every_ms": 1800000},
  "message": "检查 Runtime 异常和需要用户关注的变化。",
  "context_mode": "light",
  "preflight_mode": "runtime_attention",
  "session_mode": "isolated",
  "delivery": "none",
  "active_hours": {"start": "08:00", "end": "22:00", "timezone": "Asia/Shanghai"}
}
```

策略不会创建平台共享的全局 Run。每位用户首次使用该 Agent 时，Runtime 才对账其私有托管 Schedule；
发布新 revision 会更新已经存在的用户 Schedule。Schedule API 对托管项的 PATCH/DELETE 返回 409，需发布
新 revision 修改，或将 `enabled` 设为 `false`。`delivery=origin` 可投递到最近一次真实外部渠道来源；
`none` 只把结果保存在 Run/Occurrence 中。

开发模式（显式 `gateway.allowInsecureAuth=true`）下，API 首次启动会把 `porthouse` 写入
`platform_admins` 并创建控制台初始密码：

- 用户名：`porthouse`
- 初始密码：`porthouse`

这个 11 字符密码只作为回环地址 insecure 开发模式的窄范围例外；首次登录后必须改为至少 12 字符的新
密码。本地仍保留 `X-User-ID` 无密码路径用于 API 联调，因此这个默认值绝不能
用于对外可访问的部署；可通过 `PORTHOUSE_DEV_USER_ID` 和 `PORTHOUSE_DEV_ADMIN_PASSWORD` 覆盖。
其他开发 `user_id` 不会自动获得权限。

生产控制面首次启动使用环境秘密引导管理员，不存在内置生产密码：

```bash
export PORTHOUSE_BOOTSTRAP_ADMIN_USER='platform-admin'
export PORTHOUSE_BOOTSTRAP_ADMIN_PASSWORD='use-a-unique-secret-with-12-plus-chars'
export PORTHOUSE_AUTH_ENCRYPTION_KEY="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n')"
```

`BOOTSTRAP_ADMIN_PASSWORD` 只在数据库尚无该管理员密码时生效，并把账号标记为必须改密；改密后应从部署
环境移除该变量。`AUTH_ENCRYPTION_KEY` 必须长期保留在秘密管理系统并进入备份/灾备流程，轮换前必须设计
TOTP 密钥重加密，否则已启用的验证器无法登录。控制台会话默认 12 小时，可用
`PORTHOUSE_ADMIN_SESSION_HOURS` 设置 1–168 小时。

控制台密码登录页把“管理员账号”和“操作 user_id”分开：前者只用于验证后台权限，后者决定本次个人
Run、Session、Memory、Schedule、Knowledge、Workflow 与 Work 的归属。两者不同时，浏览器会话只在
用户数据 API 上发送 `X-Impersonate-User-ID`；`/v1/auth`、`/v1/admin`、`/v1/system` 始终绑定真实
管理员，避免代操作目标影响改密、MFA 或平台治理。非 `operator` 管理员必须具有
`users.impersonate`（`*` 包含该权限）；普通数据库 API Token 不能代操作其他用户。

控制面认证接口（仅 `combined/control` surface）包括：

- `POST /v1/auth/login`：账号密码登录；启用 TOTP 后返回五分钟 MFA challenge。
- `POST /v1/auth/mfa/verify`：提交 6 位动态码或一次性恢复码，换取短期浏览器会话。
- `GET /v1/auth/status`、`POST /v1/auth/password`、`POST /v1/auth/logout`：安全状态、改密和退出。
- `POST /v1/auth/totp/setup`、`/confirm`、`/disable`：创建扫码密钥、确认激活和双重校验后停用。
- `GET /v1/me`：返回当前资源 `user_id`、真实 `actor_user_id` 和 `impersonating` 状态，供控制台常驻提示。

密码用 Scrypt 加盐哈希；TOTP 密钥用独立环境密钥加密；会话、challenge、API Token 与恢复码均只保存
SHA-256 指纹。连续五次失败会锁定 15 分钟，限流与锁定都由 PostgreSQL 跨 API 副本共享。

生产令牌由 `/v1/admin/access-tokens` 签发，数据库只保存哈希；管理员身份和细粒度权限由
`/v1/admin/users/{user_id}` 管理。令牌本身还有独立 scope（如 `runs.read`、`runs.write`、
`admin.read`、`admin.write`、`mcp.invoke`），用于收窄账号权限。服务令牌禁止 `*` scope 且必须过期；
管理 API 默认签发 90 天有效、60 天轮换提醒的用户令牌。`/v1/admin/access-token-events` 提供签发/吊销
审计。紧急 operator 凭据只能通过进程环境变量 `PORTHOUSE_CONTROL_TOKEN` 注入，不能写进 JSON 配置。

配置文件中的 `apiKey`、`token`、`password`、`databaseUrl` 等敏感字段不接受明文；应留空并由标准环境变量注入，或写成 `env://VARIABLE`。启动时引用的环境变量不存在会直接失败。

已有部署也可以继续使用一次性紧急 operator token引导 API Token：

```bash
export PORTHOUSE_CONTROL_TOKEN="$(openssl rand -base64 36)"

curl -X PUT http://127.0.0.1:18790/v1/admin/users/platform-admin \
  -H "Authorization: Bearer $PORTHOUSE_CONTROL_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"role":"admin","permissions":["*"],"enabled":true}'

curl -X POST http://127.0.0.1:18790/v1/admin/access-tokens \
  -H "Authorization: Bearer $PORTHOUSE_CONTROL_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"platform-admin","label":"initial-admin"}'
```

第二个响应中的 `token` 只出现一次。保存后从部署环境移除 `PORTHOUSE_CONTROL_TOKEN` 并滚动
重启 API；日常操作改用数据库 Token。不要把上面变量或响应写进仓库、Shell history 或日志。

## 远程 Capability 连接发布

先把 `connector-http-capability` 安装到每个 Agent Worker，加入 `extensions.allowedIds`，再通过扩展中心激活并通过插件
发布门禁使其 active。连接使用的 `signing_secret_ref` 只写 `env://VARIABLE`，同名变量必须存在于
每个目标 Agent Worker 且至少 32 bytes；API 与 Console 进程不需要该密钥。

在 Console“集成中心 → 远程能力”保存 Draft 后再发布。预热只校验固定 endpoint、密钥环境、
Schema 和精确 Capability 定义，不会调用业务能力产生副作用。任一 Worker 失败时当前连接继续
服务；查看 rollout target 错误，修复后重试失败目标。连接生效后还需逐个发布发现的 Capability，
最后在 Agent Revision 中显式选择并授予最小权限。回滚使用正常 rollout 接口，禁止直接更改
`remote_connections.current_revision_id`。完整协议和对账要求见 [远程 Capability 协议](REMOTE_CAPABILITY_PROTOCOL.md)。

## 模型 Provider 配置发布

先在所有 Agent Worker 安装并启用对应 `provider-*` 扩展，在插件中心发布为 active。密钥写入每个目标
Worker 的环境，例如 `OPENROUTER_API_KEY`；Console“集成中心 → Models”只填写
`env://OPENROUTER_API_KEY`，不能输入密钥值。Provider Draft 可以配置 Endpoint、附加 Header 环境引用、
请求超时、模型上下文/输出上限以及 Tools、Reasoning、结构化输出等目录元数据。

发布时 Worker 解析环境引用，校验精确 Provider 扩展并构造 Adapter，但不发送收费模型请求。任一 Worker
失败时旧 Provider Revision 继续生效；修复缺失环境变量、扩展版本或 Endpoint 后重试失败目标。Provider
生效后，再到 Agent Studio 选择模型并发布 Agent Revision。回滚必须走 rollout，不得直接修改
`model_providers.current_revision_id`。Agent 发布会拒绝目录外模型及超过模型输出上限的 `max_tokens`；
Provider 发布会拒绝删除或停用当前 Agent 正在引用的模型，应先发布替换 Agent Revision，再缩减目录。

本地或部署安装扩展后可先运行 `porthouse discover-extensions --config config.json`。该命令不需要 LLM
密钥，也不会启动执行 Worker；它用于解除“必须先启动 Worker 才能在 Console 看见配置 Schema”的
引导死锁。目录发现不等于发布或加载成功，生产执行资格仍以 Worker rollout ACK 为准。

## Agent 能力策略升级

升级到带 Capability policy resolution v1 的版本后，`catalog` 不再隐式授予外部写入、计费、需审批或
restricted 能力。已有发布版本不会被数据库迁移脚本改写，但它创建的新 Run 会按收紧后的规则解析能力。
升级前应在预发布环境执行：

1. 导出当前 Agent revision 的 `capability_policy`，确认实际依赖的外部/计费能力；
2. 为每个 Agent 创建新 Draft，把这些能力加入 `allowed`，并在 `permissions` 中授予 Capability 声明的
   最小权限；更推荐直接切换为 `mode=allowlist`；
3. 确认插件已 active、目标 Worker 加载精确版本、运行时配置启用且凭据健康；
4. 运行 Agent 配置测试和一条需审批的端到端 Run，验证审批、Artifact 与对账后再发布 Revision；
5. 保留旧 Agent Revision，由 rollout 机制在预热失败时继续服务。

该升级不删除数据库列或改写不可变 Revision。应用级回滚可重新部署上一版本 Runtime，并把 current Agent
指针通过正常 rollout 切回旧 Revision；不要直接修改数据库指针。回滚期间创建的 Run 仍使用各自已经冻结
的 execution snapshot，不能通过修改当前 Agent 绕过原 Run 的能力与权限边界。

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

原始读取和回放都会写 runtime audit log。开发模式引导的 `porthouse` 管理员拥有 `*`，便于联调；
生产不得照搬该授权。Trace 表包含用户输入、上下文、Tool 数据和供应商推理，应使用数据库 TLS、磁盘/
备份加密、最小权限与独立保留策略。`purge_old_runtime_data` 会一起清理模型缓存、推理、Span、Blob、
回放、Event、Log 和 Request Trace；执行前应先按合规要求归档。

需要复现问题时优先使用 `offline`/`frozen`，它们不再次访问外部系统；只有确认允许产生新的模型成本、
Tool 副作用和外部流量后，才使用 `branch`/`live`。供应商没有在 API 响应中返回的服务端内部状态无法
被平台采集，诊断台会明确显示 `unavailable`。

HTTP 客户端需要短等待时可发送 `Prefer: wait=20`；最大值为 30 秒。后台任务应发送
`interaction_mode=background` 并通过 Run 查询或 SSE 获取进度。

## Eval 与生产验收

内置业务 Eval 安装和执行：

```bash
porthouse eval-bootstrap --config ./config.json
porthouse eval-execute evalrun_<id> --config ./config.json
```

三套数据集覆盖证据研究、受治理执行和可发布作品。候选 Agent draft 只能在目标 ID、revision 与 active
Eval run 完全匹配时执行；所用 revision 会冻结到 Run snapshot，旧发布版本的成绩不能冒充候选版本。

控制台“评测与发布门禁”可把 Eval Run 排入 `eval_execution_jobs`，并管理 `eval_schedule_policies`。
Scheduler Worker 会物化到期策略，Agent Worker 以 lease/fencing 执行；`queued/running` 作业的过期 lease
可由其他 Worker 接管。不要直接修改 Eval 表。先查看 Eval Run 返回的 `execution_job`、attempt、owner 和
error，再检查 Worker 心跳。Suite/发布门禁可配置：

- `max_total_cost_usd`：整套 Case 的可观测总成本上限；
- `max_p95_latency_ms`：有时延观测 Case 的 P95 上限；
- `min_cost_coverage`：返回成本数据的 Case 占比，生产成本门禁建议设为 `1.0`。

Graph API 同时支持全图和 Task 级 `max_input_tokens / max_output_tokens / max_cost_usd`。Task 超限会立即
失败；Graph finalizer 会把 Task、协调和聚合用量相加后再次验收。并行任务可能在最后一个调用返回前已
产生费用，因此预算是 fail-closed 的完成门禁，不是供应商侧预付费额度；需要硬支付上限时仍应在模型
供应商账户配置配额。

### Knowledge K4：成本、重嵌入、ANN 与发布门禁

Embedding 模型目录必须声明精确维度；远程付费模型还必须声明 `input_cost_per_million_tokens`。Embedding
Profile 把单次操作成本、每分钟请求/Token 上限、`ann_min_rows` 和 HNSW 参数冻结进不可变 Revision。
限额由 PostgreSQL 原子计数协调全部 Worker，失败调用也会写入 `knowledge_embedding_operations`，不得通过
提高 API 副本数绕过。

模型或维度变化时，在 Console **Models → Embedding Profiles** 选中已发布 Profile，确认当前代操作用户，
再点击“为当前用户重建向量”。等价 API 为：

```text
POST   /v1/knowledge/reembedding-jobs       # 必须 Idempotency-Key
GET    /v1/knowledge/reembedding-jobs
GET    /v1/knowledge/reembedding-jobs/{job_id}
DELETE /v1/knowledge/reembedding-jobs/{job_id}
```

作业长期停在 `running` 时先看 item 的 `lease_owner/lease_version/lease_expires_at/attempt/error`，再查 Agent
Worker 日志与对应 `knowledge_embedding_operations`。Worker 死亡后 lease 过期即由其他 Worker 接管；不要
手改状态。失败最多重试五次，最终父作业进入 `failed`。取消只阻止尚未完成的投影，不删除已经成功写入的
不可变 Profile 投影。

Agent Worker 每五分钟对账 `knowledge_vector_indexes`。小于阈值显示 `exact/not_required` 是正常状态；达到
阈值后应进入 `hnsw/building → ready`。建索引使用并发 DDL和数据库 advisory lock。`failed` 时检查表中
bounded error、pgvector 版本、数据库 DDL 权限和可用磁盘；修正后等待下次对账重试。检索结果 Trace 中的
`vector_strategy` 是实际策略证据，不能只以 Profile 配置推断。

生产 Profile 建议先创建 `embedding_profile` Eval Suite：每个 Case 的 `input` 含 `corpus`（1–100 个
`{source_id,title,content}`）及 `arguments.query`，Scorer 针对 `result/tasks` 中的检索证据。把 Suite 绑定到
精确 Draft Revision 的 release gate，并启用 `require_automated`；旧 Profile 或人工 observation 的成绩不能
解锁候选 Revision。

数据库协调演练会写入带唯一 `drill:*` 用户的合成 Run/Task，默认完成后精确清理：

```bash
porthouse durability-drill \
  --confirm WRITE_SYNTHETIC_RUNTIME_DATA \
  --tasks 500 --claim-concurrency 16 \
  --config ./config.json
```

API 规模演练必须使用仅有 `runs.read`、`runs.write` 的专用服务令牌，令牌从环境读取，永不进入参数或报告：

```bash
export PORTHOUSE_LOAD_TOKEN='one-time-scoped-service-token'
porthouse load-test --base-url https://api.example.com \
  --count 200 --concurrency 16 --wait
unset PORTHOUSE_LOAD_TOKEN
```

两种命令都会把机器可读 JSON 写入 `artifacts/drills/` 并以退出码表示 PASS/FAIL。完整分阶段矩阵、SLO 和
故障注入步骤见 [生产验收手册](PRODUCTION_ACCEPTANCE.md)。

出现 `Too many open files` 时：

1. 确认运行的是预期版本的 `porthouse api`，并核对其实际配置与进程角色。
2. 使用 `lsof -p <pid>` 按类型统计句柄；PG 连接池会在角色关闭时统一释放，Agent 也不再启动 knowledge watcher/subprocess。
3. 检查是否意外把 Channel 角色与 API 混合启动，以及第三方 SDK 是否反复重连。
4. 开发机可检查 `ulimit -n`，但提升限制不能替代定位泄漏。

命令执行默认依赖 Docker 沙箱；Docker 不可用时工具返回错误，不会在 API/Worker 宿主机执行。

## 独立 HappyHouse Market

HappyHouse Market 不是 Runtime 角色，也不位于本仓库。它在相邻的独立项目
`../happyhouse-market` 中使用独立进程和独立密钥部署；第一阶段与其他服务共用 `PORTHOUSE_DATABASE_URL`，
只迁移和访问 `cloud_*`、`market_*` 表。生产环境必须额外完成：

1. TLS、限流、WAF 与 `/tuf`、`/targets`、`/v2` 的分层缓存；
2. 共享数据库整体备份恢复和 Market 签名密钥离线备份演练；
3. TUF root 离线 ceremony 与连续版本保留，不能只备份最新 `root.json`；
4. OAuth/OIDC Token 发行、最小 scope、操作员分权和高风险双人复核；
5. 真实支付 Provider 的 Webhook 验签、退款/拒付对账、税务、KYC 与 payout；
6. 在 Runtime 侧用显式固定的 TUF root 注册 Registry，并验证一次跨服务 Acquisition。

Market Web 与协议 API 可以使用不同 Origin；Registry Discovery 的 `market_web_url` 决定 Console 打开的
登录/购买页面，`market_id` 决定 Runtime 拉取和验签的协议 Origin。Console 只向 Market Web 发送本地安装
公钥，Market 用户 Token 不进入 Runtime；Market Web 返回的 DSSE Entitlement 由 Acquisition Worker 再次
验签。生产环境两者都必须使用 HTTPS，本机开发只允许 `localhost`、`127.0.0.1` 或 `::1` 使用 HTTP。

Market Alpha 内置的 `free/manual` Checkout Provider 仅用于协议与状态机测试。Market 故障不得影响已提交 Run；授权在
签名 `offline_until` 内按本地缓存继续生效，过期后只阻止新的付费能力或更新，不删除本地产物与历史。
