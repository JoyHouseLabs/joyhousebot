# joyhousebot CLI 参考

本文档按「全局 → 顶层 → 按组子命令」列出 joyhousebot 的命令与主要参数。更多选项请运行 `joyhousebot <command> --help` 或 `joyhousebot <group> --help`。

**相关文档**：[README](../README.md)（概览与快速开始） · [设计与架构](DESIGN_AND_ARCHITECTURE.md)（分层设计、核心组件、执行流程）

## 一、全局选项

| 选项 | 说明 |
|------|------|
| `--version` / `-v` | 打印版本并退出（在 callback 中提前处理） |

## 二、顶层命令

### onboard

初始化配置与工作区（含多 agent 时的 workspace 与 AGENTS.md/SOUL.md/memory 模板）。

```bash
joyhousebot onboard
```

无参数。若配置已存在会提示覆盖或刷新（保留已有值并补充新字段）。

### gateway

启动网关：通道（ChannelManager）+ 定时任务（CronService）+ 心跳（HeartbeatService）+ HTTP/WebSocket API，单端口默认 18790。

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--host` / `-h` | `127.0.0.1` | 绑定地址（API + 网关） |
| `--port` / `-p` | `18790` | 端口 |
| `--verbose` / `-v` | `False` | 详细输出 |
| `--wallet-unlock` | `False` | 启动时输入默认钱包密码，解密私钥驻留内存供签名等使用 |

```bash
joyhousebot gateway
joyhousebot gateway --port 8080
```

### agent

与 agent 对话：单条消息或交互式。单条模式用 `-m`；无 `-m` 为交互式，输入 `exit`/`quit`/`:q` 或 `Ctrl+D` 退出。

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--message` / `-m` | — | 要发送的消息（省略则进入交互式） |
| `--session` / `-s` | `cli:direct` | 会话 ID |
| `--markdown` / `--no-markdown` | `True` | 是否以 Markdown 渲染回复 |
| `--logs` / `--no-logs` | `False` | 是否显示运行时日志 |
| `--debug` / `-d` | `False` | 调试模式：完整执行流（LLM 调用、工具调用、中间结果、错误与 traceback） |

```bash
joyhousebot agent -m "What is 2+2?"
joyhousebot agent
joyhousebot agent --no-markdown --logs
```

### status

显示状态：配置路径、工作区、模型、Provider 等。

```bash
joyhousebot status
```

## 三、按命令组分类

### config

配置读写（点号路径）、从 OpenClaw 迁移。

| 子命令 | 参数/选项 | 说明 |
|--------|-----------|------|
| `get` | `<key>` | 读取点号路径，如 `providers.openrouter.apiKey` |
| `set` | `<key>` `<value>` | 设置点号路径，value 支持 JSON 或字符串 |
| `unset` | `<key>` | 删除点号路径 |
| `migrate-from-openclaw` | `[OPENCLAW_DIR]`、`--dry-run` | 从 OpenClaw 目录导入 openclaw.json，默认 `~/.openclaw` |

```bash
joyhousebot config get providers.openrouter.apiKey
joyhousebot config set agents.defaults.model "anthropic/claude-opus-4-5"
joyhousebot config migrate-from-openclaw --dry-run
```

### channels

通道管理。

| 子命令 | 说明 |
|--------|------|
| `login` | 链接 WhatsApp（扫码）等 |
| `status` | 显示各通道启用状态与配置摘要 |

```bash
joyhousebot channels login
joyhousebot channels status
```

### cron

定时任务。

| 子命令 | 主要选项 | 说明 |
|--------|----------|------|
| `add` | `--name` / `-n`、`--message` / `-m`、`--every` / `-e`、`--cron` / `-c`、`--at`、`--deliver`、`--to`、`--channel` | 新增定时任务 |
| `list` | `--all` / `-a` | 列出任务（`-a` 含已禁用） |
| `remove` | `<job_id>` | 删除任务 |

```bash
joyhousebot cron add --name daily --message "Good morning!" --cron "0 9 * * *"
joyhousebot cron add --name hourly --message "Check" --every 3600
joyhousebot cron list
joyhousebot cron remove <job_id>
```

### skills

技能列表、安装、查看、搜索。

| 子命令 | 说明 |
|--------|------|
| `list` | 列出已安装与内置技能 |
| `install` | `install <repo>` 从 GitHub 安装（如 owner/repo 或 owner/repo/subpath） |
| `list-builtin` | 列出内置技能 |
| `show` | `show <name>` 显示技能内容（SKILL.md） |
| `search` | `search [query]` 按名称或描述搜索 |

### plugins

插件相关子命令。详见 `joyhousebot plugins --help`。

### house

本地 house 身份与任务队列（可与控制面联动）。

