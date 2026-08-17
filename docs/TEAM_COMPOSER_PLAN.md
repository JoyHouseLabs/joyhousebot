# AgentTeam Composer 改造计划

> 状态：已实施（P0–P5，2026-08-17；契约与实现说明见 [COLLABORATION_BLUEPRINTS.md](COLLABORATION_BLUEPRINTS.md)）  
> 日期：2026-08-16  
> 适用范围：Porthouse Runtime、Console 与 App Pack 协议

## 1. 要解决的问题

现有 `AgentTeams` 已经具备可靠的运行时协作机制：固定成员与版本、Coordinator、持久化 Task DAG、共享 Workspace、审批、Trace 和 Artifact 都进入同一条 Run 链路。但控制台当前主要是**运行时控制面**：操作者需要手工填写 Agent/Team revision、角色、成员 ID 和 JSON 策略。

这带来两个问题：

1. 方案设计者难以在不理解底层 ID、版本与 JSON 的前提下配置协作；
2. 用户容易把 Team 定义（谁可以协作、有什么边界）与 Workflow（这次任务按什么精确顺序执行）混为一谈。

本计划的目标是增加一个面向方案设计者的 **Team Composer（协作编排器）**，让人用角色、协作模式和少量约束创建可审计的 Team，而不是手工编辑底层配置；同时保留现有高级控制面给 Runtime 管理员使用。

## 2. 产品与架构决策

| 层级 | 解决的问题 | 谁使用 | 是否属于 Core |
| --- | --- | --- | --- |
| AgentTeam | 哪些 Agent 可协作、各自职责与权限边界 | Runtime/方案设计者 | 是，通用机制 |
| Collaboration Blueprint | 一个 Team 默认采用哪种协作模式与约束 | 方案设计者、App Pack 作者 | 是，通用、版本化描述 |
| Team Composer | 将角色、模式和约束写成 Team Revision 的界面 | Console 用户 | 否，Console 产品能力 |
| Coordinator Plan | 本次具体 Run 的 Task DAG 和上下文传播 | Worker | 是，动态执行机制 |
| Workflow | 需要人工精确规定的节点、分支、审批与系统集成 | 方案设计者 | 是，独立执行方式 |
| Team Pack / App Pack | 教学方案、机会研究、客服等业务角色及页面 | App 作者、最终用户 | 否，独立 App/Market 内容 |

原则：

- Core 提供协作协议、验证、持久化、运行和观测；不内置“儿童心理专家”“课程设计专家”等业务角色。
- Team 定义协作边界，不等同于每次 Run 的静态 DAG。
- Coordinator 仍负责因任务输入而生成具体计划；Blueprint 只限定它可采用的协作结构。
- Workflow 继续承担确定性的手工编排；不要把 Team 页面改造成另一个任意 DAG 编辑器。
- 最终用户默认从 App 进入 Team Pack，不应看到 revision ID、原始 JSON 或底层调度参数。

## 3. 目标体验

### 3.1 方案设计者：四步完成一个协作 Team

1. **选择成员与职责**：从已发布 Agent Revision 中选择成员，填写清晰职责；系统自动生成 Team ID 和 revision 草稿。
2. **选择协作模式**：从可解释的预设中选择，而不是编辑 DAG JSON。
3. **设置护栏**：填写最大并行数、单次预算/时限、是否必须复核、是否在最终输出前人工确认。
4. **预览并发布**：查看成员、阶段和可执行边界；发布后由 Worker 加载确认。

首批通用预设：

- `parallel_synthesize`：多个专家并行产出，Coordinator 汇总；
- `parallel_review_revise_synthesize`：并行产出 → 独立复核 → 作者修订 → 汇总；
- `sequential_handoff`：上一步产物作为下一角色的输入；
- `research_challenge_decide`：研究 → 反方挑战/风险检查 → 决策建议；
- `monitor_diagnose_execute_verify`：监控 → 诊断 → 受控执行 → 验证。

预设表达的是协作结构，不预设业务语义。教学方案、运维、内容生产等业务角色由 App Pack 配置。

