# 协作 Blueprint 与 Team Composer

本文是 AgentTeam 协作编排（Collaboration Blueprint、计划确认闭环、Team Composer）的契约说明，对应实现见 `joyhousebot/domain/collaboration_blueprints.py`、`joyhousebot/orchestration/blueprint_compiler.py`、`joyhousebot/storage/postgres_plan_confirmations.py` 与 Console `TeamComposer`。

## 1. 层级边界

| 层级 | 解决的问题 | 谁使用 | 是否属于 Core |
| --- | --- | --- | --- |
| AgentTeam | 哪些 Agent 可协作、各自职责与权限边界 | Runtime/方案设计者 | 是，通用机制 |
| Collaboration Blueprint | 一个 Team 默认采用哪种协作结构与护栏 | 方案设计者、App Package 作者 | 是，版本化描述 |
| Team Composer | 把角色、模式和护栏写成 Team Revision 的界面 | Console 用户 | 否，Console 能力 |
| Coordinator Plan | 本次 Run 的具体计划与上下文传播 | Worker | 是，动态执行机制 |
| Workflow | 人工精确规定的节点、分支与审批 | 方案设计者 | 是，独立执行方式 |
| Team Pack / App Package | 业务角色与页面 | App 作者、最终用户 | 否，独立 App 内容 |

原则：Core 提供协议、校验、持久化与运行；业务角色（"儿童心理专家"等）由 App Package 配置。Team 定义协作边界，不是每次 Run 的静态 DAG；Coordinator 仍因任务输入生成具体计划，Blueprint 只限定结构。Workflow 负责确定性手工编排；Team Composer 不是 DAG 编辑器。

## 2. Blueprint 结构（schema_version 1）

Blueprint 是 `AgentTeamRevision` 的版本化字段，保存在 `agent_team_revisions.definition` JSONB 内，与 context/budget/approval 三个 policy 同模式；**不需要独立迁移**，已发布 Revision 不可变。

```json
{
  "schema_version": 1,
  "preset": "parallel_review_revise_synthesize",
  "phases": [
    {"id": "produce", "kind": "produce", "participants": ["author_a", "author_b"], "mode": "parallel", "depends_on": []},
    {"id": "review",  "kind": "review",  "participants": ["reviewer"], "mode": "sequential", "depends_on": ["produce"]},
    {"id": "revise",  "kind": "revise",  "participants": ["author_a", "author_b"], "mode": "parallel", "depends_on": ["review"]},
    {"id": "synthesize", "kind": "synthesize", "participants": ["coordinator"], "mode": "sequential", "depends_on": ["revise"]}
  ],
  "guardrails": {
    "max_parallel_tasks": 4,
    "require_review": true,
    "require_plan_confirmation": true,
    "require_final_confirmation": false
  }
}
```

- 输入可给 `{preset, role_bindings, guardrails}` 简写，服务端派生规范 `phases`（`derive_preset_phases`）；
- `participants` 引用 Team 成员别名；`synthesize`/`checkpoint` 只能是协调者；
- 依赖只能指向更早阶段（结构无环）；复核者不得出现在其依赖闭包的产出者中。

### 预设注册表（5 个通用预设）

| preset | 阶段模板 | 角色绑定 |
| --- | --- | --- |
| `parallel_synthesize` | produce → synthesize | producers |
| `parallel_review_revise_synthesize` | produce → review → revise → synthesize | producers, reviewers |
| `sequential_handoff` | handoff₁…handoffₙ（每个链成员一个 produce）→ synthesize | chain（有序） |
| `research_challenge_decide` | research → challenge → decide | producers, challengers |
| `monitor_diagnose_execute_verify` | monitor → diagnose → execute → verify → synthesize | monitors, diagnosticians, executors, verifiers |

预设表达协作结构，不预设业务语义。

### 校验错误码（契约）

`blueprint_unsupported_schema_version`、`blueprint_unknown_preset`、`blueprint_unknown_fields`、`blueprint_invalid(_phase/_phase_kind/_phase_mode/_phases)`、`blueprint_unknown_participant`、`blueprint_coordinator_only_phase`、`blueprint_missing_synthesize`、`blueprint_synthesize_not_final`、`blueprint_invalid_dependency`、`blueprint_review_independence`、`blueprint_parallel_exceeds_budget`、`blueprint_review_required_missing`、`blueprint_preset_kind_missing`、`blueprint_invalid_guardrails`。

### 隐式默认与迁移

