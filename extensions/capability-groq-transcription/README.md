# joyhousebot Groq Transcription Capability

可选音频转写 Capability。安装后提供 `media.transcribe.groq@1.0.0`，只有获得
`media.transcribe` 与 `filesystem.read` 权限的 Agent 才能调用。

```bash
uv pip install -e extensions/capability-groq-transcription
```

通过 `GROQ_API_KEY` 环境变量注入密钥。Channel 只产生媒体输入，不会隐式调用本 Capability；
是否转写必须进入正常 Capability allowlist、权限、Run/Task/Trace 和成本审计链。
