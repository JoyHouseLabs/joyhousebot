# Work 成果资产闭环

> 状态：P1 已实现（2026-08-15）。本文定义 `Artifact → Work → App 消费 → 反馈 → 新版本`
> 的闭环。Work 的版本、发布、分享、协作者与审计，以及 App 消费的冻结交接、回执和撤销已进入
> Runtime；具体 App 的业务对象、Run 编排和后续成果版本仍由各 App 分阶段接入。

## 1. 为什么需要 Work

一次对话、一个 Run 的最终回复，通常只是瞬时结果；它可能有价值，也可能只是一次试探。`Artifact` 是
Runtime 对执行结果和证据的不可变记录，而 `Work` 是用户确认后愿意长期保存、迭代、交付或复用的
**成果资产**。

因此，不能把每次聊天自动变成 Work，也不能把 Work 当作普通聊天记录的另一个名字。

```text
聊天 / 渠道 / 定时任务 / App
              │
              ▼
       Run → Artifact（原始结果与证据）
              │ 用户或业务流程明确选择
              ▼
      Work v1（可验证、可版本化的成果）
              │ 交给一个明确的 App / 持续任务
              ▼
     App 业务状态 + Runtime Run / Artifact / Receipt
              │ 复盘、人工确认或自动验证
              ▼
      Work v2 / 后续 Work / 归档
```

这条链路使“内容方案、机会判断、客户提案、教学设计、复盘报告、学习成果”等不随聊天窗口、模型和
Agent 的更换而丢失，也让它们能成为下一个业务动作的可追溯输入。

## 2. 领域边界

| 对象 | 职责 | 不能承担的职责 |
| --- | --- | --- |
| `Run` | 一次可恢复执行的状态机和时间线 | 长期业务对象、公开成果 |
| `Artifact` | 运行输出、证据和原始材料；append-only | 公开 URL、协作和业务生命周期 |
| `Work` | 用户拥有的成果资产；不可变版本、分级、发布、分享、撤销 | App 的客户、订单、项目等业务主数据 |
| `App` | 定义具体业务状态、界面、指标和领域动作 | 第二套 Run/Task/审批/审计状态机 |
| `Work Handoff` | 一个精确 Work 版本交给一个 App 的可审计委托 | 复制 Work 私有内容或绕开权限 |

Runtime 是 Work 的权威所有者；App 仅保存 `work_id`、冻结的版本与其自身业务记录，不能直接读取
Runtime 表或以跨模块 JOIN 获取用户数据。第一阶段可以使用同一个 PostgreSQL database，但仍必须遵守
表命名、Repository 和 HTTP/SSE 边界。

## 3. 当前能力与缺口

### 已有

- 从指定 `Run + Artifact` 创建 Work，冻结源内容摘要、对象版本和执行证据；
- Work 版本递增，已发布版本不会被新草稿覆盖；
- `draft / published / archived` 状态、`private / unlisted / public` 可见性和数据分级；
- 可过期、可撤销的分享链接，协作者和审计记录；
- Run 终态回调与 Artifact 读取位置，供独立 App 以短期委托读取私有结果。

### 已实现的交接基线

- App Package Manifest 可声明 `work_consumers`，包括媒体类型、用途、最大数据分级和输入 Schema；
- Work 控制台只展示当前用户已安装、已激活且与当前 Work 媒体类型/分级兼容的消费者；
- `work_handoffs` 固定 Work 版本、内容摘要、数据分级、App/安装版本、用途和幂等键；
- 目标 App 用绑定 `user_id + installation_id` 的委托 Token 读取冻结输入，不能读取其他 Work；
- App 以幂等 receipt 回写 `accepted / executing / verified / failed`，用户可撤销未完成的交接；
- 授权、输入读取、回执和撤销都写入 Work 审计时间线。

### 后续闭环

- 交接后生成的业务对象、Run、Artifact、验证结果和用户决策需要由首批官方 App 回写完整的 receipt；
- 当前公开分享端点返回机器可读的 Work 投影，尚不是面向最终用户的成果展示页；
- 标题为 `111`、内容为默认欢迎语的 Work 只是测试数据，不应作为产品价值示例。

## 4. 目标闭环

### 4.1 选择与形成

1. 用户在 Run 的 Artifact 列表中选择“沉淀为成果”；也可由 App 明确请求形成候选 Work。
2. Runtime 校验 Artifact 属于当前用户、内容摘要完整、对象版本冻结、必要验证已完成。
3. 用户填写标题、用途、数据分级；系统创建 `Work v1 draft`。
4. 用户确认后发布，或维持私有草稿继续修订。

