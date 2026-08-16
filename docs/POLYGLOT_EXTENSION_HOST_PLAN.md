# Polyglot Extension Host 开发计划

状态：Runtime Baseline Complete；D0-D10 已完成，D11 协议基线完成，D12 Runtime/Console/部署基线完成；
JoyHouse Desktop 打包与具体生态 Driver 在各自仓库继续实施（2026-08-16）

## 1. 复核结论

JoyhouseBot 不需要从零建设第二套异步执行框架。当前代码已经具备：

| 能力 | 当前状态 | 可复用实现 |
| --- | --- | --- |
| 签名 Remote Capability | 已实现 | `connector-http-capability`、`REMOTE_CAPABILITY_PROTOCOL.md` |
| `accepted -> reconcile` | 已实现 | `OperationReconciliationCoordinator` |
| 等待外部结果并释放 lease | 已实现 | `waiting_external`、`operation_reconciliations` |
| 对账抢占和 stale worker fencing | 已实现 | reconciliation `lease_owner/lease_version` |
| Run/Task 故障接管 | 已实现 | PostgreSQL Run/Task lease |
| Action、幂等、审批、写回执 | 已实现 | `CapabilityDispatcher`、`action_intents`、`approval_requests` |
| Remote Connection/Capability 发布 | 已实现 | connection rollout、Capability rollout、Worker ACK |
| 用户侧人工对账 | 已实现 | `/v1/runs/{run_id}/operations` |
| Remote Connection Console | 已实现 | `RemoteConnections.vue` |
| Remote Capability 流式进度 | 基线已实现 | reconcile cursor、有界事件、规范化投影和恢复已完成；SSE 仍是可选低延迟增强 |
| Node Extension SDK/Host | 已实现 | 签名 Node SDK、Host Profile、精确 preflight、独立进程 Supervisor 与持久 operation 路由 |
| OpenCLI 浏览器生态接入 | Host Pilot 已实现 | 精确版本、Catalog 编译、严格 argv、Profile、退出码、幂等恢复与供应链校验；设备桥已完成，产品专用 UI 后续实施 |
| Cloud 到本地设备 Host | 已实现 | 出站领取、设备身份、claim fencing、离线恢复、短期模型/工具授权与 Console 撤销 |
| Host Artifact upload grant | 已实现 | 一次性 grant、流式摘要校验、lease/fencing 与不可变 Artifact 物化 |
| Host Model Proxy | 已实现 | 短期 grant、事务预算、OpenAI-compatible Gateway 与 usage 对账 |
| Host 动态 Tool Broker | 已实现 | 冻结 allowlist、短期 grant、durable child Action、审批与结果轮询 |
| Node Host 治理 Console | 已实现 | 设备在线状态、精确能力摘要、一次性 Token 轮换、撤销与 Host model grant 投影 |
| Server Model Gateway 部署 | 已实现 | systemd loopback unit 与 Compose internal-only service/readiness |

因此开发原则是：

> 先让 Node Extension 成为现有 Remote Capability 的另一种实现语言，再逐步增加流式进度、Supervisor、
> Artifact、OpenCLI、设备桥、模型代理和动态 Tool Broker；任何阶段都不复制 Run/Task/Approval 状态机。

## 2. 本轮复核后的架构修正

### 2.1 不新增第一版 Host Dispatcher Worker

现有 Agent Worker 已能重新 claim `waiting_external` Run/Task，并通过带独立 lease 的
`operation_reconciliations` 完成对账。第一版继续使用这条路径，不增加只为保持 SSE 连接而存在的新 Worker
角色。可选 SSE 是低延迟增强，正确性仍依赖 reconcile。

### 2.2 不把 Runtime lease 暴露成 Host 身份

Host operation 跨越多次 Worker claim，必须绑定稳定的 `action_id + idempotency_key + request_digest`，而
不是某一次短暂的 Run/Task lease。Runtime 内部 reconciliation lease 决定谁可以消费事件和推进状态，
Run/Task lease 决定谁可以提交 Runtime 状态。

### 2.3 Pi Pilot 不依赖动态 Tool Broker

第一版 Pi 只在隔离 worktree/container 内读取、修改和测试，返回 patch、报告和截图；应用 patch、commit、
部署和外部写入作为后续独立 Runtime Capability。这样先验证 Node Host 和恢复机制，再建设复杂的 Host
反向 Tool 调用协议。

### 2.4 Invocation、Channel、Event Source 分开演进

- Invocation Host 首先复用 Remote Connection 和 Capability rollout；
- WhatsApp 保留 Python Channel adapter + Node driver，不塞进 operation reconciliation；
- Event Source 后续使用签名 Trigger envelope，不伪装成 Capability；
- 三者共享 Node SDK 的完整性、日志、配置、密钥和进程生命周期基础模块。

