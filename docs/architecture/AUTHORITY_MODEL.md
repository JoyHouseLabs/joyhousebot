# ADR：Owner、Installation、Host 与 Operator Authority

- 状态：Accepted for joyhousebot 2.0 migration
- 日期：2026-08-18
- 范围：公共执行面、控制面、Worker/Host 和 App 安装

## 问题

产品代表用户启动执行、已安装 App 在授权范围内运行，以及管理员操作平台，是三种不同权力。若产品通过
Control Token 加 `X-Impersonate-User-ID` 代表用户，产品会获得远超所需的权限，撤销、审计和数据隔离也
无法准确表达。Host 同样不能因为执行一个任务就拥有枚举用户资源的能力。

## 决策

Runtime 建立四类互斥的 Principal。每个 access token 只有一个 `principal_kind`，公共授权逻辑先解析
Principal，再由统一 application service 执行资源 scope 检查。

| Principal | 典型调用方 | 可访问范围 | 禁止 |
| --- | --- | --- | --- |
| Owner | JoyHouse、个人客户端 | 当前用户拥有的 EntryPoint、Run、Artifact、Approval 和授权资产 | Operator 管理、其他用户数据 |
| Installation | Talent Flow 等 App | `user_id + installation_id` 下批准的 EntryPoint、Run、Artifact 和 Capability | 用户全局 Memory、其他 Installation |
| Host | Worker、Connector、设备 Host | Runtime 指派的 Task/Action/Capability 与窄上下文 | 枚举 Run、任意选择 user 或扩大 Capability |
| Operator | Console、迁移器、运维 | 显式控制面 scope 内的发布、诊断、安全和配置 | 作为普通产品身份长期执行 |

Owner 不是 Operator，Installation 不是 Owner，Host 也不是 Installation。角色或 scope 不能把一种 Principal
隐式提升为另一种 Principal。

## Token 约束

公共 access token 至少冻结：

```text
principal_kind
subject_id
user_id（适用时）
installation_id（Installation 必需）
host_id / assignment_id（Host 必需）
scopes
issued_at / expires_at
credential_version / revocation_epoch
```

- 数据库只保存 token 指纹；原始 secret 不落库、不记录日志。
- Installation 以 client credential 换取短期 token；`grant_id` 是控制面实现细节，不进入 App 环境配置。
- Grant 重新授权、缩权、卸载或撤销必须推进 revocation epoch，使既有 token 失效。
- Host token 必须短期、单用途，绑定冻结的 Task/Action 和 Capability 上限。
- Owner 云端使用 OIDC/OAuth delegation；本地设备使用配对或本地登录，长期凭据保存到系统 Keychain。
- Control Token 只接受于显式 control route；公共执行面必须拒绝它。

## 授权顺序

每个请求按以下顺序失败关闭：

1. 验证签名、issuer、audience、时效、指纹和 revocation epoch。
2. 验证 `principal_kind` 是否允许访问当前 API surface。
3. 解析资源所有权，不接受客户端提供的 `user_id` 覆盖 token scope。
4. 计算 token scope、Client allowlist、Installation Grant、EntryPoint 权限和 Run capability ceiling 的交集。
5. 对输入、配额、风险和 Approval 规则进行校验。
6. 在审计上下文中记录真实 Principal、用户、Installation、资源和决策理由。

`X-Impersonate-User-ID` 只允许 Operator 诊断路由，必须要求独立 scope、理由和审计。它不得被 Product
Gateway、Desktop、普通 SDK 或后台 App 当作日常执行方式。

## 资源隔离

- Owner：按当前 `user_id` 限制资源。
- Installation：按 `user_id + installation_id` 限制 EntryPoint、Run、Artifact、Callback 和 Capability。
- Host：只访问 assignment 中冻结的输入、scratch、Artifact 和窄服务，不接收 Repository。
- Operator：诊断读取与修改操作分离 scope；读取供应商请求/响应 Blob 也必须产生审计。

Runtime 内部仍按 `user_id + agent_id + root_run_id` 隔离执行数据；EntryPoint resolution 不能削弱这一
不变量。

## 必须通过的负向测试

1. Owner token 调用 control route 被拒绝。
2. Control token 调用公共 EntryPoint/Run route 被拒绝。
3. Installation A 读取或取消 Installation B 的 Run 被拒绝。
4. Installation token 读取用户全局 Memory/Knowledge 被拒绝。
5. 撤销 Installation 后既有 access token 立即失效。
6. Host token 枚举 Run、修改其他 Task 或调用未分配 Capability 被拒绝。
7. 客户端伪造 `user_id`、`installation_id` 或 metadata 不能改变 scope。
8. Operator impersonation 缺少理由、scope 或审计上下文时被拒绝。

## 迁移要求

先实现 Principal 与公共 API gate，再迁移 JoyHouse 和 Talent Flow。JoyHouse 完成 Owner delegation 前
不得公开部署新的产品执行入口；两个参考调用方迁移后删除 Control Token 产品路径和公开 `grant_id`。
