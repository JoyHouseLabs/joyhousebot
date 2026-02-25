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
  - [OpenClaw 插件接入](zh/OPENCLAW_PLUGIN_HOST.md)
  - [记忆 vs OpenClaw](zh/MEMORY_VS_OPENCLAW.md)
- **`en/`** — English
  - [OpenClaw Plugin Host](en/OPENCLAW_PLUGIN_HOST.md)

同一主题在不同语言下使用相同文件名，例如：
- `zh/OPENCLAW_PLUGIN_HOST.md` / `en/OPENCLAW_PLUGIN_HOST.md`

在线文档站点可据此映射为 `/zh/openclaw-plugin-host`、`/en/openclaw-plugin-host` 等路由。