### 2.5 OpenCLI 是官方 Extension，不是 Core 特例

OpenCLI 作为第一个真实浏览器生态 Pilot，使用普通 Invocation Capability 接入，不在 Runtime 中增加
`opencli` 分支，也不把任意命令字符串开放给 Agent。安装时读取并校验上游命令 Manifest，再把准入命令
冻结为精确、版本化 Capability。OpenCLI 自带 Skill 只作为工作方法和调用说明；真正的工具执行仍经过
Capability Dispatcher、Action、Approval、Artifact 和 Audit。

本地 Desktop 可以直接访问同机 Node Host、OpenCLI daemon 与已登录 Chrome。Cloud Runtime 复用用户
本机登录态时，需要独立的 Device Host Transport：设备主动建立出站连接，SSE 只用于唤醒，任务领取、
结果提交和恢复仍使用签名 HTTP 与 PostgreSQL 对账。设备桥是传输适配，不创建第四种执行语义或第二套
Run/Task 状态机。

## 3. 目标代码组织

新增顶层目录时同步增加 `AGENTS.md`，明确边界：

```text
hosts/
`- node/                         # 通用 Node Host Supervisor，不含具体供应商逻辑

sdks/
`- node/                         # @joyhousebot/extension-sdk 与协议测试向量

extensions/
|- capability-opencli/           # OpenCLI 浏览器与网站命令适配器
|- capability-pi-runner/         # Pi Invocation Extension
|- capability-browser-playwright/# 后续通用能力
`- channel-whatsapp/             # 现有 Python adapter；Node driver 后续迁移到 SDK

tests/
|- contract/extension-host/      # 跨语言协议 fixtures
`- integration/extension-host/   # Python Runtime <-> Node Host 集成测试
```

Node Host、SDK 和每个 Extension 是独立制品。生产 Extension 不共享可变全局 `node_modules`；开发阶段可以
使用 workspace 提升效率，但每个发布包必须拥有可验证 lockfile/build digest。

## 4. D0：冻结协议与测试向量

状态：完成。

目标：在写 Host 代码前冻结最小可实现契约。

### 修改范围

- `docs/EXTENSION_HOST_PROTOCOL.md`
- `docs/REMOTE_CAPABILITY_PROTOCOL.md`
- `tests/contract/extension-host/`
- `extensions/connector-http-capability` 的签名 fixtures

### 工作项

1. 定义 Host Profile JSON Schema；
2. 确定 `request_digest` 的 canonical 内容：Capability identity、subject、authorization 和 input；
3. 定义 `operation_id`、bounded event batch、cursor、checkpoint reference 和 command envelope；
4. 定义固定 Host 路径，禁止 Host 返回可选目标 URL；
5. 提供 Python/TypeScript 共用的 canonical JSON、HMAC、响应签名和幂等冲突向量；
6. 将 SSE 标记为 optional，reconcile 标记为 correctness baseline。

### 验收

- Python 与 TypeScript 对全部签名向量逐字节一致；
- 同键同请求返回相同 operation，同键换请求返回 409；
- 文档中不存在第二套 Run/Task、Approval 或 rollout。

## 5. D1：最小 Node SDK 与 Echo Capability 纵向切片

状态：完成。实现位于 `sdks/node/`、`hosts/node/fixtures/echo-host/` 和
`tests/contract/extension-host/`；Fixture 重启后未完成 operation 明确返回 `unknown`，不伪装持久恢复。

目标：不修改 Runtime 状态机，先证明 Node 可以使用现有 Remote Capability v1 完成真实调用。

### 新增范围

- `sdks/node/`：Manifest、协议类型、canonical JSON、HMAC、错误和日志工具；
- `hosts/node/fixtures/echo-host/`：只用于契约测试的单 Extension Host；
- Node build/test 脚本和锁文件；
- `tests/integration/extension-host/test_echo_host.py`。

### 场景

1. 同步 `echo` Capability；
2. 异步 `delayed-echo` 返回 `accepted`，随后由现有 `operations:reconcile` 完成；
3. 重复请求不重复执行；
4. HMAC、nonce、响应签名、Schema 和输出大小错误均 fail closed。

### 验收

- Echo Host 不访问 PostgreSQL；
- Runtime 无需知道实现语言；
- Node 进程重启后异步 operation 可恢复或进入明确 `unknown/manual_required`；
- Python 测试、Node 测试和 Ruff 全部通过。

## 6. D2：Remote Connection Host Profile 与安全预热

目标：让 Runtime 区分普通业务 Remote Capability 和受管 Extension Host，但继续共用控制面。

