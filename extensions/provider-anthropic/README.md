# Porthouse Anthropic Provider

可选 Anthropic Messages API 模型扩展。供应商请求、SSE、tool/reasoning 格式转换位于本包；
路由、fallback、成本、Trace、原始响应权限和审计仍由 Core 负责。

```bash
uv pip install -e extensions/provider-anthropic
```

API Key 必须通过 `env://ANTHROPIC_API_KEY` 或环境变量注入。