| 子命令 | 主要选项/参数 | 说明 |
|--------|----------------|------|
| `init` | — | 初始化本地 house 身份与 SQLite 状态 |
| `register` | `--server`、`--name` 等 | 向控制面注册当前 house |
| `bind` | `<user_id>`、`--server` | 将当前 house 绑定到用户 |
| `worker` | `--server`、`--poll-interval`、`--heartbeat-interval`、`--ws`/`--no-ws`、`--concurrency`/`-c`、`--max-retries`、`--retry-backoff-base`、`--run-once` | 运行本地 worker 循环（拉取/执行任务） |
| `tasks list` | `--status`、`--limit`、`--type` 等 | 列出本地任务队列 |
| `tasks add-local` | 等 | 本地入队任务 |
| `tasks retry` | 等 | 重试失败任务 |

```bash
joyhousebot house init
joyhousebot house register --server http://127.0.0.1:8000
joyhousebot house worker --server http://127.0.0.1:8000 --run-once
joyhousebot house tasks list
```

### wallet

钱包相关子命令（EVM 等）。详见 `joyhousebot wallet --help`。

### health

网关健康检查。

| 子命令 | 选项 | 说明 |
|--------|------|------|
| `check` | `--timeout` | 请求网关 `/health`，默认超时 3s |

```bash
joyhousebot health check
```

### dashboard

打开本地控制 UI（系统浏览器）。依赖网关已启动。

```bash
joyhousebot dashboard
```

### doctor

本地诊断与可选网关检查。

| 子命令 | 说明 |
|--------|------|
| `check` | 检查 config、workspace、data dir、Python、Node、网关可达性等 |

```bash
joyhousebot doctor check
```

### logs

查看网关日志。

| 子命令 | 选项 | 说明 |
|--------|------|------|
| `tail` | `--file`、`--lines` / `-n` | 查看本地日志文件，默认 80 行 |

### daemon、reset

运行时守护与重置相关。详见 `joyhousebot daemon --help`、`joyhousebot reset --help`。

### comms

消息、会话、记忆、归档。

| 典型子命令 | 说明 |
|------------|------|
| `message`、`sessions`、`memory`、`archive` | 与消息/会话/记忆/归档相关的操作 |

详见 `joyhousebot comms --help`。

### protocol

协议/控制子命令，数量较多，与 OpenClaw 兼容或扩展能力相关。

| 命令组/命令 | 典型子命令 | 说明 |
|-------------|------------|------|
| `devices` | `list`、`status`、`approve`、`reject`、`token-rotate`、`token-revoke` | 设备管理 |
| `pairing` | `list`、`approve`、`reject`、`login` | 配对 |
| `system` | `status`、`presence`、`start`、`stop`、`logs` | 系统状态与控制 |
| `docs`、`update`、`uninstall` | — | 文档、更新、卸载 |
| `hooks` | `list`、`add`、`remove`、`run`、`check`、`install`、`update` | 钩子 |
| `webhooks` | `list`、`add`、`remove`、`test`；`webhooks gmail setup`/`run` | Webhook 与 Gmail |
| `directory` | `list`、`agents`、`self`、`peers`、`groups`、`members` | 目录 |
| `browser` | `request`、`status`、`inspect`、`action`、`debug`、`state` | 浏览器控制 |
| `node` / `nodes` | `run`、`install`、`status`、`stop`、`restart`、`uninstall`、`list`、`describe`、`rename`、`invoke`、`pending`、`pair-list`、`approve`、`pair-approve`、`reject`、`pair-reject`、`pair-verify` | 节点管理 |
| `approvals` / `exec-approvals` | `get`、`set`、`request`、`wait`、`resolve`、`policy-get`、`policy-set`、`node-get`、`node-set`；`allowlist list`/`add`/`remove` | 审批与执行审批 |
| `sandbox` | `status`、`set`、`list`、`recreate`、`explain` | 沙箱 |
| `security` | `status`、`scopes-set`、`token-rotate`、`token-revoke`、`audit`、`fix` | 安全 |
| `acp` | `connect`、`call` | ACP |
| `dns` | `lookup` | DNS 解析 |

完整列表与参数请运行：

```bash
joyhousebot protocol --help
joyhousebot protocol <子组> --help
```

## 四、与 OpenClaw 的兼容说明

- **gateway**：单端口提供 HTTP/WebSocket API，与 OpenClaw 单端口部署方式兼容。
- **house**：本地 house 身份与 worker/tasks 可与 OpenClaw 风格控制面配合使用（`house register`、`house worker`、`house tasks`）。
- **protocol**：大量子命令与 OpenClaw 协议/控制面对齐，便于迁移或混合部署。

**相关文档**：项目概览见 [README](../README.md)，设计与架构见 [DESIGN_AND_ARCHITECTURE.md](DESIGN_AND_ARCHITECTURE.md)。
