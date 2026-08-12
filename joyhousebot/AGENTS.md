# JoyhouseBot Runtime Core 协作说明

本文件适用于 `joyhousebot/` Python 包，并补充仓库根目录规则。

## 功能作用

这里是开源执行内核：认证后的 Run 提交、Task 状态机、Agent/Team/Workflow 编排、能力准入、调度、
Worker 执行、审计、回放、Artifact/Work 和 PostgreSQL 存储都在这里实现。

## 边界

- Core 只提供可复用机制，不包含 JoyHouse 产品页面、Market 商业流程或具体垂直业务。
- Runtime 使用独立数据库和账号；JoyHouse、Market、App 的业务表不得进入 Runtime database/schema。
- `api` 只做认证、参数适配、提交和查询；模型、工具、长任务只由 Worker 调用。
- 依赖方向保持为 `api/bootstrap -> application -> runtime/domain -> storage`，下层不得反向导入 API。
- PostgreSQL 是唯一事实源；内存对象、Redis、文件和进程生命周期不能决定 Run/Task 最终状态。
- Agent、Team、Workflow、Scenario 最终都编译或提交到统一 Run/Task/Event/Trace 链路，不增加旁路执行器。
- 所有 Tool、Connector、MCP 动作必须经过 Capability Registry、权限、配额、审批、幂等和审计。
- `market_protocol` 只放开放协议与纯契约；账号、订单、支付、评价和结算属于 `joyhouse-market`。
- `app_sdk` 只提供公共客户端契约，不得导入内部 Repository 或暴露 Runtime 数据库结构。
- App Pack 和 Extension 不得携带 Runtime 数据库 migration 或在安装/启动时执行自定义 DDL。

## 修改要求

- 修改公共 API、事件、状态机或数据库 Schema 时同步更新 `docs/`、迁移和契约测试。
- 新后台活动必须明确所有者、lease/fencing、重试上限、终态和关闭行为。
- 新数据默认按 `user_id + agent_id + root_run_id` 隔离，禁止引入隐式全局个人状态。
- 至少运行相关 Pytest 与 `ruff check joyhousebot tests`。
