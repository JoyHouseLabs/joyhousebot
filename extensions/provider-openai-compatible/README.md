# JoyhouseBot OpenAI-compatible Provider

可选 OpenAI-compatible Chat Completions 模型扩展，声明 OpenAI、OpenRouter、DeepSeek、
Gemini compatible API、智谱、DashScope、Moonshot、MiniMax、Groq、vLLM、AiHubMix 和自定义端点。

供应商请求、SSE、tool/reasoning 格式转换位于本包；路由、fallback、成本、Trace、原始响应权限
和审计仍由 Core 负责。

```bash
uv pip install -e extensions/provider-openai-compatible
```

密钥必须使用 `env://VARIABLE` 或供应商对应的环境变量注入。自定义和 vLLM 端点必须显式配置
`api_base`。
