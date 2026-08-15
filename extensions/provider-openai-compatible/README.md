# JoyhouseBot OpenAI-compatible Provider

可选 OpenAI-compatible Chat Completions 模型扩展，声明 OpenAI、OpenRouter、DeepSeek、
Gemini compatible API、智谱、DashScope、Moonshot、MiniMax、Groq、Ollama、vLLM、AiHubMix 和自定义端点。

供应商请求、SSE、tool/reasoning 格式转换位于本包；路由、fallback、成本、Trace、原始响应权限
和审计仍由 Core 负责。

```bash
uv pip install -e extensions/provider-openai-compatible
```

本机 Ollama 使用免凭据的回环地址 `http://127.0.0.1:11434/v1`，模型目录中的完整 ID 使用
`ollama/<ollama-model-tag>`，例如 `ollama/qwen3:1.7b`。HTTP 仅允许回环地址，并需在 Provider Revision
中显式设置 `allow_insecure_http=true` 与 `credential_mode=none`。

密钥必须使用 `env://VARIABLE` 或供应商对应的环境变量注入。自定义和 vLLM 端点必须显式配置
`api_base`。

DeepSeek V4 的思考模式由 Agent Revision 显式控制：`reasoning_effort=none` 会映射为
`thinking.type=disabled`；`low`/`medium` 映射为 `high`，`xhigh` 映射为 `max`。
