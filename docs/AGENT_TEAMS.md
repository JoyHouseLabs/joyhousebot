# AgentTeam 协作协议

状态：Implemented（2026-08-10）

## 1. 边界

AgentTeam 是 Core 的通用多 Agent 协作机制，不是客服、运维、研发或风控应用。Core 负责版本、委派、
执行、共享上下文、预算、审批边界与证据；具体成员身份、Prompt、Skill、Workflow、Scenario 和业务页面
由 App Pack 交付。

AgentTeam 不创建第二套 Runtime：

```text
App / Console -> Run(execution.mode=team)
                    |
                    v
        frozen AgentTeam Revision
                    |
             Coordinator plan
                    |
                    v
       existing durable Task Graph
          |         |         |
     Agent rev  Agent rev  Agent rev
          \         |         /
           append-only Workspace
                    |
             Artifact / Evidence
```

Run、Task、Event、Trace、Approval 和 Action 仍是唯一执行事实源。Workspace 只保存成员之间可复用的
结果和证据，不负责排队、重试或完成状态，也不等同于一个长期共享聊天会话。

## 2. 版本与发布

一个 Team Revision 包含：

- `team_id + revision_id + version`；
- 唯一的 `coordinator_member_id`；
- 2–32 个成员，每个成员精确绑定 `agent_id + agent_revision_id`；
- 成员职责、是否可以委派以及 `allowed_handoffs`；
- Context、Budget 和 Approval Policy；
- 可选的版本化 `collaboration_blueprint`（协作结构与护栏，见
  [协作 Blueprint 与 Team Composer](COLLABORATION_BLUEPRINTS.md)）。

发布时，每个成员必须指向当时生效的已发布 Agent Revision。发布后定义不可变。Run 提交时再次冻结当前
Team Revision（含有效 Blueprint），并把协调器的精确 Agent Revision 写入执行快照；后续 Agent 或
Team 发布不会改变已经接受的 Run。

状态机是：

```text
draft -> dependency validation -> published -> retired
```

发布保持即时生效，同时创建 `agent_team` 类型的 configuration rollout：Worker 预热（校验成员 Agent
Revision 可解析、协调者可加载）并 ACK；rollout 完成时只做守卫式重确认，绝不回拨当前指针。无 Worker
的环境（本地/测试）可照常发布。未设置显式 Blueprint 的存量 Team 解析为隐式 `parallel_synthesize`
默认，该默认不约束 Coordinator——发布显式 Blueprint（含 `blueprint-migrate` 迁移草稿）后结构才开始
强制。

## 3. 规划与委派

协调器输出显式的 Typed Collaboration DAG。每个步骤必须包含稳定 `id`、`phase`、`kind`、
`depends_on`、`acceptance_criteria` 和 `member_id`。`kind` 支持
`produce / review / revise / synthesize / checkpoint`；评审必须声明 `review_of`，修订必须声明
`revision_of`，最终综合与检查点只能
由 Coordinator 承担。Runtime 在模型输出之后再次验证：

1. 成员必须存在于冻结 Team；
2. 目标必须是协调器自身或其 `allowed_handoffs`；
3. DAG 必须无环，评审与修订引用必须属于显式依赖；
4. Task、并行数、handoff 和 `max_review_rounds` 不得超过 Team Budget；
5. Team 冻结了显式 Blueprint 时，计划还必须通过 Blueprint Compiler：步骤必须落在阶段参与者内、
   每个阶段被覆盖、阶段顺序被依赖链满足、复核独立、并发层宽不超过护栏。可修复违规触发
   `plan_blueprint_violation` 重规划；越界/超预算等致命围栏以 `plan_boundary_violation` 失败关闭；
6. Task 只获得目标成员 Agent Revision 已发布的 Capability 与 Skill 交集；
7. Worker 使用 Task 中冻结的 `agent_revision_id`，不能读取该 Agent 的新版本代替执行。

动态 `spawn` 同样受 Team 约束：当前成员必须具有委派权限，目标必须在它的 handoff allowlist 中，且子
Run 继承 `user_id + root_run_id + Team Revision`。不在 Team 中的 Agent 会失败关闭。

## 4. Shared Workspace

每次 Team 执行拥有一个按 `user_id + team_workspace_run_id + team_revision_id` 隔离的追加式 Workspace。
顶层 Team 的 `team_workspace_run_id` 就是根 Run；Workflow 内 Team 节点则使用该节点创建的 Team 子 Run，
所以同一个 Workflow 内多个 Team 不会串共享上下文。全局 `root_run_id` 继续用于整条执行树的审计和追踪。
Graph Task 完成和 Workspace 写入在同一 PostgreSQL 事务提交，避免出现“Task 已完成但上下文没有沉淀”的
半状态。每条记录保存：

- 来源 Run、Task、成员和类型；
- 有界的摘要与结构化结果；
- Artifact ID、Tool、Usage 等证据引用；
- `team` 或 `coordinator` 可见性；
- 数据库生成的时间和不可变条目 ID。

