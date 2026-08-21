# joyhousebot Extension Host 协作说明

本目录保存独立语言运行环境的通用 Host、Supervisor 和契约测试 Fixture。

- Host 只是 Remote Capability 的执行与传输适配，不拥有 Run、Task、Approval、Artifact 或业务状态机。
- Host 和 Extension 不得访问 Runtime PostgreSQL、私有 Repository 或 Core 内部服务定位器。
- 每个生产 Extension 使用独立子进程或 OCI 故障域；不得加载任意 npm 包、脚本或宿主机 Shell。
- stdout 仅用于有界协议帧，日志写 stderr 并脱敏；所有输入、输出、资源、网络和执行时间必须有限制。
- Fixture 可以简化持久化，但必须明确重启后的 `unknown/manual_required` 行为，不能伪造成功。
