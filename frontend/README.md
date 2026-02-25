# Joyhousebot 配置界面 (Config UI)

Vue 3 + Naive UI 的配置前端，用于在浏览器中修改 joyhousebot 的 Agent、API 提供商、通道等配置。

## 开发

```bash
cd frontend
npm install
npm run dev
```

开发时 Vite 会代理 `/config`、`/health`、`/chat`、`/v1` 到 `http://127.0.0.1:18790`，请先启动：

```bash
joyhousebot gateway --port 18790
```

若服务端启用了 HTTP API 认证（`gateway.controlToken`），需配置环境变量 `VITE_HTTP_API_TOKEN` 与之一致（例如在 `.env` 或 `.env.local` 中设置），否则请求会返回 401。

然后在浏览器打开 http://localhost:5174/ui/

## 构建并打入 Python 包

在**仓库根目录**执行：

```bash
./scripts/build-and-package.sh
```

可选：构建后顺带可编辑安装，执行 `./scripts/build-and-package.sh --install`。

若只需更新前端静态资源、不重新打 wheel，可只执行：

```bash
./scripts/build-ui.sh
```

**打包流程简述**：

1. **build-ui.sh**：在 `frontend/` 下执行 `npm ci` 与 `npm run build`，将生成的 `frontend/dist/*` 复制到 **joyhousebot/static/ui/**。
2. **Python 包**：`pyproject.toml` 的 `[tool.hatch.build] include` 包含 `joyhousebot/static/**`，因此 `hatch build` 或 `pip install -e .` 会把 `joyhousebot/static/ui/` 打进 wheel/安装目录。
3. **运行时**：gateway 启动时，`joyhousebot.api.server` 将 `joyhousebot/static/ui` 挂载到路径 **/ui**，故访问 **http://&lt;host&gt;:&lt;port&gt;/ui/** 即可使用配置界面。

启动 `joyhousebot gateway` 后访问 **http://&lt;host&gt;:&lt;port&gt;/ui/** 即可使用。

## 技术栈

- Vue 3、Vue Router、Pinia
- Naive UI、unplugin-vue-components (NaiveUiResolver)
- Vite 5、TypeScript

与 [joy-house-front](../joy-house-front) 的依赖风格一致，便于后续扩展或复用组件。