### 3.2 发起执行：先看可执行计划，再产生副作用

用户提交 Team 任务后，Runtime 创建一个普通 `Run`，首先进入 `planning` 阶段：

1. Coordinator 在 Worker 中生成计划预览；此阶段禁用外部写工具；
2. Runtime 根据 Team Revision、Blueprint、预算和权限校验计划；
3. 将计划、风险、预计成员任务、依赖关系写入 Run/Event/Artifact；
4. 当策略要求确认时，Run 进入 `waiting_input`，用户可“确认执行”“带反馈重新生成”或“取消”；
5. 确认后，Runtime 在**同一个 Run**内物化 Task DAG 并开始调度。

因此预览不是浏览器里的临时 JSON，也不是绕开 Worker 的新入口；它继承现有的审计、回放、预算、审批和恢复链路。

### 3.3 最终用户：从 App 中使用

App 可以将已经发布的 Team Revision 封装为 Team Pack。例如“教学方案专家组”可暴露为：选择课程目标、年龄段、限制条件，查看方案预览并确认。App 只能通过 HTTP/SSE 与 App SDK 访问 Runtime；业务页面、私有数据模型、销售许可均保持在 App/Market 一侧。

## 4. 协作上下文规则

共享 Workspace 不是所有 Agent 自动共享所有私有上下文。每个 Task 读取的上下文必须显式、可追踪。

| 上下文类别 | 默认流转方式 | 说明 |
| --- | --- | --- |
| 原始任务、目标、约束、预算 | Coordinator → 所有需要的成员 | 作为 Run 的冻结输入摘要 |
| 阶段产物与工具结果 | 通过 Task output/Artifact 引用 | 下游按依赖读取，避免重复复制大文本 |
| 复核意见 | Reviewer → 对应作者与 Coordinator | 绑定被复核产物和版本 |
| 最终综合结论 | Coordinator → Run 输出/Artifact | 由责任角色显式生成 |
| 私有资料、Memory、知识检索结果 | 仅按 Agent/Skill/Capability 授权注入 | 不因同属一个 Team 而自动全量共享 |
| 执行状态、Trace、审批与错误 | Runtime 记录，可按权限查询 | 不作为模型默认 prompt 全量注入 |

每个 Task 必须记录 `root_run_id`、父/依赖 Task、输入 Artifact 引用、输出 Artifact 引用与负责 Agent Revision。这样并行专家、复核者和综合者可以恢复、回放和定位责任。

## 5. Runtime 设计

### 5.1 Versioned Collaboration Blueprint

第一阶段将 `collaboration_blueprint` 作为 `AgentTeamRevision` 的版本化字段，以避免新增一套独立资产生命周期；当 Market 出现跨 Team 复用的协作模板需求时，再提升为独立可发布资产。

建议的通用结构：

```json
{
  "schema_version": 1,
  "preset": "parallel_review_revise_synthesize",
  "phases": [
    {"id": "produce", "kind": "produce", "participants": ["author_a", "author_b"], "mode": "parallel"},
    {"id": "review", "kind": "review", "participants": ["reviewer"], "depends_on": ["produce"]},
    {"id": "revise", "kind": "revise", "participants": ["author_a", "author_b"], "depends_on": ["review"]},
    {"id": "synthesize", "kind": "synthesize", "participants": ["coordinator"], "depends_on": ["revise"]}
  ],
  "guardrails": {
    "max_parallel_tasks": 4,
    "require_review": true,
    "require_plan_confirmation": true,
    "require_final_confirmation": false
  }
}
```

其中 `participants` 引用 Team 内的稳定成员别名，而不是运行时临时 ID。校验器必须拒绝不存在成员、环依赖、越权任务类型、超过并行/预算上限或绕开复核阶段的计划。

### 5.2 Blueprint Compiler 与 Coordinator 约束

新增一个纯运行时的 Blueprint normalizer/compiler：