未设置 Blueprint 的既有 Team 解析为隐式 `parallel_synthesize` 默认（`effective_blueprint.origin == "implicit_default"`），**它不约束 Coordinator**——只有显式 Blueprint 才进入运行时强制（`frozen_enforced_blueprint`）。这保证存量 Team 行为零变化。"迁移"（`POST /control/v1/admin/teams/{id}/blueprint-migrate`）把默认物化为下一版本草稿并写 `blueprint_migrated` 审计事件；发布该草稿后结构开始强制。已发布 Revision 永不改写。

## 3. Blueprint Compiler（计划门禁）

插入点：`request_coordination.py` 的 `normalize` 闭包（replan 反馈）与 `build_coordinator_graph` 之后、物化之前的最终门禁。

| 违规码 | 可修复 | 语义 |
| --- | --- | --- |
| `blueprint_step_member_not_in_phase` | 是（replan） | 步骤的 kind+成员不属于任何阶段 |
| `blueprint_missing_phase` | 是 | 某阶段没有步骤覆盖 |
| `blueprint_phase_order_violation` | 是 | 阶段 B 的步骤未依赖其上游阶段 |
| `blueprint_review_independence` | 是 | 复核者审了自己的产出 |
| `blueprint_require_review` | 是 | 护栏要求复核但计划无 review 步骤 |
| `blueprint_parallel_budget_exceeded` | 是 | 计划最大并发层宽超过护栏 |
| `blueprint_boundary_violation` | 否（失败关闭） | 成员越界/越权/非协调者综合（纵深防御） |
| `blueprint_budget_exceeded` | 否（失败关闭） | 超出团队任务/交接/评审轮预算（再断言） |

可修复违规以 `plan_blueprint_violation` reason code 进入既有 replan 循环（`BlueprintRepairError`）；致命违规记录 `plan_boundary_violation` escalate 决策后让 Run 失败关闭（`PlanBoundaryViolationError`）。Coordinator prompt 注入冻结 Blueprint，要求步骤对齐阶段。

## 4. 计划确认闭环（Planning Run）

复用既有 Run/Task/Event/Artifact 状态机，不建平行系统。护栏 `require_plan_confirmation: true` 时：

```
submitted → running(planning) → 冻结 plan + graph-spec Artifact，建 run_plan_confirmations(awaiting)
                         → Run → waiting_input（plan.confirmation_requested 事件）
confirm    → confirmation(confirmed)，Run → queued → Worker 重放已接受计划 → 同一 Run 物化 Task DAG
regenerate → confirmation(regenerate_requested)，plan_generation+1，反馈注入 planner prompt，重新规划
cancel     → confirmation(cancelled)，Run → cancelled，Task 恒为 0
过期(默认7天) → maintenance 清扫，Run 失败关闭（plan_confirmation_expired）
```

- **预览即执行**：`{run_id}:plan-spec:v{gen}` 冻结编译后的 `TaskGraphSpec`；确认后由既有的崩溃安全决策重放确定性物化同一计划；
- 幂等/围栏：`act_plan_confirmation` 单条 `UPDATE ... WHERE status='awaiting_confirmation'`，Run 转换加 `AND status='waiting_input'`；重复同动作返回 `no_op`，冲突动作 409 `plan_already_actioned`；
- 重新生成上限 5 代（`plan_generation`）；regeneration 进入 planning scope 哈希，防止旧决策短路；
- Planning 本就 `allowed_tools=[]`（工具锁定），确认前无外部写；
- 与 Tool Approval 是两类独立对象，分别审计。

### 公共 API

```
GET  /control/v1/runs/{run_id}/plan
     → 200 {run_id, plan_version, status, awaiting_confirmation, actions[],
            plan{intent,summary,planned_steps,estimated_duration_seconds},
            stage_graph{phases[{id,kind,participants,mode,depends_on,step_ids}]},
            estimate{task_count,phase_count,max_concurrent}, confirmation{...}}
     → 404 run_not_found | plan_not_ready
POST /control/v1/runs/{run_id}/plan/confirmation   {action: confirm|regenerate|cancel, feedback?}
     → 200 {run, plan_confirmation, no_op?}
     → 409 plan_already_actioned / 422 plan_feedback_required | plan_regeneration_exhausted
```

SSE 走既有 Run 事件流：`plan.created`、`plan.confirmation_requested`、`plan.confirmation_resolved`、`plan.updated`。

## 5. 发布与 Worker 预热

