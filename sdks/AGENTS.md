# JoyhouseBot SDK 协作说明

本目录只保存跨语言公开契约和客户端工具，不承载 Runtime 状态机、业务 App 或供应商实现。

- SDK 必须消费仓库中的共享协议 Schema 与测试向量，不能自行发明另一套签名、幂等或错误语义。
- SDK 不连接 PostgreSQL，不接收 Runtime Store，也不提供绕过 Capability Dispatcher 的入口。
- 公共 API 需要语义版本、类型检查和跨语言契约测试；破坏性变更必须升级协议或主版本。
- 日志不得输出密钥、完整认证头、Cookie、用户输入 Blob 或隐藏推理。
- SDK 依赖必须锁定；生产使用精确 bundle，不在运行期自动安装依赖。
