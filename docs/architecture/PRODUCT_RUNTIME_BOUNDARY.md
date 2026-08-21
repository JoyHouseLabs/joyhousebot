# ADR：Product、App、Runtime、Extension 与 Market 边界

- 状态：Accepted for joyhousebot 2.0 migration
- 日期：2026-08-18
- 范围：joyhousebot、JoyHouse、Talent Flow、JoyHouse Market

## 决策

joyhousebot 是通用 Agent Runtime，不是 JoyHouse 产品后端的别名，也不是任意业务 App 的进程内框架。
JoyHouse 和 Talent Flow 是独立 App，通过版本化 HTTP/SSE 与 SDK 使用 Runtime。业务动作通过受保护的
Remote Capability Connector 进入统一 Dispatcher，不把业务代码、路由、表或事务打包进 Worker。

依赖方向只能是：

```text
JoyHouse ─┐
Talent Flow ├──▶ Public Execution API ──▶ joyhousebot Core
Other Apps ─┘                                  ▲
                                               │ narrow ABI / protocol
                                    Extensions / Hosts

JoyHouse Market ──▶ signed package acquisition ──▶ local Runtime installer
```

Core 不能依赖 JoyHouse、Talent Flow 或 Market。Market 缺失不得影响 Runtime 启动和本地包运行。

## 各层所有权

### joyhousebot Core

拥有 Run、Task、Event、Trace、Artifact、Approval、Action、调度、恢复、审计、回放、Capability 准入、
版本发布、通用 Knowledge/Memory substrate、本地包验证与安装。PostgreSQL 是唯一 Runtime 事实源。

### JoyHouse

拥有个人产品身份、目标、产品会话体验、私人数据组织、Desktop/Web/Mobile、官网与用户入口。它以 Owner
Authority 使用 Runtime，不能持有 Operator Control Token，也不能要求普通用户操作 joyhousebot Console。

### Talent Flow 与其他 App

拥有领域实体、领域状态机、业务权限、页面、通知和业务数据库。它们以 Installation Authority 使用
EntryPoint，并用 Remote Capability 暴露最小业务读写面。joyhousebot 不能直接 JOIN 或迁移 App 表。

### Extension

只承载 Provider、Channel、Connector、Capability、Context Provider 与 Host Integration。Extension 不得
承载 Agent、Team、Workflow、Scenario、Skill、业务 Prompt、产品页面或领域模型，也不得 import Runtime
内部 Repository 或 service。

### JoyHouse Market

拥有账号、目录、搜索、offer、entitlement、商业授权、获取和更新订阅。Core 只验证签名包、展示权限 diff、
安装、激活、回滚并审计；购买或下载不能自动扩大 Runtime 权限。

### joyhousebot Console

是 builder/operator/security-admin 控制面，只负责 Build、Runs、Extensions、Security 和 Operations。聊天、
个人工作台、私人资产产品体验与 Market 商店属于 JoyHouse。

## 公共与内部概念

App 运行时只承诺：

- `EntryPoint`：稳定、版本化、带输入输出 Schema 的可执行入口；
- `Run`：一次持久执行；
- `Artifact`：执行输入、中间结果或输出产物；
- `Approval`：需要授权的副作用决策。

Agent、Team、Workflow、Scenario、Skill、Revision、Task、Worker、Trace 和 Capability Graph 是构建或诊断
概念。它们可以出现在 Control API 与 Console 高级视图，但不能成为 App 启动 Run 的必需知识。

统一术语：

| 旧词 | 目标词或处理 |
| --- | --- |
| Plugin | Extension |
| Tool | Capability |
| Integration | Connection；删除无边界的通用 Integration |
| Task Pack | 删除；使用 App Release + EntryPoint |
| App Package | App Package（制品）或 App Release（版本） |

迁移期间不得为旧词新增别名；触碰旧实现时按调用方同步替换并删除旧路径。

## 数据边界

第一阶段允许共用一个 PostgreSQL database，但各模块只访问自己拥有的表：Product 使用 `product_*`，
Market 使用 `cloud_*`/`market_*`，App 使用 `app_<id>_*`，joyhousebot 只迁移 Runtime 表。禁止跨模块外键、
触发器、直接 JOIN 和越权 Repository。

私有 Run、Memory、Artifact 不因 App 发布或 Market 获取而公开。可分享成果必须先形成不可变 Work 版本，
再进入分级、分享、撤销和审计链。

## 结果

- joyhousebot 可以独立开源、安装和离线运行。
- JoyHouse 可以独立演进产品体验和部署。
- Talent Flow 只需学习稳定执行面，不需要复制 Runtime 或理解全部内部资产。
- Extension ABI 更窄，但现有把业务资产声明为 Plugin component 的实现需要破坏性迁移。
- Market 相关路由、Worker、表和依赖在消费者迁移后从 Core 删除。

## 不做的事

- 不重写已可靠的 Run/Task/PostgreSQL 状态机。
- 不把 JoyHouse 或 Talent Flow 合并进 joyhousebot 仓库。
- 不立即删除 Agent/Workflow 等内部资产。
- 不让 Market 成为 Runtime 认证或启动的强依赖。
