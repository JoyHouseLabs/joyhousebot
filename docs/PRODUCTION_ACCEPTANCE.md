# JoyhouseBot 生产验收与规模演练

这份手册回答一个严格的问题：当前 build 是否有足够证据作为下一代智能执行底座上线，而不只是功能看起来
完整。所有验收必须绑定 Git commit、镜像 digest、配置 checksum、Agent revision、插件 build digest 和
JSON 报告；任何一项变化都使旧证据失效。

## 放行标准

| 维度 | 必须满足 |
|---|---|
| 安全 | Public/Control 分离；不安全认证关闭；服务令牌最小 scope 且有期限；无明文密钥 |
| 业务 Eval | 三套内置 suite 均满足目标 revision 的 gate；证据不超过 24 小时 |
| API | Run 接受率 ≥ 99.5%；提交 p95 ≤ 1 秒；幂等重复产生 0 个新 Run |
| 执行 | 已接受 Run 终态率 ≥ 99%；成功率 ≥ 95%；端到端 p95 ≤ 120 秒（按场景另行收紧） |
| 队列 | Task claim p95 ≤ 2 秒；停止施压后 5 分钟内清空；无持续过期 lease |
| 恢复 | Worker 被杀后任务由新 lease owner 接管；旧 owner 的提交 100% 被 fencing 拒绝 |
| 副作用 | 未解决 reconciliation、失败 compensation、失败 Saga 均为 0 |
| 可观测 | 每个抽样 Run 可由 tracker → Run → Task → provider/tool span → Artifact 回放 |

App 数据面在上述 Runtime 门禁之外使用独立目标。以下是发布 SLO，不是仓库代码对任意部署的自动承诺：

| App 边界 | 目标与判定窗口 |
|---|---|
| Token 交换与安装查询 | 月可用性 ≥ 99.9%，服务端错误计入失败，客户端鉴权错误不计入 |
| App Run 接受 | 月可用性 ≥ 99.9%，p95 ≤ 1 秒；同一幂等键新增 Run 数恒为 1 |
| Run 终态投影 | 已接受 Run 终态完整率 ≥ 99.95%，不得出现两个相互冲突的终态 |
| Callback | 接收端健康时 5 分钟内投递成功率 ≥ 99%，原始投递与人工重放均保持事件幂等 |
| 用量归因 | 抽样安装的 Run、模型调用、Token 和 cost 与底层事实表差异为 0 |
| 恢复 | 单区域 RPO=0（已提交 PostgreSQL 事务）；实际 RTO、跨区域 RPO/RTO 由部署演练记录决定 |

## 阶段一：构建与静态门禁

```bash
./scripts/release-candidate-check.sh
```

该脚本拒绝任何未提交/未跟踪文件，并依次执行完整 Runtime、Extension、Console 和数据库前置检查；它还会
从精确 Git commit 构建临时 wheel、输出 SHA-256，并验证 Compose 配置。不要用仅通过
`pre_release_check.sh` 的脏工作区发布。记录脚本输出的 commit、wheel SHA-256 和镜像 SHA-256。生产变量中必须有 `JOYHOUSEBOT_ENVIRONMENT=production`，API 分别以
`--surface public`、`--surface control` 启动。先确认 Prometheus、Grafana 和 OTLP Collector 都能接收数据，
并验证 `/metrics` 未配置 token 时为 404；配置 token 后，无 token 或错误 token 为 401、正确 token 为 200。

## 阶段二：真实业务 Eval

1. 安装 `evals/suites/` 的三个不可变 suite。
2. 为精确候选 revision 分别创建 Eval run，不得用“latest”。
3. 运行 `joyhousebot eval-execute`。中断后执行相同命令会跳过已有 observation 并继续。
4. 检查每个 case 的 `source_run_id`、execution snapshot、事件、验证、Artifact、成本和延迟。
5. 将三个 suite 写入 exact revision 的 release gate，`max_age_hours` 设为 24，并设置
   `require_automated=true`，禁止人工 observation 冒充自动回归。

模型、Provider 路由、系统提示、Tool/Skill/Plugin digest 或安全策略改变后，即使 revision 字符串未变，也必须
创建新的 Eval run；正式流程应通过新 revision 保证不可变性。

## 阶段三：数据库协调演练

先在与生产同规格的 staging 执行 500–5000 Task：

```bash
joyhousebot durability-drill \
  --confirm WRITE_SYNTHETIC_RUNTIME_DATA \
  --tasks 1000 --claim-concurrency 32 \
  --config ./staging.json
```

它并发验证 `SKIP LOCKED` 不重复 claim、强制过期一个 lease、验证新 owner 的 fencing version 增长、验证旧
owner 无法提交，并检查幂等 Run 只创建一次。生产环境只在低峰执行，先确认目标数据库，再保留 JSON 报告；
默认清理唯一 drill 用户的合成数据。

## 阶段四：API 与 Worker 规模矩阵

使用专门的服务账号和仅包含 `runs.read`、`runs.write` 的短期令牌。默认每用户并发/每分钟提交配额会有意阻止
大规模单账号施压；演练环境应按目标负载调整 `JOYHOUSEBOT_MAX_RUNS_PER_USER` 和
`JOYHOUSEBOT_RUN_SUBMIT_PER_MINUTE`，或使用多个真实租户分片，不要把 429 当成运行时吞吐缺陷。