### Runtime 修改点

- `joyhousebot/domain/remote_connections.py`
- `joyhousebot/api/schemas.py`
- `joyhousebot/application/remote_connections.py`
- `joyhousebot/bootstrap/agent_runtime_catalog.py`
- `extensions/connector-http-capability/.../connector.py`
- `apps/console/src/api/remoteConnections.ts`
- `apps/console/src/views/RemoteConnections.vue`

### 契约扩展

Remote Connection 增加强制类型字段。项目尚未正式发布，不保留开发期旧配置的兼容分支：

- `service_profile`: `business` 或 `extension_host`；
- `host_protocol_version`；
- `expected_host_manifest_digest`；
- `require_host_preflight`。

Remote Capability declaration 贯通当前已存在但远程路径会丢失的字段：

- `execution_mode`；
- `supports_stream`；
- Host Profile/SDK/build/lockfile provenance。

Host 使用固定、签名、只读的 `POST /meta:describe`。Worker preflight 比对 Host protocol、Host build、
Extension build、lockfile digest 和 Capability 精确定义；不调用业务 Capability，不产生写入。

### 验收

- 普通 Remote Connection 明确配置 `service_profile=business`，不接受 Host 专属字段；
- Host preflight 任何摘要不一致都会使 rollout 失败且旧 Revision 保持 active；
- Console 分开展示“业务远程能力”和“Extension Host”，但仍使用同一个版本化 API。

## 7. D3：长程进度、事件投影和恢复

状态：已实现（2026-08-15）。Core 已提供有界事件批次、规范化事件表、原子 cursor/checkpoint 投影、
lease fencing、私有 Run Event 映射和用户范围内的分页查询；SSE 保持为后续可选低延迟增强。

目标：为长任务提供进度与证据，不改变现有对账正确性。

### 存储设计

升级 `operation_reconciliations`，只增加固定、小字段：

- `provider_cursor`；
- `checkpoint_ref`；
- `progress_summary`；
- `progress_percent`；
- `last_provider_event_at`。

新增规范化 `operation_reconciliation_events`：

- 主键 `reconciliation_id + event_id`；
- 唯一 `reconciliation_id + sequence`；
- `event_type`、有界 `payload`、`created_at`；
- payload、单次 event batch 和总保留量有硬上限。

不要把不断增长的事件数组放入 `operation JSONB`。

### 执行路径

1. `operations:reconcile` 返回状态、cursor 和有界 events；
2. 当前 reconciliation lease owner 去重并落库；
3. Runtime 将安全摘要映射为正常 Run Event/Trace；
4. cursor 只有在事件持久化成功后推进；
5. 可选 SSE 只减少延迟，断线后仍回到 reconcile cursor。

### 验收

- 重复 event batch 不重复 Runtime Event；
- cursor 丢失或过期时查询终态并记录恢复决定；
- 旧 reconciliation lease owner 不能推进 cursor；
- 10 万事件压测不会形成无限 JSONB 或拖慢 Run 列表查询。

## 8. D4：通用 Node Host Supervisor

状态：Alpha 已实现（2026-08-15）。已覆盖精确入口校验、三 Extension 独立 child process、JSONL IPC、
有界 stdout/stderr、超时终止、crash-loop breaker、签名 Host transport 和 Supervisor 重启后的 operation
路由恢复。OCI adapter 保持失败关闭，待 Server 隔离部署批次接入容器执行，不会静默降级到宿主进程。

目标：从测试 Echo Host 演进为不依赖具体 Extension 的生产 Host。

### Host 模块

- package/manifest verifier；
- child-process 与 OCI runner；
- process lifecycle、heartbeat、crash loop breaker；
- resource/network/workspace/secret policy；
- operation registry 和有限期 event replay；
- Extension IPC：stdio JSONL，stdout 仅协议、stderr 仅日志；
- health、describe、invoke、reconcile、command 和 cancel adapter；
- OpenTelemetry trace/log/metrics exporter。

### 安装原则

- Host 安装只由部署脚本、Desktop 安装器或未来 Market installer 执行；
- Console 只能激活已被部署 allowlist 准入的精确包；
- 第一版不允许 Console 上传任意 tgz/npm 包；
- 开发可使用 child process，Shell、浏览器和不受信包生产必须用 OCI；
- Desktop 分发预构建 Host，不要求用户全局安装 Node/npm。

### 验收

- 同时运行三个依赖版本不同的 Extension；
- 注入 crash、OOM、事件循环阻塞、网络断开，其他 Extension 不受影响；
- restart 后 operation 可 reconcile/checkpoint 恢复；
- Host/Extension 升级失败时旧版本继续服务。