1. 将预设和成员别名解析为已冻结的 Team Revision；
2. 对 Coordinator 生成的 typed plan 执行结构校验；
3. 对可修复偏差（漏掉综合、缺少复核输入）给 Coordinator 返回结构化修复反馈；
4. 对不可修复偏差直接让 Run 失败关闭并记录原因；
5. 仅由 compiler 生成/允许的 `GraphTaskSpec` 进入调度器。

Coordinator 可以决定具体子问题、提示词和任务粒度，但不能改变 Team 成员边界、跳过 Blueprint 强制阶段、发起未授权工具写入或超出预算。

### 5.3 Run 生命周期扩展

优先复用既有 Run/Task/Event/Trace/Approval/Artifact 状态机，不建立平行的“计划预览”执行系统：

```text
submitted → planning → plan_ready/waiting_input → running → terminal
                           │
                     regenerate / cancel
```

- `planning` 和重新生成计划仍由 Worker 执行，API 只提交/确认/查询；
- 计划预览保存为 Artifact，并通过 Event 关联至当前 Run；
- `plan_ready` 仅表示还没有创建可执行 Task；确认后一次性物化可恢复 Task DAG；
- 高风险 Tool Action 继续使用现有 Approval 链，不把“确认计划”错误实现为 Tool 审批的替代品。

### 5.4 建议的 API 演进

具体 URL 在实施时按现有版本化路由收敛；接口语义应覆盖：

- 创建/更新 Team 草稿并写入 Blueprint；
- 发布 Team Revision，等待 Worker loaded acknowledgement；
- 用 Team 模式提交一个 planning Run；
- 查询计划预览、阶段图、预计预算与验证错误；
- 对同一 Run 确认、带反馈重新生成或取消计划；
- 用 SSE 收取 planning、approval、Task 和 Artifact 事件。

产品 App 与 Console 使用相同的公共执行契约。Console 的管理接口可有更严格的管理员认证，但不能让浏览器绕开 API 直接写数据库。

## 6. Console 改造

### 6.1 新增 Team Composer

在“构建中心”增加 `Team Composer` 入口。它展示向导和只读阶段图，默认隐藏：Team ID、revision ID、原始成员 JSON、原始预算策略 JSON。

界面组成：

- 成员卡片：Agent、职责、可用工具/Skill 摘要、版本状态；
- 协作模式预设卡：包含阶段图和何时适用的说明；
- 护栏表单：并行上限、预算、复核、计划确认、最终确认；
- 发布检查：成员均为已发布版本、引用能力可用、预算合理、Blueprint 已校验；
- 试运行面板：创建 planning Run，显示阶段图、验证结果、预算估算和确认动作。

### 6.2 保留高级控制面

现有 `AgentTeams` 页面重命名为“高级 AgentTeam 配置”，继续给平台管理员：

- 查阅 revision、成员 pin、加载状态、审计历史；
- 处理迁移、调试与低层策略；
- 跳转到 Team Composer 编辑草稿。

该页面不作为普通 App 用户的入口。

### 6.3 Workflow 的边界

Workflow 页面负责精确节点与分支。后续可增加“Team 执行节点”，使一个 Workflow 节点启动已发布 Team 的 planning Run，并以确认后的最终 Artifact 作为下游输入。不要在 Team Composer 中复制 Workflow 画布功能。

## 7. App Pack 与 Market 协议

App Pack 可随包声明：所需 Agent Revision、Team Revision、Blueprint preset、所需 Skills/Capabilities、输入表单和结果渲染方式。安装时由 Runtime 完成版本与能力检查；App 通过 App SDK 提交 `user_id` 范围内的 Run。

例：教学方案 App Pack 可以声明“心理分析、课程设计、游戏设计、评测复核”四个角色，以及 `parallel_review_revise_synthesize` Blueprint。Core 只看到通用角色成员、阶段与权限，不知道教学领域的数据表、页面或商业逻辑。

Market 只分发并授权 Pack/发布物；不得读取用户的 Workspace、私有 Artifact、Run 或 Memory。

## 8. 分阶段实施计划

### P0：基线与契约梳理

