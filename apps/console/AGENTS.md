# Runtime Console 协作说明

本目录是 joyhousebot Runtime 的运维与治理控制台，不是 JoyHouse 面向普通用户的产品前端。

## 功能作用

- 管理 Agent、Team、Workflow、Scenario、Skill、Capability、Extension 和模型配置。
- 查看 Run、Task、Trace、成本、Worker、发布 ACK、审计、回放和故障诊断。
- 管理本地 App Package、安装审批、版本发布与运行时授权；Market 分发属于独立服务。

## 边界

- 只能调用版本化 HTTP/SSE API；禁止直接访问 PostgreSQL 或复制 Runtime 状态机。
- API 类型、请求、认证和错误适配集中在 `src/api/`，Vue 页面不得散落自定义 `fetch`。
- Console 可以展示和发起治理动作，但最终权限、状态转换和校验必须由 Runtime 服务端决定。
- 不实现 JoyHouse 用户登录、会员、协作、计费、同步和普通用户工作台。
- Runtime 不保存 Market 登录态、订单、Entitlement 或商业授权；Console 只消费开放分发协议。

## 验证

修改后运行 `npm run typecheck` 和 `npm run build`。需要同步 Runtime 内置 UI 时，从仓库根目录运行
`./scripts/build-ui.sh`，不要手工编辑 `joyhousebot/static/ui/`。
