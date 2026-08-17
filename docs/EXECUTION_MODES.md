# Run 顶层执行模式

状态：Implemented（2026-08-10）

`POST /v1/runs` 必须显式指定一个且仅一个顶层编排权威：

```text
agent    单 Agent 自主执行或由该 Agent 自己规划
team     AgentTeam Coordinator 动态拆解并委派给冻结成员
scenario 已发布 Scenario Revision 约束输入、能力和确定性流程
workflow 已发布 Workflow Revision 执行显式 DAG
```

请求使用判别联合，不再接受顶层 `agent_id`、`team_id`、`scenario_id` 混合字段：

```json
{
  "execution": {"mode": "agent", "agent_id": "default"},
  "session_id": "demo",
  "input": {"type": "message", "content": "分析这个任务"}
}
```

```json
{
  "execution": {"mode": "team", "team_id": "team.market-research"},
  "input": {"type": "message", "content": "研究市场并形成证据简报"}
}
```

```json
{
  "execution": {
    "mode": "scenario",
    "scenario_id": "scenario.customer-refund",
    "version": 3,
    "agent_id": "default",
    "inputs": {"order_id": "order-123"}
  },
  "input": {"type": "message", "content": "处理这笔退款"}
}
```

```json
{
  "execution": {
    "mode": "workflow",
    "workflow_id": "workflow.weekly-review",
    "revision_id": "workflow.weekly-review:v2"
  },
  "input": {"type": "message", "content": "生成本周复盘"}
}
```

四种模式最终都落入同一套 `Run / Task / Event / Trace / Artifact / Approval / Action` 事实链，不能让
同一个 Run 同时拥有两个顶层规划者。组合由 Workflow 的显式节点完成：`team` 与固定 `scenario` 节点
冻结精确发布版本并创建持久子 Run，`agent` 节点执行单一职责，`verify / branch / bounded_loop /
approval` 提供确定性质量与控制边界。父子关系使用
`root_run_id / parent_run_id / parent_task_id`；子 Run 等待、恢复、失败传播和取消都复用统一状态机。
`interaction_mode` 只表达 HTTP 客户端希望交互等待还是后台返回，不参与编排选择。

## Team 计划确认

当 Team 冻结了显式 Collaboration Blueprint 且护栏 `require_plan_confirmation=true` 时，`team` 模式
（含 Workflow 的 team 子 Run）在 Coordinator 计划冻结后把 Run 置为 `waiting_input`：这可能是场景
追问、动态澄清或**等待计划确认**，调用方用 `GET /v1/runs/{id}/plan` 判别——`awaiting_confirmation`
为真时可 `POST /v1/runs/{id}/plan/confirmation` 执行 confirm / regenerate（需 feedback）/ cancel。
确认后同一 Run 物化 Task DAG；取消不产生任何执行 Task。Workflow 父节点在子 Run 等待确认期间保持
`waiting_external`，由子 Run 所有者确认；确认窗口默认 7 天，过期由维护清扫失败关闭。契约细节见
[协作 Blueprint 与 Team Composer](COLLABORATION_BLUEPRINTS.md)。

## 调用方工具上限

`POST /v1/runs` 的可选 `allowed_tools` 是调用方对冻结 Agent/Scenario 能力的进一步缩权：

- 不提交时，Runtime 使用已发布 Agent Revision 或 Scenario Revision 的能力策略；
- 提交非空列表时，只向本 Run 暴露同时被 Agent 授权且位于列表中的 Tool；
- 提交空列表时显式禁用所有 Tool，不能回退到 Agent 的完整能力目录；
- 列表包含 Agent 未授权能力，或超过已选择 Scenario 的冻结 allowlist 时，Run 失败关闭；
- 最终 allowlist 与 `caller_tool_allowlist_enforced=true` 进入 Run execution snapshot，可审计和回放。

这项契约用于 App Gateway、只读研究、受限自动化等上游场景。它只能缩权，不能凭请求临时授予 Agent 新能力。
