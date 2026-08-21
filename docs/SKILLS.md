# Skill 资产与发布模型

Skill 是 joyhousebot 中独立的、声明式的“如何完成工作”资产。它可以包含方法说明、Instruction、输入输出
Schema、模板、示例、依赖和 Eval case，但本身不能联网、执行代码或写入业务系统。真实动作仍由 Capability
和 Connection 承担，并经过统一 Dispatcher、权限、审批、幂等和审计链。

## 与其他概念的边界

| 对象 | 负责什么 | 不负责什么 |
| --- | --- | --- |
| Skill | 方法、判断标准、上下文要求和输出契约 | 执行外部动作、持有密钥 |
| Capability | 一个可治理、可调用的原子动作 | 完整业务产品和长期方法沉淀 |
| Connection | 连接既有业务服务的配置与身份边界 | 决定 Agent 应如何完成任务 |
| Agent | 承担角色，绑定 Skill 并获准使用 Capability | 作为独立业务 App 的用户和计费系统 |
| Workflow | 冻结多步执行结构和每个节点的精确依赖 | 动态修改已发布 Skill 内容 |
| Extension | 交付技术实现或可导入资产的安装包 | 在安装时绕过控制面直接生效 |

## 生命周期

```text
Draft 编辑
  → 发布校验（内容 / JSON Schema / Capability / Connection / Eval 覆盖）
  → Staged
  → Agent Worker 按 content_sha256 预热并 ACK
  → Published 原子切换
  → Retired（仍可供已冻结的 Agent / Workflow 精确引用）
```

`skill_definitions` 保存资产身份、当前版本和紧急停用状态；`skill_versions` 保存不可变内容、依赖、校验证据和
摘要。发布复用统一 `configuration_rollouts` 与 `configuration_events`，不会在 API 请求线程加载模型或执行工具。

停用 Skill 是紧急停止开关：`get_published_skill` 不再向新执行提供该资产，但版本内容不会被删除或覆盖。
恢复旧版本通过再次发布 Retired 版本完成，仍需 Worker 预热；切换失败不会覆盖当前有效版本。

## 精确引用

运行时引用由三个字段组成：

```json
{
  "skill_id": "skill.market-research",
  "version": "1.0.0",
  "content_sha256": "sha256:..."
}
```

Agent Draft 的绑定还声明 `activation_mode`：

- `always`：每次执行都加载；
- `coordinator_selected`：只进入协调器可选目录，由协调计划选择；
- `scenario_required`：仅允许固定场景显式要求。

绑定随 Agent Revision 进入 Run snapshot。Workflow 保存时也把名称解析为上述精确引用，并随节点元数据冻结。
Worker 在执行前核对版本与摘要，不能仅凭同名 Skill 加载当前最新内容。

## 控制面 API

- `GET /control/v1/admin/skills`：资产目录；
- `PUT /control/v1/admin/skills/{skill_id}/versions/{version}`：创建或更新 Draft；
- `POST .../validate`：保存可审计的确定性校验证据；
- `POST .../publish`：启动 Worker rollout；
- `PUT /control/v1/admin/skills/{skill_id}/status`：启用、停用或归档。

控制台 `/ui/skills` 提供 Draft、依赖、Schema、示例、Eval、发布策略、版本历史和回退入口；Agent 控制台只从
独立 Skill 目录选择已发布版本，不再从 Capability 目录筛选 `kind=skill`。

## 隐私和分发

Skill 是可分享资产，因此不得把个人记忆、客户数据、访问令牌或私有 Artifact 原文写入 Skill 内容。App 或
Extension 可以提供待导入的 Skill 源信息，但安装包不能直接激活版本；导入后仍经过同一 Draft、校验、发布和
审计流程。用户数据继续按 `user_id + agent_id + root_run_id` 隔离。
