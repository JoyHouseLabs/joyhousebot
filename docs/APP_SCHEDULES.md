# App Entry Point 定时任务（App Schedules）

状态：Implemented（2026-08-16）

## 目标

Operator 可以为已安装 App 的 Entry Point 配置 Runtime 定时触发。产品 App 本身不调用本接口：
Talent Flow 等业务应用默认拥有自己的业务调度器，Installation Authority 只使用公共 `/v2`
EntryPoint/Run/Artifact/Approval 契约。

## 契约

### 端点

- `POST /control/v1/apps/{installation_id}/schedules`（201）：为一个安装创建 App 定时任务。
  - 必须携带 `Idempotency-Key`；同键重试返回同一 schedule（`idem-` 前缀派生 ID）。
  - 请求体 `CreateAppScheduleRequest`：`name`、`schedule`（at/every/cron）、
    `payload`（`kind` 固定为 `app_entrypoint`，可选 `entrypoint_id`/`inputs`/`message`/
    `deliver`）、`policy`、`enabled`。不携带 `agent_id`：Agent 由 Entry Point 决定。
  - 创建时执行一次 `resolve_launch` 预检：安装不 active、Entry Point 缺失、依赖锁
    漂移都会在写入前失败（404/409/422），不会产生一条注定失败的 schedule。
- `GET /control/v1/apps/{installation_id}/schedules`：列出该安装拥有的全部 schedule。
  个人 `GET /control/v1/schedules` 增加了 `kind=app_entrypoint` 过滤。

### 权限与 Authority

- App Package manifest 的 `permissions` 必须声明 `schedules.submit`；安装授予必须与
  声明完全一致（既有不变量）。
- 端点属于 `/control/v1`，只接受 Operator Authority；Owner 与 Installation Token 即使持有同名
  scope 也会在命名空间边界被拒绝。
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

### 跨 occurrence 治理

Schedule 沿用同一条 Run/Task 链路提供可选的累计边界；所有字段默认关闭，不会改变
既有 schedule 行为：

- 生命周期：`max_occurrences`、`ends_at_ms`；
- 连续结果熔断：`max_consecutive_failures`、`max_consecutive_quiet`；
- 滑动窗口：`window_ms` 配合 `max_runs_per_window` 或
  `max_cost_usd_per_window`；
- 静默退避：`idle_backoff_multiplier` 配合 `idle_backoff_max_ms`。

治理准入发生在 occurrence 已被 PostgreSQL 租约锁定、Run 尚未提交时。Run 成本通过
`schedule_occurrence_runs -> model_invocations` 归属；如果配置了成本上限但窗口内存在
缺失计费的模型调用，Runtime 失败关闭并暂停 schedule，不把未知成本当作零。

`enabled` 是用户配置，`paused` 是 Runtime 的预算/熔断状态；普通 enable 操作不会暗中
清除 pause。使用 `POST /control/v1/schedules/{schedule_id}/resume` 显式恢复；
`reset_counters` 默认为 `false`，显式设为 `true` 时只清零连续失败/静默计数，不清除
不可变的累计准入次数。自动暂停与显式恢复都记入 `schedule_governance_events`。

`GET /control/v1/schedules/{schedule_id}/execution-summary` 返回只读投影：暂停原因、累计
occurrence、成功/失败/静默数、连续计数、窗口 Run/成本/计费完整性以及最近治理事件。
安装归属的 schedule 同时提供对应的 installation-scoped summary 和 resume 端点，
并在 application 层校验 schedule 确实归属该 installation。

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
