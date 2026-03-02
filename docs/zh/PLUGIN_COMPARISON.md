# OpenClaw vs joyhousebot 插件系统对比

## 架构对比

| 方面 | OpenClaw | joyhousebot |
|------|----------|-------------|
| **运行时** | TypeScript/Node.js | Python |
| **插件定义** | 导出对象/函数 | 类 + `plugin` 变量 |
| **清单文件** | `openclaw.plugin.json` | `joyhousebot.plugin.json` |
| **加载方式** | 动态 import | importlib |

## API 对比

### OpenClaw API（丰富）

```typescript
const myPlugin = {
  id: "my-plugin",
  name: "My Plugin",
  configSchema: { ... },
  register(api: OpenClawPluginApi) {
    // 工具注册
    api.registerTool(toolFactory, { names: ["tool1", "tool2"] });
    
    // 生命周期钩子（非常丰富）
    api.on("before_agent_start", handler);
    api.on("agent_end", handler);
    api.on("before_tool_call", handler);
    api.on("after_tool_call", handler);
    api.on("message_received", handler);
    api.on("message_sending", handler);
    api.on("session_start", handler);
    api.on("gateway_start", handler);
    // ... 共 14 种钩子
    
    // HTTP 路由
    api.registerHttpRoute({ path: "/api/xxx", handler });
    api.registerHttpHandler(handler);
    
    // 通道注册（核心能力）
    api.registerChannel(channelPlugin);
    
    // 网关方法
    api.registerGatewayMethod("method", handler);
    
    // CLI 命令
    api.registerCli(({ program }) => { ... });
    
    // 后台服务
    api.registerService({ id, start, stop });
    
    // Provider（OAuth 认证）
    api.registerProvider({
      id: "google",
      auth: [{ kind: "oauth", run: async (ctx) => { ... } }]
    });
    
    // 自定义命令（绕过 LLM）
    api.registerCommand({
      name: "tts",
      handler: async (ctx) => { ... }
    });
  }
};
```

### joyhousebot API（简洁）

```python
class MyPlugin:
    def register(self, api):
        # 工具注册
        api.register_tool("echo", lambda v: {"echo": v})
        
        # RPC 方法
        api.register_rpc("greet", lambda p: {"message": f"Hi {p['name']}"})
        
        # 钩子（基础）
        api.register_hook("gateway_start", handler, priority=1)
        
        # HTTP 路由
        api.register_http("/api/xxx", handler, methods=["GET"])
        
        # CLI 命令
        api.register_cli("greet", handler)
        
        # 后台服务
        api.register_service("worker", start=fn, stop=fn)
        
        # Provider（简化）
        api.register_provider("my.provider")
        
        # 通道（简化）
        api.register_channel("my.channel", start=fn, stop=fn)

plugin = MyPlugin()
```

## 能力对比

| 能力 | OpenClaw | joyhousebot |
|------|----------|-------------|
| **工具注册** | ✅ 工厂函数，支持多名称 | ✅ 简单函数 |
| **RPC 方法** | ✅ 通过 gateway | ✅ 直接注册 |
| **钩子系统** | ✅ 14 种生命周期钩子 | ⚠️ 基础钩子 |
| **HTTP 路由** | ✅ 完整 | ✅ 简化 |
| **CLI 命令** | ✅ Commander 集成 | ✅ 简化 |
| **后台服务** | ✅ | ✅ |
| **通道实现** | ✅ 插件式（30+） | ✅ 内置式（9 种） |
| **通道架构** | 插件注册 | BaseChannel + Manager |
| **Provider/OAuth** | ✅ 完整 OAuth 流程 | ⚠️ 仅注册 ID |
| **自定义命令** | ✅ 绕过 LLM | ❌ |
| **配置 Schema** | ✅ JSON Schema + Zod | ⚠️ 基础 |
| **Skills 集成** | ✅ | ✅ |

## 通道对比

### OpenClaw 通道（插件式）

通过 `api.registerChannel()` 注册，每个通道是一个独立插件：

```
extensions/
├── discord/       # Discord 通道插件
├── slack/         # Slack 通道插件
├── telegram/      # Telegram 通道插件
├── whatsapp/      # WhatsApp 通道插件
├── feishu/        # 飞书通道插件
├── matrix/        # Matrix 通道插件
├── msteams/       # Microsoft Teams 通道插件
├── irc/           # IRC 通道插件
├── nostr/         # Nostr 通道插件
├── line/          # LINE 通道插件
├── googlechat/    # Google Chat 通道插件
├── bluebubbles/   # BlueBubbles (iMessage) 通道插件
├── mattermost/    # Mattermost 通道插件
├── nextcloud-talk/# Nextcloud Talk 通道插件
└── ...
```