- 补充 Team、Workflow、Scenario 的边界文档和现有运行时测试矩阵；
- 明确现有 Coordinator plan schema、Task DAG schema 和 `waiting_input`/Approval 的可复用点；
- 选定第一版 Blueprint JSON Schema 和错误码。

**验收**：文档可解释 Team 与 Workflow 的差异；现有 Team 执行回归测试不变。

### P1：Core Blueprint 与校验器

- 在 Team Revision 增加版本化 `collaboration_blueprint`；
- 实现预设 registry、schema 校验、成员解析、依赖检查和 guardrail 检查；
- 在 Coordinator plan normalizer 前加入 Blueprint compiler；
- 为非法成员、环、遗漏强制阶段、超并行、越权工具等建立契约测试。

**验收**：无法通过 API 或 Coordinator 计划绕过 Team 的强制协作边界；旧 Team 可迁移为明确的 `parallel_synthesize` 默认 Blueprint，并保留迁移审计。

### P2：Planning Run 与确认闭环

- 实现 planning-only Worker 路径（工具写入默认关闭）；
- 将预览、重新生成原因、校验结果和确认动作写入现有 Run/Event/Artifact 链；
- 确认后在同一 Run 物化 Task DAG；取消不创建任何执行 Task；
- 完成 SSE 事件与恢复/重试测试。

**验收**：服务重启后 planning/等待确认/执行中 Team Run 都可恢复；计划未确认前没有外部写操作。

### P3：Console Team Composer

- 新建向导、预设卡、护栏表单、成员发布检查与只读阶段图；
- 将现有 Team 页面降级为高级配置入口；
- 在 Run 详情展示“计划中/待确认/执行中”的阶段与 Task 依赖关系；
- 为所有按钮提供 loading、失败提示和发布加载状态。

**验收**：不编辑 JSON 的用户可创建、发布并试运行一个四角色 Team；界面构建通过，API 错误可理解且无静默失败。

### P4：Workflow 与 App Pack 接入

- Workflow 增加 Team 执行节点与 Artifact 输出映射；
- App SDK/Market manifest 增加 Team Pack 依赖声明；
- 以一个外部示例 App Pack（如教学方案）完成安装 → 计划 → 确认 → 执行 → 成果回放闭环。

**验收**：业务代码不进入 Core；卸载 App 不影响已存在的 Runtime 审计与 Artifact。

### P5：质量、治理与观测

- 记录阶段耗时、成员失败率、重试、计划修复次数、预算/Token 消耗和人工确认率；
- 增加 Team blueprint/plan 的离线评测样本与回归测试；
- 增加权限、配额、失败关闭、审计检索和压测验收。

**验收**：Console 及 `/metrics` 能区分 planning、执行、审批等待和失败原因；可以基于 Trace 回放每个角色使用过的输入 Artifact 与输出。

## 9. 非目标与风险控制

- 第一阶段不做通用“无限画布式多 Agent 对话室”；共享状态必须可追踪且只按依赖传递。
- 第一阶段不让最终用户任意创建 Agent、改写工具权限或编辑底层 DAG。
- 不把业务角色、行业 SOP、CRM/教学/运维数据模型加入 Core。
- 不将计划确认与高风险 Tool Approval 混成同一种对象；两者分别审计。
- 对可能破坏现有公共 API 的字段/状态变化，实施前必须提供迁移、文档和回滚方案；当前开发阶段可清理无使用方的内部接口，但不得悄然改变已发布契约。

## 10. 首个可交付增量

优先交付 P1 + P3 的最小闭环：

1. 一个 `parallel_review_revise_synthesize` 预设；
2. 四名已发布 Agent 的成员选择器；
3. Blueprint 结构校验和发布检查；
4. Console 阶段图与高级页面跳转；
5. 保留现有 Coordinator 执行路径，不在本增量中加入人工计划确认。

这样先解决“控制台太复杂、无法手动配置协作”的主要体验问题，再安全地将 planning preview、确认和 App Pack 接入扩展到完整闭环。
