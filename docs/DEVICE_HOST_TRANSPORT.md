# Device Host Transport

状态：Core/API/Node client 已实现（2026-08-16）

## 1. 目的与边界

Device Host Transport 让 Cloud Runtime 把已经完成身份、权限、Capability allowlist、审批与 Action 冻结的
operation 投递到用户自己的 Mac/PC。设备主动发起 HTTPS 请求，因此家庭 NAT、动态 IP 和防火墙不要求
开放入站端口。

它不是新的执行引擎：

- PostgreSQL 中既有 Run、Task、Action、reconciliation 仍是最终事实源；
- `device_operation_deliveries` 只是 delivery outbox 和 claim fencing 投影；
- Device Host 不创建 user、Run、Task、Approval 或 Work；
- Node Supervisor 仍通过精确 Capability Revision 执行本机 Extension；
- Cloud 不接收 Cookie、Chrome Profile、浏览器存储、本机文件密钥或 local Host HMAC secret。

```text
Runtime Action + reconciliation
             |
             v
device_operation_deliveries (PostgreSQL)
             ^
             | authenticated HTTPS claim / heartbeat / result
JoyHouse Device Host --> loopback HMAC --> Node Supervisor --> OpenCLI/Extension
```

## 2. 身份与密钥

用户通过正常 Runtime 身份调用 `POST /host/v1/device-hosts`。响应只返回一次 `jhd_...` 设备 token，Runtime
数据库只保存 SHA-256 指纹。Desktop 应把原始 token 写入 macOS Keychain、Windows Credential Manager 或
等价系统密钥库，并仅通过环境变量注入 Device Host 子进程。

设备 API 同时要求：

- `Authorization: Bearer <device-token>`；
- `X-JoyHouseBot-Device-ID: <registered-device-id>`；
- 数据库中设备仍为 `active`；
- 心跳中的 Host revision 与 manifest digest 和注册值完全相同。

轮换 token 会立即使旧 token 失效。撤销设备会删除可领取资格、取消其未完成 delivery，并让既有
reconciliation 回到 Runtime 的重试/人工确认路径。

## 3. 数据模型

Core 只新增三个规范化事实投影：

- `device_host_registrations`：用户、设备、token/public-key 指纹、精确 Host revision、manifest digest、
  在线时间、默认设备和撤销状态；
- `device_host_capabilities`：设备实际加载的 Capability ID、版本、implementation digest 与 portable 声明；
- `device_operation_deliveries`：绑定 reconciliation、Run、Action、invocation、operation、目标设备、精确
  Host/Capability revision、请求摘要、deadline、claim lease/version 和结果摘要；
- `device_operation_delivery_events`：有界、去重、单调序号的设备执行事件。

没有 `tenant_id`，所有读取和状态变更均用 `user_id + device_id` 约束。表之间不复制 Run/Task 状态机。

## 4. API v1

用户控制 API：

| Method | Path | 用途 |
|---|---|---|
| `POST` | `/host/v1/device-hosts` | 注册精确 Host 与 Capability 清单，返回一次 token |
| `GET` | `/host/v1/device-hosts` | 查询自己的设备、在线时间和能力摘要 |
| `POST` | `/host/v1/device-hosts/{id}/token:rotate` | 轮换 token |
| `DELETE` | `/host/v1/device-hosts/{id}` | 撤销设备并停止新领取 |
| `POST` | `/host/v1/runs/{run}/operations/{reconciliation}/device-deliveries` | 为已冻结 operation 建立幂等交付 |
| `GET` | `/host/v1/device-deliveries/{id}` | 查询交付状态，不返回私有执行输入 |
| `GET` | `/host/v1/device-deliveries/{id}/events` | 查询执行证据 |

设备 API：

| Method | Path | 用途 |
|---|---|---|
| `POST` | `/host/v1/device-host/heartbeat` | 校验设备和精确 Host build，更新在线时间 |
| `POST` | `/host/v1/device-host/operations:claim` | 使用 `SKIP LOCKED` 批量领取并取得 fencing version |
| `POST` | `/host/v1/device-host/operations/{id}:heartbeat` | 在未过期 lease 上续约 |
| `POST` | `/host/v1/device-host/operations/{id}/events:append` | 幂等追加有界进度事件 |
| `POST` | `/host/v1/device-host/operations/{id}:complete` | 提交有摘要的终态并回写原 reconciliation |

SSE 唤醒是可选优化，目前正确性只依赖 PostgreSQL + HTTPS claim。断线后轮询恢复，不需要常驻 WebSocket。

### 自动设备投递

Scheduler Worker 的 `DeviceHostService.auto_enqueue_pending` 周期扫描（默认每 5 秒，
`JOYHOUSEBOT_DEVICE_DELIVERY_INTERVAL` 可调）：对状态仍为 `pending`/`manual_required`、
尚无 delivery、且其精确 `capability_id+version` 被某台 `active` 设备声明过的 reconciliation，
自动为目标设备（默认设备优先）创建幂等 delivery（deadline 1 小时、尝试 3 次后回到人工路径）。
候选 action 在冻结前都已通过能力审批，该 pass 只选择执行者，不产生新的治理决定。
无匹配设备时维持既有手动 reconciliation 路径，失败关闭。手动 API 创建 delivery 的语义不变，
两者由 `UNIQUE(reconciliation_id)` 保证幂等互斥。

## 5. Fencing、幂等与恢复

领取会增加 `claim_version` 并绑定随机 `claim_session_id`。事件、续约和完成必须同时匹配 device、session、
version，且 lease 尚未过期。旧进程即使保留 token，也不能用旧 version 推进 cursor 或写入终态。

设备完成先以 `result_digest` 幂等落到 delivery，再回写既有 `complete_operation_reconciliation`。若两步间
进程退出，同一结果重投会继续完成 reconciliation；不同结果使用同一 delivery 会失败关闭。最终写入仍会
更新 Action observation、Capability invocation 并唤醒原 Run。

设备事件同时进入 delivery event、operation reconciliation event 和 Runtime Event 时间线；大文件不进入
事件或 JSON result，必须使用 scoped Artifact upload grant。

## 6. Node Device Host

`hosts/node/device-host` 是无状态出站客户端：

1. 从 `env://` 引用读取设备 token 与本机 Supervisor HMAC secret；
2. 心跳并领取 delivery；
3. 把 Runtime 冻结的标准 Invocation envelope 通过 loopback HMAC 发给 Supervisor；
4. 对 accepted operation 续约并 reconcile；
5. 上传有界事件和终态。

它和 Supervisor 一起使用 `hosts/node/runtime-lock.json` 的 Node `v24.19.0`，不探测用户全局 Node。纯本地
JoyHouse 可以直接调用 loopback Supervisor，不依赖 Cloud Device API。

## 7. 当前限制

- 自动 portable 故障切换尚未开放；默认始终固定原设备，避免本机登录态、文件和 workspace 被错误迁移；
- 可选 SSE 只计划作为唤醒优化，尚未提供；
- Desktop 的设备注册、Keychain 保存和状态页面属于 D12 产品集成；
- Pi 的模型预算 grant 属于 D8，动态反向 Tool Broker 属于 D10，不在本传输中偷渡实现。
Device delivery 可冻结 `model_access` 和 `tool_access`。Device Host 按当前 fenced claim 分别换取短期
`jhm_` 与 `jht_` grant，再通过签名本机 envelope 交给 Supervisor；设备总 token 不进入 Extension。
Tool grant 只能访问 `/host/v1/host-tool-requests`，并随 claim、设备或 Run 失效。