| 阶段 | Run 数 | 并发 | 目的 |
|---|---:|---:|---|
| smoke | 20 | 4 | 发布后快速回归 |
| baseline | 200 | 16 | 单组 Worker 的稳定基线 |
| target | 1,000 | 32 | 计划峰值与水平扩展 |
| soak | 每分钟 30–100，持续 2 小时 | 目标并发 | 连接、内存、队列和费用漂移 |

每阶段开始前记录 Worker 数和数据库连接数，结束后等待队列清空。示例：

```bash
export JOYHOUSEBOT_LOAD_TOKEN='short-lived-scoped-token'
joyhousebot load-test --base-url https://api.example.com \
  --count 1000 --concurrency 32 --wait \
  --min-accept-rate 0.995 --min-completion-rate 0.99 \
  --min-success-rate 0.95 --max-submit-p95-ms 1000 \
  --max-e2e-p95-ms 120000
unset JOYHOUSEBOT_LOAD_TOKEN
```

## 阶段五：进程与依赖故障

只对无外部副作用的演练 Agent 执行以下动作。每次只注入一种故障，记录起止时间和受影响 Run ID。

1. **Worker 优雅退出**：在有 20 个运行中 Run 时发送 SIGTERM。确认 worker registration 立即释放，其他
   Worker 完成任务，没有重复 Artifact。
2. **Worker 硬故障**：在无副作用任务运行时 SIGKILL 一个 Worker。等待原 lease 过期并确认 takeover；旧进程
   若恢复，其 stale commit 必须失败。`durability-drill` 是该 fencing 性质的数据库级确定性证明。
3. **Scheduler 重启**：停 Scheduler 两个轮询周期后恢复。确认 schedule occurrence 不重复，超时 Run 和 Graph
   finalization 被补推进。
4. **Provider 429/timeout**：在 staging 的测试 Provider/网关注入 429、5xx 和超时。确认 bounded retry、fallback、
   成本上限和 `provider_error_ratio` 告警；不得对真实外部写操作重试。
5. **PostgreSQL 短暂不可用**：仅在 staging 执行 30–60 秒连接中断。API readiness 应为 503、metrics 保留
   `joyhousebot_up 0`，恢复后队列继续；不得出现两个终态或丢失已接受 Run。
6. **OTLP/Prometheus 不可用**：执行链路必须继续，遥测缓冲不得拖垮 Worker；恢复后新 Trace 正常上报。

## 阶段六：独立 App 边界演练

先用 `AppRuntimeSimulator` 跑 App 自身的无数据库契约测试，再在 staging 使用一次性 App Client、测试用户
Grant 和无外部副作用的 Entry Point 执行真实链路：

```bash
export JOYHOUSEBOT_APP_CLIENT_ID='appclient_staging'
export JOYHOUSEBOT_APP_CLIENT_SECRET='read-from-secret-manager'
export JOYHOUSEBOT_APP_GRANT_ID='appgrant_staging'
export JOYHOUSEBOT_APP_INSTALLATION_ID='appinst_staging'
python scripts/app-integration-smoke.py \
  --confirm LAUNCH_APP_SMOKE_RUN \
  --base-url https://staging-runtime.example.com \
  --entrypoint-id safe-smoke
unset JOYHOUSEBOT_APP_CLIENT_ID JOYHOUSEBOT_APP_CLIENT_SECRET \
  JOYHOUSEBOT_APP_GRANT_ID JOYHOUSEBOT_APP_INSTALLATION_ID
```

然后逐项执行并归档证据：

1. **Client Secret 轮换**：先交换一个旧 Token；轮换后确认旧 Token 返回 401、旧 Secret 无法交换、新
   Secret 可交换；用户 Grant 仍有效。只使用可丢弃的 staging Client。
2. **Callback 接收端中断**：测试端先持续返回 503，让投递进入 `dead`；恢复 2xx 后用固定
   `Idempotency-Key` 重放两次，确认只新增一个 replay Event、`replay_sequence` 递增、原 dead Event
   不变且消费者只处理一次。
3. **Scheduler 接管**：投递处于 `sending` 时终止 Scheduler，等待 lease 过期，由另一 Scheduler 接管；
   旧 owner 的完成必须被 fencing 拒绝。
4. **用量对账**：按安装查询 `/usage`，抽取同一时间窗的 root Run、所有子 Run 和
   `model_invocations`，Run/调用/Token/cost 差异必须为 0。许可证或支付账单在 App/Market 侧另行对账。
5. **备份恢复**：恢复同一 PostgreSQL 备份后，已提交 Run 及同事务产生的 Callback Outbox 必须同时存在；
   记录实际 RPO/RTO。跨区域演练必须在目标云和网络拓扑完成，不能用本机测试结果代替。

Smoke 报告默认写入 `artifacts/drills/`，不包含 Secret。真实回调 Payload、业务输出和用户数据仍只能进入
受控发布记录，不能提交到公开仓库。

## 证据归档与结论

将以下内容放入受控发布记录，而不是提交含业务输出的报告到公开仓库：

- load/durability JSON 报告；
- 三套 Eval run ID、gate decision ID 与源 Run 样本；
- Prometheus/Grafana 时间窗口截图或导出；
- 故障时间线、告警时间、恢复时间、重复副作用检查；
- commit、wheel/image/plugin digest 和配置 checksum。

任何硬性检查失败都停止发布。失败不是“手工豁免后继续”，而是形成缺陷、修复、生成新 build，并重新运行受
影响阶段。只有 staging 全通过且生产 smoke 通过，才可声明该版本具备生产安全、可观测、真实业务质量和规模
恢复证据。
