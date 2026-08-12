# JoyhouseBot HTTP Capability Connector

通用远程业务能力连接器。企业程序保持自己的 UI、身份、组织权限、业务数据库和事务，只把经过保护的
业务操作暴露为版本化 Capability。连接器在 Agent Worker 内注册这些声明，调用继续经过 JoyhouseBot
的权限、配额、审批、Action、幂等、对账、Trace 和 Artifact 链路。

它不会加载企业代码、连接企业数据库、接受模型提供的 URL，也不会创建第二套 Run/Task 状态机。

安装：

```bash
uv pip install -e extensions/connector-http-capability
```

在 `extensions.enabled` 中加入并发布 `connector-http-capability`。随后从 Console 的“集成中心 →
远程能力”创建服务 Revision、`env://` 密钥引用和 Capability 目录；发布会执行逐 Worker 预热，配置不会
依赖首个 Run 才加载。完整协议与控制面流程见
[`docs/REMOTE_CAPABILITY_PROTOCOL.md`](../../docs/REMOTE_CAPABILITY_PROTOCOL.md)。

远端企业程序只实现：

```text
POST /capabilities/{capability_id}:invoke
POST /operations:reconcile
```

生产必须使用 HTTPS。共享签名密钥只能通过 `env://VARIABLE` 注入。HTTP 仅允许显式开启的本机回环地址，
用于开发测试。