自动化只能创建“候选 Work”或草稿，不能把私有结果默默公开，更不能把没有来源、验证或可辨识用途的
模型回复批量转成成果。

### 4.2 交给 App

Work 页面应有“用此成果继续”入口，只展示已经安装且声明可消费该 Work 类型的 App。例如：

| Work | 交给 App 后的动作 |
| --- | --- |
| 机会判断 / 调研报告 | 市场机会雷达：建立机会、设置观察信号和复盘日期 |
| 内容资料包 / 内容大纲 | 内容工作室：建立选题、生产计划与待确认发布物 |
| 客户提案 / 会议纪要 | 轻量 CRM：建立联系人、下一次跟进和承诺事项 |
| 教学或学习方案 | 学习/课程 App：生成执行计划、日历节点和评估 |
| 项目复盘 | 项目 App：形成改进项、风险与下一周期目标 |

用户选择 App、确认用途和可见范围后，Runtime 创建一个 `WorkHandoff`。该交接必须冻结如下引用：

```json
{
  "handoff_id": "handoff_...",
  "work_id": "work_...",
  "work_version": 3,
  "content_sha256": "...",
  "data_classification": "internal",
  "app_id": "content-studio",
  "app_version": "1.2.0",
  "purpose": "create_content_plan",
  "requested_by": "user_...",
  "idempotency_key": "..."
}
```

App 用短期、范围受限且绑定安装的 `work_handoffs.read/write` 委托 Token 读取冻结版本及其允许的证据投影；
不得拿到用户全部书房、记忆、历史对话或其他 Work，也不能查看其他 App 的 Handoff。App 必须将
`handoff_id` 作为幂等键保存，重复投递只能返回同一业务对象。

### 4.3 执行、验证和回流

1. App 创建自身的业务对象，例如 `app_content_studio_content_plan`；保存 `handoff_id` 和 Work 引用。
2. 若需要 Agent 执行，App 通过公共 HTTP/SSE 创建 Runtime Run，传入冻结的 `handoff_id`，而不是自行调用模型。
3. Runtime 继续产出 Event、Trace、Artifact、Approval、成本和终态；App 继续管理其业务界面和领域状态。
4. App 将业务结果以签名 callback / receipt 回报 Runtime：创建了什么、验证状态、关联 Run、可显示摘要及下一步。
5. Runtime 在 Work 时间线追加不可变 Handoff Receipt；若结果需要长期保存，用户可把新的 Artifact 形成 `Work vN+1`
   或创建关联的新 Work。

这不是把 App 数据复制进 Runtime：Runtime 只保存最小化的交接和回执引用，完整业务数据仍在
`app_<id>_*` 表或 App 自己的服务中。

## 5. Work Handoff 状态机

`Work.status` 与 `WorkHandoff.status` 必须分开。发布状态描述成果本身，交接状态描述一次消费尝试。

```text
authorized → accepted → executing → verified
     │           │           │
     └───────────┴───────────┴────→ failed / cancelled
```

- `authorized`：用户确认授权，短期委托可签发；
- `accepted`：App 已以 `handoff_id` 创建或找到对应业务对象；
- `executing`：关联的 App 业务流或 Runtime Run 尚未结束；
- `verified`：App 或用户确认结果，写入 receipt；
- `failed`：可重试的投递、Schema、执行或验证失败，必须带错误码与下一步；
- `cancelled`：用户撤回，已撤销 App 对冻结输入的读取资格。

Work 被归档、版本升级或分享撤销不应篡改历史 Handoff。新的消费默认要求用户选择当前版本；旧 Handoff
仍可通过冻结版本审计与回放。高风险外部写入继续经过 Run 的 Approval/Action/对账链。

## 6. 公共协议设计

以下契约已实现；它们位于 Runtime 的版本化 HTTP API，App 不能读 Runtime 数据库。

