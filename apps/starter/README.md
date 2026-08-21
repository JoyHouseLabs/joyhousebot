# JoyHouseBot Starter

本地 fork 的 Owner Starter App。它是一个独立的 Vue 3 应用：Console 继续只承担运行时控制面；Starter 只通过版本化 HTTP API 提交个人目标、查看 Run/Artifact、处理输入与审批。最终 `final-output` 会直接作为 Agent 回复显示，其他 Artifact 与可追踪 Run 细节可展开查看。

## 开箱启动

1. 在仓库根目录创建或更新未提交的 `.env.local`，例如：

   ```bash
   DEEPSEEK_API_KEY=...
   ```

2. 启动 Runtime 和 Starter：

   ```bash
   JOYHOUSEBOT_CONFIG_PATH=config.dev.json ./scripts/start-local.sh
   ```

3. 打开 `http://127.0.0.1:5179`，进入“本地初始化”，填写 Provider、API Base、模型名称和密钥环境变量名，点击“保存并发布默认配置”。

Starter 默认选择 DeepSeek V4 Flash（`deepseek/deepseek-v4-flash`）：适合高频目标和长上下文；它使用 `https://api.deepseek.com/v1` 与 `DEEPSEEK_API_KEY`。也可在页面切换到 OpenAI 或手动填写任何兼容 Provider。Starter 只将 `env://DEEPSEEK_API_KEY` 这样的引用提交给 Runtime，绝不提交或保存明文密钥。变更 `.env.local` 后必须重启 Runtime，让 API 和 Worker 都加载新环境。

该引导会创建/发布模型 Provider、`default` Agent 的新 revision，以及随 Agent revision 递增的 `app.personal-starter` 发布物，并安装给当前本地用户。默认开发身份为 `joyhousebot`；可在启动前用 `JOYHOUSEBOT_DEV_USER_ID` 覆盖。它仅适合启用 `allowInsecureAuth` 的本地开发。生产环境请由 Operator 配置模型和 App 发布物；Owner 应用使用 Owner token 调用 `/v2`，不应调用 `/control`。

## 单独启动

```bash
npm --prefix apps/starter install
npm --prefix apps/starter run dev
```

开发服务器会把 `/v2`、`/control`、`/healthz` 和 `/readyz` 反向代理到本机 Runtime 的 18790 端口。常规的 `scripts/start-local.sh` 会自动安装缺失依赖、构建 Vue 产物，并用 loopback 静态服务器提供它；因此浏览器不需要 CORS 例外。