后继 Task 仍优先接收显式依赖结果，同时读取策略允许的 Workspace 尾部。并行 Task 只能看到自己开始前
已经提交的条目；这符合数据库事实，而不是假装并行分支拥有全局同步记忆。

### 4.1 必要同步

每个成员 Task 必须获得以下冻结上下文，Team 配置不能关闭：

- 根目标和当前分配目标；
- Team Revision、当前成员身份、职责与允许的 handoff；
- 用户已经确认的结构化输入；
- DAG 显式依赖结果及其 Artifact 引用；
- 本次 Run 的 Context、Budget 和 Approval Policy 快照。

这些信息决定成员“为什么做、做什么、能交给谁、依据是什么”，缺少任一项都会破坏协作可验证性。

### 4.2 可选同步

Workspace 是额外的横向共享层。`context_policy` 可以限制：

- 是否启用 Workspace；
- 可读取的 `task_result / subagent_result / decision / evidence` 类型；
- 是否注入 summary、content、structured output、Artifact、Tool 或 Usage；
- 默认 `team / coordinator` 可见性；
- 单条、总字符数和最近条目数预算。

默认不进入共享上下文：完整 Session 历史、成员私有 Memory、系统 Prompt、密钥、原始 Tool 参数、私有
推理。成员使用独立 Task Session，Workspace 也不能绕过 Artifact 权限或 Capability 审计。

## 5. Budget 与 Approval

Runtime 强制执行：

- `max_tasks`；
- `max_parallel_tasks`；
- `max_handoffs`；
- 可选的总输入 Token、输出 Token 和成本上限；
- Workspace 条目数与字符预算。

`approval_policy.require_result_approval` 可以在全部叶子成员 Task 完成后自动加入一个显式 Graph Approval
Gate；批准前 Run 保持 `waiting_approval`，拒绝、过期和审计沿用统一审批状态机。具体高风险 Tool 仍必须
走 Dispatcher、Approval、Action、幂等、对账和审计链，Team 不能扩大 Agent 或 Capability 权限。

## 6. API、Console 与 App Pack

控制面位于 `/v1/admin/teams`，支持 Draft、Revision 列表、发布、事件、Run Workspace 检查、Blueprint
预设目录（`/blueprint-presets`）、Blueprint 校验（`/blueprint-validate`）、迁移（`/{team_id}/blueprint-migrate`）
与最新 rollout（`/{team_id}/rollout/latest`）。Console 面向方案设计者的入口是“构建中心 → Team
Composer”（四步向导），原 AgentTeams 页面保留为“高级 AgentTeam 配置”。当 Blueprint 护栏
`require_plan_confirmation` 打开时，Team Run 在计划冻结后进入 `waiting_input`，所有者经
`GET /v1/runs/{id}/plan` 与 `POST /v1/runs/{id}/plan/confirmation`（confirm / regenerate / cancel）
确认后同一 Run 物化 Task DAG。公共 Run API 使用显式执行模式：

```json
{
  "execution": {"mode": "team", "team_id": "team.market-research"},
  "input": {"type": "message", "content": "研究市场并形成证据简报"}
}
```

Team 模式由 Runtime 解析并冻结协调器，调用方不能再额外传入另一个顶层 Agent 或 Scenario。确定性垂直
流程应选择 Workflow/Scenario，开放协作任务选择 AgentTeam，避免两套编排权同时生效。

Workflow 可以把冻结 Team 作为 `subrun` 节点调用。父 Task 在 PostgreSQL 中进入
`waiting_external`，子 Run 终态后恢复原 Task；子运行结果、用量与证据引用回写父 Task。父 Workflow
取消会递归请求取消所有未终态子 Run。Team 或成员版本后续退役不影响已冻结 Workflow Revision。

App Pack 使用精确引用安装 Team：

```json
{
  "assets": {
    "teams": [
      {"team_id": "team.market-research", "revision_id": "team.market-research:v1"}
    ]
  }
}
```

安装只组合发布资产，不自动授予权限，也不携带任何成员的个人 Memory 或 Knowledge。

## 7. 回归矩阵

| 保证 | 测试 |
| --- | --- |
| 计划受成员/预算约束 | `tests/test_agent_teams.py::test_coordinator_plan_is_confined_to_team_members_and_budget` |
| 显式 Blueprint 强制、隐式默认不强制 | `tests/test_agent_teams.py::test_explicit_blueprint_constrains_the_coordinator_plan` |
| 发布产生 rollout、迁移留审计且不改写已发布行 | `tests/test_agent_teams.py::test_publish_creates_agent_team_rollout_and_migration_is_audited` |
| 计划确认闭环（等待/确认/重生成/取消/重启恢复/过期） | `tests/test_plan_confirmation.py` |
| Blueprint Compiler 违规分类 | `tests/test_blueprint_plan_compiler.py`、`tests/test_blueprint_eval_corpus.py` |
| Worker 重启后 planning Run 恢复 | `tests/test_planning_replans.py`（既有） |
