# Android Device Host

状态：Phase 0/1 已实现（probe、Runtime 侧 capability 与自动投递）；Phase 2 Kotlin App 开发中。

## 1. 定位与边界

Android Device Host 让一台 Android 11+ 真机成为 Porthouse 的受治理执行器：Agent 调用
`android.observe` / `android.actuate`，Runtime 冻结 Action（actuate 强制人工审批）、把
operation 自动投递到配对手机（见 `DEVICE_HOST_TRANSPORT.md` 的自动投递一节），手机上的
Device Host App 领取并在本地执行，证据（截图、事件）回流同一 Run 审计链。

三层职责，不可混同：

| 层 | 组件 | 职责 |
|---|---|---|
| Runtime | `extensions/capability-android-device` | 冻结请求为 accepted operation；不碰设备 |
| 传输 | Device Host Transport（现有） | 注册/心跳/claim/fencing/完成，出站 HTTPS |
| 设备 | `hosts/android/device-host`（Kotlin） | Shizuku 执行固定 op 映射，回传有界证据 |

治理不变量：

- `android.actuate` 声明 `side_effect=external`，任何点击/输入/启动 App 在冻结前就要通过
  人工审批；设备永远收不到未批准的操作（自动投递只路由已批准的冻结 operation）。
- `android.observe` 为 `side_effect=internal`：自动放行但仍是 durable action，因此可进入
  投递链，证据进入审计。
- 设备端执行器只有固定 op→argv 映射，**没有任意 shell 通道**；`launch_app` 包名校验、
  `input_text` 受限字符集、`press_key` 白名单，全部与 Phase-0 probe 契约一致。

## 2. 共享 op 契约

单一契约三处对齐，测试互相钉死（`tests/test_android_device_extension.py` 与
`hosts/android/probe/fixtures/op_contract.json` 防漂移）：

- observe：`ui_dump`（压缩节点表，文档序，默认上限 200 节点）、`screenshot`、
  `screen_state`、`current_app`
- actuate：`tap`、`swipe`、`input_text`、`press_key`、`launch_app`、`wake`
- envelope：`{"ok", "op", "elapsed_ms", "serial", "result"|"error"}`，错误码 fail-closed

## 3. 手机端 App（Phase 2）

Kotlin 单 App（minSdk 30），放 `hosts/android/device-host/`：

- **传输层**：移植 Node device-host 状态机——注册（`jhd_` token 存 Android Keystore）、
  前台服务轮询 heartbeat/claim/renew/events/complete、`claim_session_id` 每进程随机、
  断线退避、401 即停。限制对齐：result ≤4MiB、事件 payload ≤32KiB、lease 10–300s。
  设备 header 为 `X-Porthouse-Device-ID`。
- **执行层**：Shizuku UserService（shell uid 2000，Android 11+ 无线调试自启、免 root）
  执行 op 契约；截图压缩为 JPEG 内联返回；锁屏/不可用返回 `manual_required`，不猜成功。
- **开发拓扑**：Runtime 本机 LAN HTTPS（自签 CA），debug 构建钉 CA；生产走正式证书。
- **治理 UX**：审批在 Console `/action-items` 与既有 channel 可见；App 对每个领取的
  delivery 显示前台通知（操作摘要 + 取消入口）。

注册示例（capabilities 与扩展声明的精确版本一致）：

```json
{
  "device_id": "pixel-a",
  "host_revision": "android-host@1.0.0+build-a",
  "host_manifest_digest": "sha256:<64hex>",
  "capabilities": [
    {"capability_id": "android.observe", "version": "1.0.0",
     "implementation_digest": "sha256:<64hex>", "portable": false},
    {"capability_id": "android.actuate", "version": "1.0.0",
     "implementation_digest": "sha256:<64hex>", "portable": false}
  ]
}
```

## 4. 部署顺序

1. `config.json` 的 `extensions.allowedIds` 加入 `capability-android-device`（发布走
   staged rollout → Worker ACK → 生效）。
2. Console/API `POST /v1/device-hosts` 注册手机，token 一次性返回，粘进 App。
3. Scheduler Worker 常规运行即可（自动投递循环随 `SchedulerWorker` 启动）。

## 5. Phase 0：Mac 侧 probe（开发工具）

`hosts/android/probe/android_probe.py` 用本机 `adb` 对真机验证同一契约，不进入
Capability Registry、不被任何 Run 触达。生成 `fixtures/` 下的 golden 数据供三端测试共用。

## 6. 已知限制与后续

- 大证据（>4MiB）暂走内联压缩；设备侧 artifact-grant 端点属 Phase 3 core 扩展。
- 控制动作沿用固定 allowlist；`diagnose_android` 需扩 `_CONTROL_ACTIONS` 与 DB CHECK。
- 多设备/云手机：注册多台即可，自动投递按默认设备优先选择；`portable` 语义未启用。
- 合规边界：actuate 默认人工审批不可关闭；本能力不得用于在第三方平台模拟真人批量触达。
