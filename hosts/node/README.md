# joyhousebot Node Host

本目录承载通用 Node Extension Host 的运行环境契约，不承载某个业务 App。当前已经具备：

- `runtime-lock.json`：Desktop、Server bundle 和离线安装包唯一允许内置的 Node LTS 精确版本；
- `fixtures/echo-host`：签名 `meta:describe`、同步调用、异步 accepted/reconcile、幂等冲突和重启 unknown 的纵向契约 Host；
- `supervisor`：通用签名 Host、独立 Extension 进程、JSONL IPC、crash breaker 与可恢复 operation 路由；
- `device-host`：使用设备身份主动连接 Cloud、领取 fenced delivery，并通过 loopback HMAC 调用 Supervisor；
- `../../sdks/node`：Host/Extension 共享的签名、RFC 8785 request digest、防重放和有界 HTTP SDK。

## 打包约束

发行包使用 Node `v24.19.0` LTS，不探测或复用用户全局 Node。安装器按照目标平台从 Node 官方分发地址
获取 `runtime-lock.json` 中的文件，先执行：

```bash
python scripts/verify-node-runtime-lock.py \
  --platform darwin-arm64 \
  --archive /path/to/node-v24.19.0-darwin-arm64.tar.gz
```

校验成功后才能解包到应用私有资源目录。Node 不加入用户 `PATH`，Host 启动器使用绝对路径；每个
Extension 使用不可变 bundle/lockfile 和独立进程或 OCI 容器。升级 Node 必须修改 lock 文件、重新获取
官方 SHA-256，并通过 `scripts/test-extension-host.sh` 与预发布检查。

开发环境可以使用 `node >=20` 运行 SDK 和 fixture 测试；这只是贡献者工具链兼容范围，不等于发行包会
使用系统 Node。