## 9. D5：Host Artifact 安全入口

状态：已实现（2026-08-15）。Owner 在冻结 operation 范围内签发一次性 grant；数据库只保存 token
fingerprint。上传固定绑定 user/run/task/action/reconciliation/operation、媒体类型、字节数、SHA-256 和短期
过期时间。API 仅流式校验并提交 content-addressed object，Execution Worker 使用独立 lease/fencing 和三次
重试上限物化不可变 Runtime Artifact。token 在上传事务提交时即失效。

目标：Host 返回大文件时不直接信任路径或任意 URI。

### Runtime 修改

- 新增一次性 `artifact_upload_grants`，数据库只保存 token fingerprint；
- grant 绑定 `user_id/run_id/task_id/action_id/operation_id`；
- 固定媒体类型、最大字节、期望 SHA-256 和到期时间；
- API 只负责流式接收并写对象存储，不执行模型或长任务；
- commit 后由 Worker 物化 Runtime Artifact 并关闭 grant；
- Host Profile 拒绝未经 grant 验证的 `file://`、本地路径和任意公网 URI。

小型 JSON/text 仍允许在受 `max_response_bytes` 约束的 reconcile 响应中内联。

### 验收

- token 只可使用一次，跨用户/跨 operation 使用失败；
- 大小、媒体类型或 SHA-256 不匹配时不创建 Artifact；
- 中断上传可清理，过期 grant 可回收；
- Artifact 能关联原始 Action、Host Extension build 和 Trace。

## 10. D6：OpenCLI 本地浏览器 Pilot

状态：Host Pilot 已实现（2026-08-16）。`extensions/capability-opencli/` 已固定 OpenCLI 1.8.6、上游
Manifest digest、npm integrity、入口摘要和 Node 24.19.0 LTS；Pilot Catalog 当前只准入 GitHub 当前账号、
X 搜索和需审批的 X 发帖。Catalog compiler、严格 Schema/argv、显式 Profile、异步 operation、进度事件、
有限读恢复、写操作人工复核、真实 Node 24 启动和 Browser Bridge 诊断均已验证。Desktop 安装/激活界面、
设备桥和下载文件的 grant 命令投递分别归入 D7、D10 与 D12，不伪装为本阶段已完成。

