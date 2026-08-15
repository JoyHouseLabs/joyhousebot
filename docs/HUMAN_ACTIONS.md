# 统一待办与审批视图

状态：Implemented MVP（2026-08-15）

## 目的与边界

`GET /v1/action-items` 为当前用户投影所有**正在等待人工动作**的 Runtime 节点：

- `input`：`run_input_requests.status = pending` 且所属 Run 为 `waiting_input`；
- `approval`：`approval_requests.status = pending` 且所属 Run 为 `waiting_approval`。

它不是新的 Inbox、Task 或 Approval 状态机，不保存已读、提醒、归档等第二份事实。用户提交输入或批准/拒绝审批后，原有状态机推进，下一次投影自然消失。因此它与渠道、App、Schedule 和 Team Run 始终使用同一份事实源。

`waiting_external` 不是默认的人工待办：它意味着 Runtime 正在等待回调、对子 Run 或外部对账；只有显式生成的输入请求和审批才需要用户处理。

## API 与权限

```text
GET /v1/action-items?limit=100
```

响应中的每一项都带有最小化 Run 摘要与以下二者之一：

- `input`：问题、字段、呈现提示与原 `input_request_id`；提交仍使用
  `POST /v1/runs/{run_id}/inputs`；
- `approval`：冻结 Capability 引用、风险、脱敏后的输入预览、所需角色与是否可处理；
  操作仍使用 `POST /v1/runs/{run_id}/approvals/{approval_id}/resolve`。

API Token 需要 `runs.read` 才可读取投影；真正提交输入或解析审批仍分别需要原端点的
`runs.write` 权限与 Operator 策略。App Delegation Token 仅能看见其绑定安装创建的 Run，不能借由
聚合视图扩大可见范围。

## Console

Console 路径为“工作中心 → 统一待办与审批”（`/action-items`）：

1. 对输入项按冻结字段提交答案；
2. 对审批项直接批准或拒绝；
3. 随时跳转到对应 Run 查看证据、Trace 与完整时间线。

页面不缓存或重写 Runtime 状态；操作成功后重新请求投影。

## 与 App Pack 的关系

Task Pack 已由 App Pack 控制面实现：清单、依赖锁、发布、安装、启停、升级、回滚、Market 获取和
安装级治理都位于“构建中心 → Apps”。它不是第二种调度引擎，Entry Point 仍提交标准 Run；由 App Pack
产生的等待输入和审批，也会自然出现在统一待办视图中。
