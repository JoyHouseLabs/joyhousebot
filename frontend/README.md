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

然后在浏览器打开 http://localhost:5174/ui/

## 构建并打入 Python 包

在**仓库根目录**执行：

```bash
./scripts/build-ui.sh
```

会将 `frontend/dist` 复制到 `joyhousebot/static/ui/`，随后 `hatch build` 或 `pip install -e .` 会将该静态资源包含进包内。启动 `joyhousebot gateway` 后访问 **http://&lt;host&gt;:&lt;port&gt;/ui/** 即可使用。

## 技术栈

- Vue 3、Vue Router、Pinia
- Naive UI、unplugin-vue-components (NaiveUiResolver)
- Vite 5、TypeScript

与 [joy-house-front](../joy-house-front) 的依赖风格一致，便于后续扩展或复用组件。
