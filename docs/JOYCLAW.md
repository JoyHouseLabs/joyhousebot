# JoyClaw 产品壳与 JoyhouseBot 边界

JoyClaw 是 JoyhouseBot 面向个人用户的默认产品入口。它解决“我想完成什么、现在进行到哪里、最终形成了什么”的日常问题；JoyhouseBot Console 继续承担 Agent、模型、能力、插件、评测、审计和运行治理。

## 关系

```text
JoyClaw（个人产品壳）
  ├─ 开始：自然语言目标
  ├─ 进行中：需要用户关注的执行
  ├─ 成果：版本化 Work
  └─ 自动化：个人 Schedule 的简化视图
                 │ HTTP / SSE
                 ▼
JoyhouseBot Runtime（唯一执行状态机与事实源）
                 ▲
                 │ 高级配置与治理
JoyhouseBot Console（控制面）
```

## 不变量

- JoyClaw 不实现第二套 Agent、会话、任务、记忆、插件或自动化系统。
- 所有目标仍提交 `/v1/runs`，并保留 Run/Task/Event/Artifact/Work 契约。
- JoyClaw 默认复用当前用户、默认 Agent 和 `joyclaw-main` 会话；个人偏好不改变服务端资源归属。
- 普通用户界面不暴露 Revision、Capability ID、Worker Lease、Graph Patch 等控制面术语。
- 高级设置链接到同一部署的 `/ui/`，不复制 Console 表单。
- 个人数据默认私有，只有用户主动发布的 Work 或能力才能分享。

## 第一阶段页面

- `/joy/`：超级输入框、常用目标和最近执行。
- `/joy/activity`：需要关注与全部执行。
- `/joy/runs/:runId`：简化状态、结果和完整时间线入口。
- `/joy/works`：成果作品。
- `/joy/automation`：自动任务简化视图和自然语言入口。
- `/joy/settings`：默认 Agent、个人身份和会话级 API Token。

第一阶段不新增 Runtime API，也不改变现有协议。后续补充追问/审批收件箱、附件输入和从 Artifact 形成 Work 时，应继续调用现有公共契约；只有现有契约无法表达个人产品行为时，才增加薄的 application facade，禁止绕过统一执行链路。
