# joyhousebot 文档

## 文档结构

### 根目录

设计与架构、异常处理等通用文档。

- [设计与架构](DESIGN_AND_ARCHITECTURE.md) — 分层设计、核心组件、执行流程、安全与扩展点
- [异常处理](ERROR_HANDLING.md) — 异常类层次、错误分类、敏感信息过滤
- [CLI 参考](CLI_REFERENCE.md) — 命令与参数说明

### 按语言分目录

用户向使用说明按中英文分开，便于打包成在线文档并做语言切换。

- **`zh/`** — 中文说明
  - [Python 原生插件开发](zh/NATIVE_PLUGIN_GUIDE.md)
  - [记忆 vs OpenClaw](zh/MEMORY_VS_OPENCLAW.md)
  - [Agent 自主决策配置](zh/AGENT_AUTONOMOUS_DECISION.md)
  - [DNS 工具使用指南](zh/DNS_GUIDE.md)
  - [Memory 命令使用指南](zh/MEMORY_GUIDE.md)