目标：用一个真实、已有广泛 Node 生态依赖的项目验证 Host，而不是把通用 Playwright 封装当作业务成果。
[OpenCLI](https://github.com/jackwener/opencli) 已提供结构化命令 Manifest、JSON 输出、明确退出码和
Browser Bridge，可连接用户已登录的 Chrome；JoyhouseBot 只负责把准入命令变成受治理 Capability。

### 新增范围

- `extensions/capability-opencli/`：官方 Node Invocation Extension；
- OpenCLI catalog compiler：把上游 `cli-manifest.json` 编译为待审核的 Capability definitions；
- 本地 Desktop preflight：Node/OpenCLI 版本、`opencli doctor`、Browser Bridge 和 Chrome Profile 状态；
- 命令执行、退出码、结构化结果、日志和下载 Artifact 映射测试；
- Console 的 OpenCLI 命令准入、账号/Profile 状态与风险摘要投影。

### Capability 冻结规则

不得发布 `opencli.execute(command: string)` 或允许 Agent 传入任意 argv。每个已准入命令冻结为独立能力：

```text
opencli.xiaohongshu.search@1.0.0
opencli.xiaohongshu.note@1.0.0
opencli.xiaohongshu.publish@1.0.0
opencli.twitter.search@1.0.0
opencli.twitter.post@1.0.0
```

编译映射如下：

| OpenCLI 字段 | JoyhouseBot 投影 |
| --- | --- |
| `site + name` | Capability ID 与 display name |
| `args` | 严格 input JSON Schema |
| `access: read/write` | 风险提示与默认审批基线，Runtime policy 可以提升但不能降低 |
| `domain` | Extension network allowlist |
| `strategy` | public/cookie/UI 等认证与人工接管要求 |
| `browser/siteSession` | 浏览器资源、Profile 与持久会话声明 |
| `columns` | 有界 output Schema；最终以 `-f json` 解析结果 |

上游 Manifest 只作为发布输入，不在每次调用时动态改变目录。发布 Revision 固定 OpenCLI 版本、命令
Manifest digest、Extension build、lockfile、SBOM 和允许命令集合；上游或插件目录变化必须形成新 Revision，
经过 preflight、Worker ACK 和显式激活后才能生效。

### 执行与错误映射

- Extension 使用 `spawn(executable, argv, {shell: false})`，不拼接 Shell 字符串；
- stdout 只解析 `-f json` 结果，stderr 进入脱敏日志；两者均设置字节上限；
- `0` 成功，`66` 映射为空结果成功，`69` 映射 Browser Bridge 不可用；
- `75` 是可重试临时失败，`77` 进入等待用户登录/接管，`78` 是不可自动重试配置错误；
- Runtime cancel 向子进程发送受控终止，`130` 映射为 cancelled；
- 图片、视频、文章、截图和其他下载只能通过 D5 Artifact upload grant 回写；
- 相同 `action_id/idempotency_key/request_digest` 不得再次执行发帖、关注、评论、删除等副作用。

### 用户与浏览器会话

- Runtime 只保存 `user_id + device_id + browser_profile_ref`，不上传或持久化浏览器 Cookie；
- Profile 必须由用户选择或拥有唯一默认值，多个 Profile 时禁止猜测；
- 前台登录、验证码、支付或其他必须人工观察的步骤进入 `waiting_runtime` 并通知本机用户；
- 只读查询可按策略自动执行；发布、评论、私信、关注、删除、购买和账号修改默认要求 Approval；
- OpenCLI 的 `browser eval`、任意 URL、`external register` 和运行时插件安装默认不发布给普通 Agent。

### 安装与供应链约束

- Desktop 随安装包提供精确 Node runtime、Host、OpenCLI 和官方 Extension，不要求用户全局安装 npm；
- Server 使用签名 OCI image 或不可变 bundle；
- 不执行 `plugin update --all`，不扫描用户任意目录作为生产 Capability；
- 第三方 OpenCLI plugin 必须由 Market/部署者固定来源、commit、依赖锁和签名后单独发布；
- OpenCLI Skill 可以转换为 JoyhouseBot Skill，但 Skill 不能携带或绕过 Capability 权限。

### 验收

- 至少一个公开只读命令、一个登录态只读命令、一个下载命令和一个审批写命令形成完整 Run；
- 未安装扩展、Bridge 离线、Profile 不明确、登录过期和上游输出漂移均给出可操作终态；
- 参数不能注入额外 flag/Shell，未准入命令和域名稳定拒绝；
- 两个用户或两个 Profile 的会话、结果和 Artifact 不交叉；
- 写操作重试不产生第二次副作用，回执能关联 Action、OpenCLI build 和目标对象；
- OpenCLI 升级失败时旧 Revision 继续可用并可以安全回滚。

当前 Host Pilot 已通过 Catalog 漂移、路径/flag 注入、JSON/空结果、`69/75/77/78/130`、Profile、幂等、
Worker IPC、供应链摘要和精确 Node 制品验证。完整用户验收中的下载 Artifact、跨设备隔离和 Console 激活需
等待 D7/D10/D12 的 Core-facing transport 与产品界面，仍作为发布阻塞项保留。

## 11. D7：Cloud Device Host Transport

实现状态：已完成。Core 已提供只存指纹的设备身份、精确 Host/Capability 清单、幂等 delivery outbox、
claim lease/fencing、进度证据、终态回写与撤销；`hosts/node/device-host` 已实现主动 HTTPS 领取、loopback
HMAC Supervisor 调用、续约与 reconcile。SSE 仍保持可选，正确性只依赖 PostgreSQL + HTTPS。

目标：让 Cloud Runtime 在不接收用户 Cookie、不要求本机开放公网端口的前提下，调用用户设备上的
OpenCLI、浏览器和未来本地 Node 能力。

### 传输设计

```text
Cloud Runtime -> fixed Device Relay -> durable delivery reference
                                      ^
                                      | outbound authenticated HTTPS claim/result
                              JoyHouse Device Host -> OpenCLI -> local Chrome
```

- Device Host 使用设备密钥或 mTLS 主动注册、心跳和领取任务；服务器不能任意扫描本机；
- 可选 SSE 只发送“有任务可领取”的有界通知，断线后回到 HTTPS claim/reconcile；
- Relay 只保存 operation、delivery cursor、deadline 和签名结果等传输投影，不拥有 Run/Task/Approval 终态；
- 每个调用固定 `user_id + device_id + host_revision + capability_revision + action_id`；
- 设备离线时 Task 保持 `waiting_external`，到期后按策略重试原设备或进入 `manual_required`；
- 需要前台登录或验证码时只在绑定设备提示，不把浏览器会话复制到 Cloud；
- Runtime 取消、Extension disable、设备撤销和账号退出必须阻止领取新任务。

### Runtime 与存储修改

- `device_host_registrations`：`user_id/device_id`、设备公钥或证书指纹、状态、Host Revision、能力摘要、
  最后心跳和撤销时间；设备 token 只保存指纹；
- `device_operation_deliveries`：绑定现有 reconciliation/operation/action、目标设备、request digest、
  delivery cursor、claim version/expiry、attempt、result digest 和有限终态；它只是传输 outbox，不复制 Task；
- 版本化设备 API：register/activate、heartbeat、operations:claim、events/results:append、complete 和 revoke；
- 可选设备事件 SSE 只返回有界通知 ID，Host 收到后仍通过签名 claim API 领取完整请求；
- claim 使用独立短 lease 和 fencing，过期 owner 不能提交事件或终态；
- Console、App 和 Agent 不能直接写 delivery 表或选择未授权设备。

设备故障转移不是默认行为。依赖本地登录态、文件或 workspace 的 operation 只能恢复到原设备；只有声明
为 portable、无副作用且目标设备拥有相同精确 Capability Revision 的调用，才能按已发布策略自动切换。

### 验收

- 家庭 NAT、IP 变化和短暂离线后可恢复同一 operation；
- 旧设备会话、撤销设备和错误用户不能领取或提交结果；
- 重复 delivery、SSE 重连和结果重投不产生重复副作用或重复 Artifact；
- stale claim version 不能推进 delivery cursor 或写入 operation 终态；
- Cloud 数据库和日志中不存在 Cookie、Chrome Profile 内容或 OpenCLI 本地凭据；
- 本地 Runtime 模式不依赖 Device Relay，Relay 故障不影响纯本地 JoyHouse。

## 12. D8：Host Model Gateway 与预算

状态：完成。

目标：Pi 等自带 Agent loop 的 Extension 在模型调用前受到 Runtime 治理。

### 架构

新增独立 `model-gateway` Worker/服务角色，而不是把 LLM 调用塞进公共 FastAPI 请求线程：

```text
Host Extension -> internal Model Gateway -> published Provider revision
                         |
                         `-> model_invocations / Run budget ledger
```

Runtime 为 operation 签发短期模型 grant，绑定用户、Run、Task、Extension、允许模型、token/cost、并发和
deadline。Gateway 每次调用前事务性保留预算，完成后按真实 usage 结算；缺失 billing 时 fail closed 或按
发布策略使用保守上界。

### 修改范围

- 新 `joyhousebot/model_gateway/` application/runtime/storage；
- 新 CLI role、systemd/Compose、本地启动和 readiness；
- 复用 Provider Registry、observability 和 `model_invocations`；
- Console 只展示状态、预算和用量，不显示密钥。

### 验收

- Host 不能调用未发布模型或超过预算；
- grant 撤销、Run 取消或 Extension disable 后拒绝新请求；
- 模型密钥不进入 Host、checkpoint、Event 或 Artifact；
- Gateway usage 与 Run 总账可对账。

## 13. D9：Pi Runner Pilot

状态：完成。

目标：验证真实长程 Node Agent，但控制范围，不提前引入动态 Tool Broker。

Pi 以普通 Capability 发布：

```text
coding.pi.execute@1.0.0
```

### 首期能力

- 输入是 Runtime 冻结并授权的 repository/workspace reference；
- 在隔离 worktree/container 中 inspect、edit 和运行 allowlist tests；
- 文件和 Shell 只作用于临时 workspace；
- 通过 Model Gateway 调用模型；
- 输出 patch、测试报告、日志摘要和截图 Artifact；
- operation/checkpoint 支持 Host、Pi 和 Worker 重启。

### 明确禁止

- 自动应用到用户主分支；
- commit、merge、release deploy 和任意外部写；
- 访问未声明仓库、域名或凭据；
- 把 Pi session 当作唯一恢复事实；
- 把隐藏推理写入 Event/Trace。

应用 patch、commit 和部署在 Pilot 后作为独立、审批受控的 Runtime Capability 实现。

### 验收

- 一个前端缺陷修复可生成可审阅 patch 和完整测试证据；
- 任一进程重启后恢复或安全进入 manual_required；
- 更换模型或未来替换 Pi 不影响 Run、Action、Artifact 和审计记录。

## 14. D10：动态 Runtime Tool Broker

状态：完成。正确性基线使用短期 Tool grant、PostgreSQL request queue 和结果轮询；SSE 仍为可选优化。

目标：允许 Pi 或其他 Host Agent loop 复用 Runtime 已发布能力，而不获得直接系统权限。

### 存储与身份

新增 `host_tool_requests` 映射：

- `host_request_id`、父 reconciliation/action/operation；
- 精确 Capability ref、input hash、状态；
- Runtime 冻结的 child action/invocation/idempotency identity；
- command delivery cursor 和最终 observation。

Tool request 创建独立 durable Action，通过现有 `CapabilityDispatcher`、Approval、write receipt 和
reconciliation 执行。Host 只能请求父 Agent/Scenario allowlist 与权限交集中的能力。

每个 Device delivery 最多创建 64 个不同 Tool request；重复 `host_request_id` 只返回原记录且不消耗
第二次预算。父 Capability 自身不能进入 `tool_access`，并且短期 grant 不会继续传给被调用 Tool，
因此动态调用深度固定为一层，不能形成 Host → Runtime → Host 的隐式递归。

### 状态流

```text
tool.requested
 -> validate and freeze child Action
 -> optional approval, release Runtime lease
 -> invoke/reconcile Tool
 -> persist observation
 -> deliver idempotent tool.completed/tool.failed command
 -> resume Host checkpoint
```

### 验收

- 重复 host request 不产生第二个 Action；
- 审批等待 24 小时不占 Worker lease；
- Host 不能扩大权限或替换 Capability build；
- command 重投不会让 Extension 再执行一次副作用。

## 15. D11：通用 Node 能力、Channel 和 Event Source

状态：协议基线完成。Node Manifest/SDK 已区分 Invocation、Channel 和 Event Source；具体 WhatsApp、
Playwright、Git worktree 与签名 Trigger 适配器按独立 Extension 迭代，不进入 Core 状态机。

完成 OpenCLI、Pi Pilot 和 Tool Broker 后扩展生态：

- `capability-browser-playwright`：用于 OpenCLI 尚未覆盖或 App 需要稳定自有流程的域名 allowlist、截图/视频证据；
- `capability-git-worktree`：checkout、diff、受控 apply/commit；
- `capability-preview-deploy` 与审批后的 release deploy/rollback；
- 云服务、内容、音视频和设备 SDK；
- WhatsApp Node driver 迁移到 SDK lifecycle，但保留 Python Channel adapter；
- GitHub/Webhook/消息队列 Event Source 使用签名 Trigger envelope。

Channel 不进入 operation reconciliation；Event Source 不直接调用 Agent，只产生可去重 Trigger。一个
Driver 故障不能影响 Invocation Extension。

## 16. D12：Console、安装器与分发

状态：Runtime 基线完成。Console 已提供 Device Host 治理，Model Gateway 已有本地、systemd 与 Compose
启动方式，发行包固定 Node v24.19.0 LTS 并校验 OpenCLI 锁。JoyHouse Desktop 将这些制品复制到应用私有
资源目录、写入 Keychain 和实现安装修复 UI，属于相邻闭源产品仓库，不进入 Runtime Core。

### Console

- Remote Connection 页面增加 Extension Host Profile、preflight 和摘要；
- Run 详情增加 operation progress、events、checkpoint、失败和人工对账；
- 集成中心增加已部署 Host、Extension、Profile、权限、资源和版本；
- OpenCLI 页面展示可导入命令、冻结 Capability、读写风险、目标域名、Browser Bridge、设备与 Profile 状态；
- Device Host 页面支持设备命名、在线状态、能力摘要、最后心跳、撤销和本机接管提示；
- 模型页增加 Model Gateway readiness 和 Host usage；
- 所有 UI 只查询 Runtime PostgreSQL 投影，不直连 Host。

### 部署

- `scripts/start-local.sh` 可选启动 Node Host 和 Model Gateway；
- systemd/Compose 增加独立 Host、Device Relay、Model Gateway 服务、readiness、日志和升级顺序；
- macOS Desktop 打包精确 Node runtime、Host bundle、OpenCLI 和官方 Extension；
- Server 优先运行 OCI Extension；
- Market 只分发签名包、授权和版本元数据，不读取用户 Run/Artifact/checkpoint。

## 17. 推荐提交批次

每个批次保持可测试、可回滚：

1. `docs(extension-host): freeze host profile and test vectors`
2. `feat(node-sdk): add signed remote capability sdk and echo host`
3. `feat(remote): add extension host profile and safe preflight`
4. `feat(operations): persist bounded host progress events and cursors`
5. `feat(node-host): add isolated extension supervisor`
6. `feat(artifacts): add scoped host artifact upload grants`
7. `feat(opencli): add curated browser capability pilot`
8. `feat(device-host): relay cloud operations to authenticated local devices`
9. `feat(model-gateway): govern hosted model calls and budgets`
10. `feat(pi): add isolated pi runner pilot`
11. `feat(host-tools): add governed runtime tool broker`
12. `feat(node-ecosystem): standardize channel and event-source drivers`
13. `feat(console): manage hosts, browser profiles, operations and hosted usage`
14. `build(distribution): package desktop/server host runtimes`

任何批次都不得依赖尚未提交的下一批次才能保持现有 Runtime 可用。

## 18. 发布里程碑

### Milestone A：Polyglot Capability Alpha

包含 D0-D4：第三方开发者能够用 Node SDK 实现同步/异步 Capability，部署者能以精确包和权限运行，
Runtime 能发布、调用、对账、观察进度和回滚。这个里程碑不包含 Pi、模型代理和动态 Tool Broker。

### Milestone B：OpenCLI Desktop Pilot

包含 D5-D6：Desktop 可以激活一组冻结的 OpenCLI Capability，复用用户选择的本地 Chrome Profile，完成
只读查询、下载 Artifact 和受审批写操作。这个里程碑不要求 Cloud 能访问用户本机浏览器。

### Milestone C：Connected Device Beta

包含 D7：Cloud Runtime 可以把已授权调用安全投递给在线 Device Host，设备离线、撤销和重连不破坏 Run
最终一致性，本地凭据与浏览器 Cookie 不离开用户设备。

### Milestone D：Hosted Agent Pilot

包含 D8-D9：模型预算和 Pi Pilot 形成闭环。对外只承诺“生成可审阅 patch 和证据”，不承诺自动
commit、merge 或发布。

### Milestone E：Composable Node Ecosystem Beta

包含 D10-D12：Host Agent loop 可以受治理地调用 Runtime Capability；Channel、Event Source、Console、
Desktop/Server 打包和 Market 分发具备稳定契约。

每个 Milestone 必须能独立安装和回滚，不能通过 feature flag 暗中启用未完成的下一里程碑能力。

## 19. 风险与决策门

| 风险 | 决策门 |
| --- | --- |
| OpenCLI 上游 Manifest 或输出漂移 | D6 固定版本、命令 Manifest digest 和 JSON fixtures；漂移形成新 Revision，不热更新生效定义 |
| OpenCLI plugin 或外部 CLI 扩大宿主机权限 | 禁止动态扫描、自动更新和 `external register`；仅运行 Market/部署 allowlist 精确包 |
| 用户 Chrome 登录态泄露或串号 | Cookie 不离开设备；调用绑定 `user_id + device_id + profile_ref`，多 Profile 禁止猜测 |
| Cloud 无法访问 NAT 后的本地 Host | D7 使用设备主动出站领取与可选 SSE 唤醒；不要求本机开放公网端口 |
| Pi RPC/API 或许可证不适合再分发 | D9 前固定版本、许可证、RPC fixtures；不满足则替换 Runner，Host 架构不变 |
| Desktop 无 OCI，隔离能力弱于 Server | Alpha 只允许无 Shell/低风险包；Pi 必须使用受控子进程与专用 workspace，正式写操作保持关闭 |
| Model Gateway 并发扣费超预算 | D8 必须先实现事务性预算 reservation，再允许真实 Pi 模型调用 |
| 动态 Tool Broker 形成递归调用或死锁 | D10 限制最大嵌套深度、父子 Action 图和循环检测，并独立设置预算 |
| Host replay buffer 被误当事实源 | 所有终态必须落 PostgreSQL；buffer 丢失走 reconcile/unknown，不推断成功 |
| npm 供应链污染 | 精确 bundle、lockfile digest、SBOM、签名、allowlist；正式环境禁止运行时 npm install |
| Host 本地端口被其他进程调用 | HMAC/mTLS、回环绑定、固定 base URL、nonce 和最小密钥作用域 |
| Channel 与 Invocation 状态混用 | 分开 Profile 和测试套件；WhatsApp 不写 operation reconciliation |

## 20. 全程质量门槛

每个阶段至少执行：

- Python unit/integration/PostgreSQL tests 与 Ruff；
- Node unit tests、TypeScript typecheck、lockfile reproducibility；
- canonical signature 和 Schema contract tests；
- duplicate、conflicting idempotency、timeout、cancel、restart、cursor replay；
- stale reconciliation/Run/Task lease fencing；
- secret redaction、network/workspace 限制、跨用户隔离；
- rollout、preflight、disable、rollback；
- Artifact digest、Tool approval/write receipt 和模型 usage 对账；
- OpenCLI catalog fixture、argv 注入、退出码、Profile 隔离、登录接管和上游输出漂移；
- Device Host 注册、撤销、离线、重复 delivery、SSE 重连和跨用户领取拒绝；
- Console build 和 release candidate check。

## 21. 完成定义

Node 生态支持完成的标准不是“Pi 或 WhatsApp 能启动”，而是：

> 在不修改 JoyhouseBot Core 业务逻辑的情况下，可以部署 OpenCLI 或另一个精确签名的 Node Extension；它在独立
> 故障域中运行，使用 Runtime 的身份、权限、预算、审批、Artifact、审计和恢复机制，并能安全升级、
> 禁用和回滚。