### joyhousebot 通道（内置式）

通过继承 `BaseChannel` 实现，由 `ChannelManager` 统一管理：

```
channels/
├── base.py        # 抽象基类
├── manager.py     # 通道管理器
├── telegram.py    # Telegram（纯 Python，python-telegram-bot）
├── whatsapp.py    # WhatsApp（Node.js bridge）
├── discord.py     # Discord
├── feishu.py      # 飞书
├── slack.py       # Slack
├── dingtalk.py    # 钉钉
├── mochat.py      # Mochat
├── email.py       # Email
└── qq.py          # QQ
```

### 通道架构对比

| 方面 | OpenClaw | joyhousebot |
|------|----------|-------------|
| **注册方式** | `api.registerChannel(plugin)` | 继承 `BaseChannel` |
| **生命周期** | 插件管理 | `ChannelManager` 统一管理 |
| **消息路由** | 内部事件系统 | `MessageBus`（入站/出站队列） |
| **权限控制** | `allowlist` 在插件内 | `BaseChannel.is_allowed()` |
| **可扩展性** | 高（插件可动态加载） | 中（需修改代码） |

### joyhousebot 通道优势

1. **Telegram 纯 Python**：无需 Node.js 依赖
2. **统一消息总线**：`MessageBus` 解耦入站/出站
3. **简洁基类**：`BaseChannel` 接口清晰
4. **集中管理**：`ChannelManager` 统一启停

### joyhousebot 通道劣势

1. **不可动态加载**：通道需内置编译
2. **扩展需改代码**：添加新通道需修改 `manager.py`
3. **WhatsApp 依赖 Node.js**：仍需 bridge 进程

## 钩子系统对比

### OpenClaw 钩子（14 种）

| 钩子 | 触发时机 |
|------|---------|
| `before_agent_start` | Agent 启动前，可修改 system prompt |
| `agent_end` | Agent 执行结束 |
| `before_compaction` | 消息压缩前 |
| `after_compaction` | 消息压缩后 |
| `before_reset` | 会话重置前 |
| `message_received` | 收到消息 |
| `message_sending` | 发送消息前（可取消/修改） |
| `message_sent` | 消息发送后 |
| `before_tool_call` | 工具调用前（可阻止/修改参数） |
| `after_tool_call` | 工具调用后 |
| `tool_result_persist` | 工具结果持久化前（可修改） |
| `session_start` | 会话开始 |
| `session_end` | 会话结束 |
| `gateway_start/stop` | 网关启动/停止 |

### joyhousebot 钩子（基础）

| 钩子 | 触发时机 |
|------|---------|
| `gateway_start` | 网关启动 |
| 其他 | 需要扩展 |

## 优劣分析

### OpenClaw 优势

1. **生态系统丰富**：30+ 内置扩展（Discord, Slack, Telegram, WhatsApp, Matrix, Feishu...）
2. **通道能力完整**：内置消息通道实现，无需外部依赖
3. **钩子系统强大**：可介入 Agent 执行的各个环节
4. **OAuth 支持**：完整的 Provider 认证流程
5. **配置验证**：JSON Schema + Zod 验证

### OpenClaw 劣势

1. **需要 Node.js**：增加了运行时依赖
2. **复杂度高**：学习曲线陡峭
3. **启动较慢**：需要加载大量模块
4. **调试困难**：TypeScript 编译链

### joyhousebot 优势

1. **纯 Python**：无额外运行时依赖
2. **简单直观**：API 设计简洁，易于上手
3. **启动快速**：轻量级加载
4. **调试方便**：直接 Python 调试

### joyhousebot 劣势

1. **生态较小**：内置扩展少
2. **通道依赖外部**：需要 bridge 进程
3. **钩子有限**：无法深度介入 Agent 执行
4. **无 OAuth 支持**：Provider 认证需手动处理

## 选择建议

| 场景 | 推荐 |
|------|------|
| 需要多种消息通道 | OpenClaw |
| 需要深度定制 Agent 行为 | OpenClaw |
| 需要纯 Python 环境 | joyhousebot |
| 快速开发简单扩展 | joyhousebot |
| 需要 OAuth 认证 | OpenClaw |
| 追求启动速度 | joyhousebot |

## 扩展 joyhousebot 的方向

如果想让 joyhousebot 具备类似 OpenClaw 的能力，可以：

1. **扩展钩子系统**：添加 `before_tool_call`、`after_tool_call` 等
2. **内置通道**：实现 Python 原生的通道（如 Telegram Bot）
3. **OAuth 支持**：添加 Provider 认证流程
4. **配置 Schema**：支持 JSON Schema 验证