Team 发布保持**即时生效**（翻转 `current_revision_id` + `published` 审计事件），同时创建 `aggregate_type='agent_team'` 的 configuration rollout：Worker 通过 `refresh_pending` 预热（校验成员 Agent Revision 可解析、协调者可加载）并 ACK；rollout 完成时的激活是**守卫式重确认**，绝不把指针回拨到旧版本。`require_healthy_workers=false` 保证无 Worker 环境（本地/测试）照常发布。Console 可查 `GET /control/v1/admin/teams/{id}/rollout/latest`。

## 6. Console

- **Team Composer**（`/teams/compose`）：四步向导（成员与职责 → 协作模式 → 护栏 → 预览发布）+ 试运行面板（创建 planning Run、轮询计划、确认/重新生成/取消）。默认隐藏 Team ID、revision、原始 JSON。
- **高级 AgentTeam 配置**（`/teams`，原 AgentTeams 页面）：面向平台管理员的底层控制面，新增 Blueprint 摘要、"迁移 Blueprint"按钮和 Composer 跳转。
- **Run 详情**：`waiting_input` 的 Team Run 显示计划卡（版本、阶段数、确认窗口）与内联确认操作。

## 7. App Package（Team Pack）

`assets.teams` + `entrypoints[].execution.mode="team"` + 安装期依赖锁均已内置。新增：安装校验会重新规范化 Team 的显式 Blueprint（lock check `team_blueprint`），损坏的定义失败关闭而不是 500。Blueprint 预设经由锁定的 team revision 传递性固定。示例见 `tests/fixtures/app_package_teaching_plan.json`（教学方案专家组，`parallel_review_revise_synthesize`，需计划确认）。

## 8. 观测

`/metrics` 与 `/control/v1/system/metrics` 新增：

- `joyhousebot_team_runs_total{status}`（24h，带 team_ref 的 Run）
- `joyhousebot_team_plan_actions_total{action}`（确认/重生成/取消/过期）
- `joyhousebot_team_planning_duration_seconds{quantile=p95|avg}`、`joyhousebot_team_plan_confirmation_wait_seconds`
- `joyhousebot_team_tasks_total{kind,status}`（team_step_contract.kind）
- `joyhousebot_coordinator_replans_total{reason_code}`（含 `plan_blueprint_violation`、`plan_boundary_violation`）

离线评测：`tests/evals/blueprint_plan_eval_cases.json` + `tests/test_blueprint_eval_corpus.py`（纯编译器，零模型调用）。

## 9. 回归矩阵

| 保证 | 测试 |
| --- | --- |
| 计划受成员/预算约束 | `tests/test_agent_teams.py::test_coordinator_plan_is_confined_to_team_members_and_budget` |
| 显式 Blueprint 强制、隐式默认不强制 | `tests/test_agent_teams.py::test_explicit_blueprint_constrains_the_coordinator_plan` |
| Compiler 违规分类 | `tests/test_blueprint_plan_compiler.py`、`tests/test_blueprint_eval_corpus.py` |
| 确认前零 Task / 确认后同 Run 物化 / 重生成 / 取消 / 重启恢复 / 过期 | `tests/test_plan_confirmation.py` |
| 发布产生 rollout 且迁移留审计 | `tests/test_agent_teams.py::test_publish_creates_agent_team_rollout_and_migration_is_audited` |
| Workflow team 节点等待确认并映射 Artifact | `tests/test_workflows.py::test_workflow_team_node_waits_for_owner_plan_confirmation` |
| Team Pack 安装期 Blueprint 校验 | `tests/test_app_release_control_plane.py::test_team_pack_install_validates_collaboration_blueprint` |
| 计划端点用户隔离 | `tests/test_plan_confirmation.py::test_plan_endpoints_isolate_foreign_owners` |

## 10. 手工 QA 清单（Console）

1. `/teams/compose`：选择 ≥2 个已发布 Agent，填写角色职责；
2. 选择 `parallel_review_revise_synthesize`，绑定产出者与复核者，阶段图正确渲染；
3. 护栏页打开"执行前人工确认计划"，校验通过；
4. 保存 → 发布 → rollout 显示 Worker 加载进度；
5. 试运行：填写目标 → 计划预览出现 → 确认执行 → Run 详情可见执行中状态与 Task；
6. 再次试运行 → 带反馈重新生成 → v2 计划出现；取消路径不产生 Task；
7. `/teams`（高级配置）可见 Blueprint 摘要，迁移按钮对旧 Team 生成草稿。

## 11. 非目标与后续

- 不做通用"无限画布式多 Agent 对话室"；共享状态只按依赖传递。
- 待确认计划暂不并入统一 ActionItems feed（后续增强）。
- 最终用户不直接创建 Agent、改写工具权限或编辑底层 DAG。
