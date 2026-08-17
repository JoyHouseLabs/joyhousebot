# Porthouse Android Device Host

Android 11+ 真机上的 Porthouse 受治理执行器。出站 HTTPS 直连 Runtime 的 Device Host
Transport：注册（Console 建档后粘贴一次性 `jhd_` token）→ 前台服务轮询
heartbeat/claim → Shizuku shell-uid 执行固定 op 契约 → 有界事件与结果回传。
协议、治理边界与部署顺序见 `docs/ANDROID_DEVICE_HOST.md`。

## 结构

```
app/src/main/java/dev/porthouse/host/android/
├── MainActivity.kt            # 配对 UI（URL/设备ID/token/revision/digest）
├── runner/
│   ├── DeviceHostForegroundService.kt  # 前台服务：心跳+领取循环、通知、WakeLock
│   ├── DeviceConfig.kt        # EncryptedSharedPreferences 存配对材料
│   └── OperationRunner.kt     # 单个 delivery 的执行状态机（纯逻辑，可测）
├── transport/
│   ├── Dto.kt                 # 传输 DTO 与大小限制（result ≤4MiB、事件 ≤32KiB）
│   ├── DeviceTransport.kt     # 传输接口（测试用 fake）
│   └── PorthouseTransport.kt  # OkHttp 实现；debug 可钉自签 CA（assets/runtime-ca.pem）
├── executor/
│   ├── OpSpec.kt              # 固定 op→argv 契约（与 probe/扩展三方对齐）
│   ├── Parsers.kt             # ui_dump/screen_state/current_app 解析
│   ├── DeviceExecutor.kt      # 执行器接口
│   ├── ShellUserService.kt    # Shizuku UserService（shell uid 2000）+ 绑定适配
│   └── JpegCodec.kt           # 截图 JPEG 压缩（降质/缩放守 4MiB 内联预算）
└── shizuku/IDeviceShellService.aidl  # 无任意命令入口，仅固定 argv + 截图
```

## 构建

```bash
# 需要 JDK 17 与 Android SDK（API 35）。首次生成 wrapper：
gradle wrapper --gradle-version 8.9
./gradlew :app:testDebugUnitTest   # JVM 单测（契约、解析器、runner 状态机）
./gradlew :app:assembleDebug
```

测试资源中的 `op_contract.json` / `window_dump.sample.xml` 从
`hosts/android/probe/fixtures/` 同步，`OpSpecTest` 用 golden 契约防漂移。

## 真机使用清单

1. Runtime 侧：`extensions.allowedIds` 加入 `capability-android-device` 并发布；
   Scheduler Worker 常规运行（自动投递随其启动）。
2. Console/API 注册设备（capabilities 填 `android.observe@1.0.0` 与
   `android.actuate@1.0.0`，`implementation_digest` 为 APK 构建 sha256），
   复制一次性 token。
3. 手机：安装 Shizuku 并以无线调试激活；安装本 App，填入 Runtime URL
   （LAN HTTPS 自签 CA 放 `assets/runtime-ca.pem`）与配对字段，Start。
4. 豁免电池优化，保证后台常驻；Actuator（actuate）操作会先在 Console
   `/action-items` 出审批卡，批准后手机才收到 delivery。

## 安全边界

- token 只存 Keystore 加密存储；Runtime 侧只留指纹。
- 执行器无任意 shell 通道：所有 op 都是固定 argv（见 `OpSpec`），包名/
  键名/字符集全部 fail-closed 校验。
- 锁屏、Shizuku 未激活、结果超限一律显式 failed/manual_required，不猜成功。
