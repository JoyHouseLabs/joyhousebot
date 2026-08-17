# App Entry Point 定时任务（App Schedules）

状态：Implemented（2026-08-16）

## 目标

安装在 Runtime 里的 App 可以拥有定时触发的 Entry Point Run，而不必自持调度器。
这是对 [APP_INTEGRATION.md](APP_INTEGRATION.md) 委托契约的扩展：App 声明
`schedules.submit` 权限后，可以用委托 Token 创建/列出绑定到自己安装的定时任务。

## 契约

### 端点

- `POST /v1/apps/{installation_id}/schedules`（201）：为一个安装创建 App 定时任务。
  - 必须携带 `Idempotency-Key`；同键重试返回同一 schedule（`idem-` 前缀派生 ID）。
  - 请求体 `CreateAppScheduleRequest`：`name`、`schedule`（at/every/cron）、
    `payload`（`kind` 固定为 `app_entrypoint`，可选 `entrypoint_id`/`inputs`/`message`/
    `deliver`）、`policy`、`enabled`。不携带 `agent_id`：Agent 由 Entry Point 决定。
  - 创建时执行一次 `resolve_launch` 预检：安装不 active、Entry Point 缺失、依赖锁
    漂移都会在写入前失败（404/409/422），不会产生一条注定失败的 schedule。
- `GET /v1/apps/{installation_id}/schedules`：列出该安装拥有的全部 schedule。
  个人 `GET /v1/schedules` 增加了 `kind=app_entrypoint` 过滤。

### 权限与 scope

- App Pack manifest 的 `permissions` 必须声明 `schedules.submit`；安装授予必须与
  声明完全一致（既有不变量）。
- 委托 scope `apps.schedules`：`installation_scope_ceiling` 把
  `schedules.submit` 映射为 `apps.schedules`（与 `runs.submit → apps.launch` 对称）。
  没有 `apps.schedules` 的委托 Token 调写端点返回 403。
- 每个 installation 最多 20 个 schedule（`MAX_APP_SCHEDULES_PER_INSTALLATION`），
  另受用户级 `MAX_JOBS_PER_USER=50` 总量约束。

### 执行语义

- payload `kind=app_entrypoint` 的 occurrence 由调度器解析安装的 Entry Point 并经
  统一提交核心（`application/run_launch.py`）发起 Run：agent/team/scenario 走
  `RunService.create`，workflow 走 `workflows.execute`。与交互式 App launch 共用同一条
  Run 管道，`metadata.app` 注入后自动获得 `runtime_runs.app_installation_id/
  app_entrypoint_id` 生成列、usage 归属与 App 可见性。
- 幂等键 `schedule:{schedule_id}:{scheduled_for_ms|manual}:{attempt}`；session
  `app:{installation_id}:{entrypoint_id}`（截断 128）。
- 默认 policy 与 agent_monitor 一致：`misfire=skip`、`overlap=skip`（面板刷新不堆积）。

### 失效语义（防重试风暴）

- 安装离开 active（disable/uninstall/rollback）时，Runtime 立即批量停用该安装的
  全部 schedule（`schedules.installation_id` 部分索引）；重新 activate 时批量恢复。
- 执行期发现结构性不可用（安装不 active、依赖锁漂移、Entry Point 缺失）时，
  occurrence 以终态 `skipped_app_unavailable` 落账并自动 disable 该 schedule，
  `last_error` 记录原因；不会进入 `retry_wait` 退避循环。恢复路径：修复依赖后
  re-enable 或重装。

## 与 Workflow 零模型执行的组合

Entry Point 指向纯 capability 节点的 Workflow 时，定时 Run 不产生任何模型调用，
适合数据搬运与指标计算（见 [ARCHITECTURE.md](ARCHITECTURE.md) 的 Workflow Studio
章节）。
