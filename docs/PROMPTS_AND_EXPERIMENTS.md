# Prompt 与在线实验治理

## 为什么 Prompt 不是普通字符串

`system_prompt` 仍可作为一次性 Run 输入，但不会替代受治理的 Prompt 资产。需要复用、评审、发布或回归验证的指令，必须进入以下链路：

`Prompt draft → structural validation → optional Eval release gate → published revision → Agent revision binding → Run execution snapshot`

Prompt 是**指令策略**，不是 Skill：

- Prompt 不执行工具，不读取私有数据，也不携带业务流程；
- Skill 是可复用任务能力，可声明工具、输入输出和操作方法；
- App 是完整业务闭环，继续通过版本化 HTTP/SSE 与 Runtime 协作；
- OCR、图像理解、重排、视频生成等属于 Extension/Capability，不进入这一控制面。

## Prompt 资产契约

Prompt ID 以 `prompt.` 开头。每个 Revision 包含不可变的内容摘要、输入 JSON Schema、输出契约、变更说明和验证报告。发布后的内容不可修改；新版本替换当前版本但不会改写已接受 Run。

自动绑定目前只支持已发布 Agent Revision 的 `system_instruction`，且只允许**无模板变量**的 Prompt。带 `{{variable}}` 的 Prompt 仍是合法资产，但只能由明确的 Skill/App/Eval 调用方传入声明过的变量并记录调用上下文，避免 Runtime 从任意 metadata 静默读取和拼接用户数据。

系统禁止把供应商没有暴露的内部推理要求为输出；可以要求“证据、判断、简短理由和不确定性”，不能要求暴露 private chain-of-thought。

控制 API：

- `PUT /control/v1/admin/prompts/{prompt_id}/versions/{version}` 保存草稿
- `POST /control/v1/admin/prompts/{prompt_id}/versions/{version}/validate` 保存结构验证证据
- `POST /control/v1/admin/prompts/{prompt_id}/versions/{version}/publish` 发布（受 `prompt` Eval gate 约束）
- `PUT /control/v1/admin/prompts/bindings` 绑定到已发布 Agent Revision

Run snapshot 中记录 Prompt revision、摘要和精确文本；对 Tool/Connector 仅透出 revision/digest，不透出完整 Prompt 内容。

## Eval 与发布门槛

Eval suite 现在支持 `agent`、`skill`、`prompt`、`scenario`、`capability` 和 `embedding_profile`。Prompt/Skill 的自动 Eval 需要在 Case `input` 中明确指定 executor Agent（默认 `default`），通过真实 Runtime Run 产生可复核的状态、延迟、token/cost 和输出证据。

发布门槛不配置即不阻塞；配置后要求对应的精确 `target_id + target_revision_id` 具有新鲜、满足阈值的 Eval 证据。这样既适合开发期快速迭代，也允许生产环境把高风险 Prompt 设为必须回归通过。

## 在线实验 / A-B / 灰度

在线 Experiment 是 Runtime 机制，首版只对**已发布 Agent Revision**生效：

1. 创建 draft，指定 2–16 个 revision variants，权重总和为 10,000 basis points；
2. 启动前验证每个 variant 都是已发布 Agent Revision；
3. Run 请求携带 `experiment_id` 时，按 `experiment salt + user_id` 稳定分桶；
4. 仅落在 `traffic_basis_points` 内的用户进入实验，其余继续原始 Agent 路径；
5. 接受 Run 时记录 variant，并把精确 Agent revision、Prompt bindings 一并冻结；
6. 终态 Run 聚合完成率、失败率、平均延迟和平均模型成本；超过 configured guardrail 会自动 `paused`，而不会删除历史证据。

Experiment assignment 表只保存不可逆 `subject_hash`，不保存原始 user ID；Run 本身仍按原有 `user_id + agent_id + root_run_id` 隔离。

控制 API：

- `PUT /control/v1/admin/experiments/{experiment_id}` 保存 draft
- `POST /control/v1/admin/experiments/{experiment_id}/start` 校验并启动
- `PUT /control/v1/admin/experiments/{experiment_id}/status` 暂停或停止
- `GET /control/v1/admin/experiments/{experiment_id}/summary` 查看结果并执行护栏检查

## 后续扩展边界

下一步可以在这个不变的 assignment/evidence 模型上增加 Prompt-only variant、shadow execution、指标判定规则和人工晋级；不应为了某一个客服、教学或运维案例在 Runtime 中新增业务表或硬编码工作流。
