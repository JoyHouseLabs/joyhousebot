# JoyClaw

JoyClaw 是 JoyhouseBot 面向个人用户的极简产品入口。它不是第二套 Agent Runtime：目标、自动化和外部事件仍提交到 JoyhouseBot 的统一 Run/Task 链路，Agent、模型、能力、安全策略和版本发布继续由 JoyhouseBot Console 管理。

## 本地启动

先启动 JoyhouseBot API，再启动本应用：

```bash
cd apps/joyclaw
npm install
npm run dev
```

打开 `http://127.0.0.1:5179/joy/`。Vite 会把 `/v1`、`/healthz` 和 `/readyz` 转发到 `127.0.0.1:18790`。

## 产品边界

- JoyClaw：一句话目标、执行状态、成果和个人自动化。
- JoyhouseBot Console：Agent、模型、能力、插件、评测、审计和平台治理。
- JoyhouseBot Runtime：唯一执行状态机和 PostgreSQL 事实源。

生产环境默认假设 JoyClaw、Runtime 和 Console 同源部署，JoyClaw 位于 `/joy/`，高级控制台位于 `/ui/`。