| 操作 | 建议接口 | 关键约束 |
| --- | --- | --- |
| 查询可用消费者 | `GET /control/v1/works/{id}/consumers` | 仅返回已安装、已授权、声明输入兼容的 App |
| 创建交接 | `POST /control/v1/works/{id}/handoffs` | 要求 `Idempotency-Key`、精确版本、App revision、用途 |
| 查询历史 | `GET /control/v1/works/{id}/handoffs` | 仅 Work 所有者可见 |
| 获取冻结输入 | `GET /handoffs/v1/{id}/input` | 仅绑定安装的委托 App Token 可读；最小字段投影 |
| 查询回执 | `GET /handoffs/v1/{id}/receipts` | 所有者可读；委托 App 仅可读自身交接的最小回执投影 |
| App 接收回执 | `POST /handoffs/v1/{id}/receipt` | 幂等、Schema 校验；记录业务对象和 Run 引用 |
| 取消交接 | `POST /handoffs/v1/{id}/cancel` | 撤销未完成交接的读取资格；不伪造删除已发生的外部写入 |

App Package Manifest 使用 `work_consumers` 声明消费者，包含 `consumer_id`、`media_types`、`purposes`、
`max_data_classification` 和可选 `input_schema`。Runtime 只根据当前用户已激活、输入兼容的安装显示入口。
外部写入审批和输出 receipt Schema 是下一阶段的扩展，不会由 Runtime 擅自推断。

## 7. UI 与产品原则

- **Run 页面**：Artifact 上提供“沉淀为成果”，并提示来源、验证、分级和用途；不是在所有回复旁显示“发布”。
- **Work 页面**：显示版本、来源证据、发布状态、分享、协作者，以及“用此成果继续”和交接时间线。
- **App 页面**：显示“来自 Work vN”的来源卡片，不能把 Work 内容伪装成本 App 自有数据；提供“查看来源”和“形成新版本”。
- **公开页**：只展示被允许公开的 Work 投影；非公开、内部、机密 Work 不能因分享 UI 或 App 回执泄漏内容。
- **JoyHouse 产品**：将 Work 显示为“成果”，而非 Runtime 术语。用户看到的是“把机会分析交给市场雷达继续跟进”，
  而不是“创建 Work Handoff”。

## 8. 实施顺序与验收

### P0：把现有成果做对

- 从 Run Artifact 创建 Work 时补齐标题、说明、媒体类型和来源预览；
- 改善 Work 列表，归档项默认折叠，避免测试/历史版本淹没当前成果；
- 公开分享页提供安全的面向用户投影，不直接暴露原始 Artifact URL；
- 为 Work 建立“用途”和“验证状态”展示字段，但不复制 App 业务数据。

### P1：可审计交接

- [x] 新增 Runtime 所有的 `work_handoffs`、`work_handoff_receipts` 和审计迁移；
- [x] 实现版本冻结、App 委托读取、幂等回执和撤销；
- [x] 扩展 App Package Manifest 的 `work_consumers`，Console 根据声明发现消费者；
- [x] 覆盖“内容资料包 → 内容工作室”的 Runtime/App Package 集成测试；
- [ ] 市场机会雷达与 JoyHouse 产品层的真实业务回写，作为首个产品级接入。

### P2：结果回流与持续经营

- App 通过 receipt 回报业务状态、关联 Run 和验证摘要；
- Work 时间线展示交接、执行、审批、验证和后续版本关系；
- 支持用户从 App 产物形成新版本或关联成果，保留完整来源图；
- 将未处理的失败/待确认交接投影到 JoyHouse 的“今天/待处理”，而不是新增第二套收件箱状态机。

### 验收场景

1. 用户从一次机会分析 Run 选择 Artifact，形成私有 Work v1；来源、摘要和内容 digest 可查。
2. 用户选择市场机会雷达并确认。重复点击、网络重试和 callback 重放只创建一个机会对象。
3. 雷达的后续监控 Run 失败后可由 Worker 接管；Work 时间线显示失败原因和重试结果。
4. 用户审核结果，形成 Work v2；旧 Handoff 仍准确指向 v1，不能静默漂移到 v2。
5. 用户撤销尚未接收的交接，App 无法再读取冻结输入；已完成的业务动作保留审计和可对账回执。
6. 内部或机密 Work 不会被公共链接、App callback、日志或跨 App 查询泄漏。

## 9. 非目标

- 不把 Work 做成知识库、文件系统、CRM 或项目管理工具；这些仍是书房与独立 App 的责任。
- 不允许任意 App 浏览用户所有 Work、Memory、会话或 Artifact。
- 不将 App 业务模型、页面或数据库迁移写入 joyhousebot Core。
- 不承诺所有模型回复自动变成“人生资产”；沉淀必须有用户确认或明确的业务规则和证据。
