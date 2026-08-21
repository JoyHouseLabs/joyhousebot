# joyhousebot Runtime Console

控制台用于 joyhousebot 的平台运行、管理、监控与配置，同时提供 Agent 在线试用。它只消费版本化 `/v1` HTTP API、`/healthz`、`/readyz` 和 Run SSE。

## 页面

- `/ui/overview`：API/Store 健康、平台用量、用户/会话、Worker 和最近 Run。
- `/ui/runs`：全局 Run 列表及模型调用、原始推理真实性分级、Span 性能瀑布、完整 Trace Blob、回放、Task、Tool、Log、Artifact、子 Agent 和输入/输出详情。
- `/ui/chat`：选择平台 Agent，提交持久 Run，查看可恢复的执行时间线和会话历史。
- `/ui/scenarios`：场景路由、追问 DAG 与执行策略。
- `/ui/platform`：管理员授权、Worker、Agent、Capability 和脱敏配置摘要。

平台页面要求数据库 `platform_admins` 权限。Agent 试用仍按当前 `user_id` 隔离，管理身份不会改变业务资源归属。
原始推理和完整模型 payload 还分别要求 `reasoning.read` 与 `reasoning.read_raw`；回放要求
`replay.execute`。供应商未返回 reasoning 时页面显示 unavailable，不会用摘要伪装原始思维。

## 开发

先按 Runtime 本地启动流程启动 PostgreSQL、API 和 Worker，再启动 Vite：

```bash
./scripts/start-local.sh
# 在另一个终端：
cd apps/console
npm ci
npm run dev
```

若只调试 Console API 适配，可改为自行启动 `uv run joyhousebot api --port 18790 --config ./config.json`；该命令仍需要已可用的 PostgreSQL 与有效配置，且不会启动 Agent Worker。

访问 `http://localhost:5178/ui/overview`。Vite 将 `/v1`、`/healthz` 和 `/readyz` 代理到 18790。无 Token 的开发模式默认使用测试用户 `joyhousebot`；后端仅在显式 insecure 模式把该用户引导为管理员。生产环境使用 Bearer Token。operator token 打开控制台时，同一个浏览器身份会作为 `X-Impersonate-User-ID`，普通用户 token 的服务端身份始终优先。

## 验证和打包

```bash
npm run typecheck
npm run build
./scripts/build-ui.sh
```

最后一条命令在仓库根目录执行，将产物同步到 FastAPI 挂载的 `joyhousebot/static/ui`。
