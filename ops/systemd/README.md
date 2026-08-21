# joyhousebot systemd units

四个进程角色与 `docker-compose.runtime.yml` 一一对应：

- `joyhousebot-api.service` — HTTP/SSE 网关（`api --surface combined`）
- `joyhousebot-worker.service` — 分布式 Run/Task worker
- `joyhousebot-scheduler.service` — 调度 / DAG / 超时恢复
- `joyhousebot-channel-worker.service` — 独占外部通道连接并提交 Run

## 前置准备

所有 unit 以专用非 root 用户运行（与 Dockerfile 的非 root 加固一致），启用前先创建用户并授权目录：

```bash
useradd --system --home-dir /var/lib/joyhousebot --shell /usr/sbin/nologin joyhousebot
install -d -o joyhousebot -g joyhousebot /var/lib/joyhousebot
chown -R joyhousebot:joyhousebot /opt/joyhousebot
install -d -m 0700 -o root -g root /etc/joyhousebot   # joyhousebot.env 仅 root 可读，systemd 加载时读取
```

`EnvironmentFile=/etc/joyhousebot/joyhousebot.env` 至少提供 `JOYHOUSEBOT_DATABASE_URL` 与 LLM/Channel 凭据。
注意 channel-worker 只提交 Run、不调用 LLM 供应商：如共享 env 文件，请知悉其中的 provider key 对它并非必需；
若需最小化暴露，可为该 unit 单独使用 `EnvironmentFile=-/etc/joyhousebot/channel-worker.env` 覆盖。

## 安装

```bash
install -m 0644 joyhousebot-*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now joyhousebot-api joyhousebot-scheduler joyhousebot-worker joyhousebot-channel-worker
```

加固项采用基础值：`NoNewPrivileges`、`PrivateTmp`、`ProtectSystem=full`、`ProtectHome=true`，
可写路径通过 `ReadWritePaths=/opt/joyhousebot /var/lib/joyhousebot` 显式放开。应用如需写其它路径
（如自定义日志目录），在对应 unit 追加 `ReadWritePaths=` 即可。
