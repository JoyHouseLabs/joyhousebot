# Python 原生插件开发指南

joyhousebot 支持纯 Python 原生插件，无需 Node.js 依赖。插件可以注册工具、RPC 方法、HTTP 路由、CLI 命令、服务等。

## 概念对比：插件工具 vs 系统工具 vs Skill

joyhousebot 有三种扩展机制，适用于不同场景：

| 特性 | 插件工具 (Plugin Tool) | 系统内置工具 (Built-in Tool) | Skill (技能) |
|------|----------------------|---------------------------|-------------|
| **形式** | Python 函数 | Python 类 | Markdown 文档 |
| **注册方式** | `api.register_tool()` | 继承 `Tool` 基类，加入注册表 | `SKILL.md` 文件 |
| **调用方式** | Agent 通过 `plugin_invoke` 调用 | Agent 直接调用 | Agent 读取并遵循指令 |
| **执行位置** | 插件进程内 | joyhousebot 进程内 | Agent 推理时参考 |
| **适用场景** | 业务逻辑、外部集成、可插拔功能 | 核心能力（文件、网络等） | 引导 Agent 行为模式 |

### 何时使用哪种？

**使用插件工具**：
- 需要动态加载/卸载的功能
- 业务相关的特定功能（如订单查询、通知发送）
- 需要隔离的第三方集成
- 用户自定义扩展

**使用系统内置工具**：
- 核心基础设施（文件读写、HTTP 请求、命令执行）
- 几乎所有用户都需要的基础能力
- 性能敏感的操作

**使用 Skill**：
- 引导 Agent 如何思考和行动
- 提供领域知识和最佳实践
- 组合多个工具完成复杂任务
- 不需要执行代码，只需要指导 Agent

### 示例对比

**插件工具**（业务逻辑）：
```python
# plugins/order-plugin/plugin.py
class OrderPlugin:
    def register(self, api):
        def query_order(order_id: str) -> dict:
            # 调用订单系统 API
            return {"order_id": order_id, "status": "shipped"}
        
        api.register_tool("query_order", query_order)
```

**系统内置工具**（基础设施）：
```python
# agent/tools/shell.py
class ShellTool(Tool):
    @property
    def name(self) -> str:
        return "shell"
    
    async def execute(self, command: str, **kwargs) -> str:
        # 执行 shell 命令
        result = subprocess.run(command, shell=True, capture_output=True)
        return result.stdout
```

**Skill**（行为指导）：
```markdown
# skills/code-review/SKILL.md
---
name: code-review
description: Guide for reviewing code changes
---

When reviewing code:
1. Check for security vulnerabilities
2. Verify test coverage
3. Look for performance issues
4. Ensure code style consistency

Use `shell` tool to run `git diff` and analyze changes.
```

## 快速开始

### 1. 创建插件目录结构

```
my-plugin/
├── joyhousebot.plugin.json   # 插件清单（必需）
├── plugin.py                 # 插件入口（默认）
└── skills/                   # 可选：技能目录
    └── my-skill/
        └── SKILL.md
```

### 2. 编写清单文件

`joyhousebot.plugin.json`:

```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "description": "A sample plugin",
  "version": "1.0.0",
  "runtime": "python-native",
  "entry": "plugin.py:plugin",
  "capabilities": {
    "tools": true,
    "rpc": true,
    "hooks": true,
    "services": true,
    "cli": true,
    "http": true
  },
  "skills": ["skills"]
}
```

### 3. 编写插件代码

`plugin.py`:

```python
from typing import Any

class MyPlugin:
    def register(self, api: Any) -> None:
        # 注册工具（Agent 可调用）
        def echo(value: Any) -> dict[str, Any]:
            return {"ok": True, "echo": value}
        
        api.register_tool("echo", echo)
        
        # 注册 RPC 方法（网关可调用）
        def greet(params: dict[str, Any]) -> dict[str, Any]:
            name = params.get("name", "world")
            return {"message": f"Hello, {name}!"}
        
        api.register_rpc("greet", greet)
        
        # 注册 HTTP 路由
        def handle_request(req: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": 200,
                "headers": {"content-type": "application/json"},
                "body": {"path": req.get("path")}
            }
        
        api.register_http("/my-plugin/info", handle_request, methods=["GET"])
        
        # 注册 CLI 命令
        api.register_cli("greet", lambda p: greet(p), description="Greet someone")
        
        # 注册服务（后台任务）
        def start_service():
            print("Service started")
        
        def stop_service():
            print("Service stopped")
        
        api.register_service("my-plugin.worker", start=start_service, stop=stop_service)
        
        # 注册钩子
        def on_gateway_start(*args, **kwargs):
            print("Gateway started")
        
        api.register_hook("gateway_start", on_gateway_start, priority=1)


plugin = MyPlugin()
```

## API 参考

### api.register_tool(name, handler)

注册 Agent 可调用的工具。

- `name`: 工具名称（简洁，如 `echo`）
- `handler`: 可调用对象，接收参数，返回结果

### api.register_rpc(method, handler)

注册 RPC 方法，可通过网关调用。

- `method`: 方法名（如 `greet`）
- `handler`: 可调用对象，接收 `params` 字典

### api.register_http(path, handler, methods=["GET", "POST"])

注册 HTTP 路由。

- `path`: 路由路径（如 `/my-plugin/info`）
- `handler`: 接收请求字典，返回响应字典
- `methods`: 允许的 HTTP 方法

响应格式：
```python
{
    "status": 200,
    "headers": {"content-type": "application/json"},
    "body": {"key": "value"}
}
```

### api.register_cli(command, handler, description="")

注册 CLI 命令。

- `command`: 命令名
- `handler`: 接收 `payload` 字典
- `description`: 命令描述

### api.register_service(service_id, start=None, stop=None)

注册后台服务。

- `service_id`: 服务标识
- `start`: 启动回调
- `stop`: 停止回调

### api.register_hook(hook_name, handler, priority=0)

注册生命周期钩子。

- `hook_name`: 钩子名（如 `before_tool_call`）
- `handler`: 回调函数，接收 `(event, context)` 参数
- `priority`: 优先级（数字越小越先执行）

### api.on(hook_name, handler=None, *, priority=0)

装饰器方式注册钩子（对齐 OpenClaw 风格）。

```python
# 装饰器方式
@api.on("before_tool_call")
def before_tool(event, context):
    print(f"Tool {event.tool_name} about to be called")
    return None  # 返回 None 继续执行

# 函数方式
api.on("after_tool_call", on_after_tool, priority=10)

# 带优先级
@api.on("message_sending", priority=-100)
def check_message(event, context):
    if "secret" in event.content:
        return MessageSendingResult(cancel=True)  # 取消发送
    return None
```

## 钩子系统

joyhousebot 支持完整的钩子系统，对齐 OpenClaw 的生命周期钩子。插件可以在关键执行节点介入。

### 可用钩子

| 钩子名 | 触发时机 | 可修改 |
|--------|---------|--------|
| `before_agent_start` | Agent 开始处理消息前 | ✅ 系统提示词 |
| `agent_end` | Agent 处理完成后 | ❌ |
| `message_received` | 收到用户消息时 | ❌ |
| `message_sending` | 发送响应消息前 | ✅ 可取消/修改内容 |
| `message_sent` | 发送响应消息后 | ❌ |
| `before_tool_call` | 执行工具调用前 | ✅ 可拦截/修改参数 |
| `after_tool_call` | 执行工具调用后 | ❌ |
| `session_start` | 会话开始时 | ❌ |
| `session_end` | 会话结束时 | ❌ |
| `before_compaction` | 记忆压缩前 | ❌ |
| `after_compaction` | 记忆压缩后 | ❌ |
| `before_reset` | 会话重置前 | ❌ |
| `gateway_start` | 网关启动时 | ❌ |
| `gateway_stop` | 网关停止时 | ❌ |
| `tool_result_persist` | 工具结果持久化时 | ✅ |

### 钩子示例

#### 1. 工具调用拦截

```python
from joyhousebot.plugins.hooks.types import (
    BeforeToolCallEvent, BeforeToolCallResult,
    AfterToolCallEvent
)

class SecurityPlugin:
    def register(self, api):
        @api.on("before_tool_call")
        def check_dangerous_tools(event: BeforeToolCallEvent, context):
            dangerous_tools = ["shell", "write_file", "exec"]
            if event.tool_name in dangerous_tools:
                return BeforeToolCallResult(
                    block=True,
                    block_reason="Dangerous tool blocked by security policy"
                )
            return None
        
        @api.on("after_tool_call")
        def log_tool_usage(event: AfterToolCallEvent, context):
            print(f"Tool {event.tool_name} completed, result length: {len(str(event.result))}")
```

#### 2. 消息过滤

```python
from joyhousebot.plugins.hooks.types import (
    MessageReceivedEvent, MessageSendingEvent, MessageSendingResult
)

class ContentFilterPlugin:
    def register(self, api):
        @api.on("message_received")
        def log_incoming(event: MessageReceivedEvent, context):
            print(f"Received from {event.from_id}: {event.content[:50]}...")
        
        @api.on("message_sending")
        def filter_response(event: MessageSendingEvent, context):
            forbidden_words = ["password", "secret", "api_key"]
            for word in forbidden_words:
                if word in event.content.lower():
                    return MessageSendingResult(
                        content="[REDACTED] Response contained sensitive information"
                    )
            return None
```

#### 3. 审计日志

```python
import json
from datetime import datetime

class AuditPlugin:
    def __init__(self):
        self.audit_log = []
    
    def register(self, api):
        @api.on("before_tool_call")
        def audit_tool_start(event, context):
            self.audit_log.append({
                "timestamp": datetime.now().isoformat(),
                "type": "tool_start",
                "tool": event.tool_name,
                "params": event.params
            })
        
        @api.on("after_tool_call")
        def audit_tool_end(event, context):
            self.audit_log.append({
                "timestamp": datetime.now().isoformat(),
                "type": "tool_end",
                "tool": event.tool_name,
                "success": event.error is None
            })
        
        @api.on("message_sent")
        def audit_message(event, context):
            self.audit_log.append({
                "timestamp": datetime.now().isoformat(),
                "type": "message_sent",
                "content_length": len(event.content)
            })
```

### 事件类型

所有事件定义在 `joyhousebot.plugins.hooks.types` 中：

```python
from joyhousebot.plugins.hooks.types import (
    # 工具相关
    BeforeToolCallEvent, BeforeToolCallResult,
    AfterToolCallEvent,
    
    # 消息相关
    MessageReceivedEvent, MessageSendingEvent,
    MessageSendingResult, MessageSentEvent,
    
    # 会话相关
    SessionStartEvent, SessionEndEvent,
    
    # Agent 相关
    BeforeAgentStartEvent, AgentEndEvent,
    
    # 其他
    BeforeCompactionEvent, AfterCompactionEvent,
    GatewayStartEvent, GatewayStopEvent,
)
```

### 上下文对象

每个钩子接收一个 `HookContext` 对象：

```python
@dataclass
class HookContext:
    agent_id: str | None = None
    session_id: str | None = None
    session_key: str | None = None
    workspace_dir: str | None = None
    channel: str | None = None
    account_id: str | None = None
```

### api.register_provider(provider_id)

注册 Provider。

### api.register_channel(channel_id, start=None, stop=None)

注册通道。

## 安装与启用

### 方法一：CLI 安装

```bash
joyhousebot plugins install /path/to/my-plugin
```

### 方法二：手动配置

在 `~/.joyhousebot/config.json` 中添加：

```json
{
  "plugins": {
    "enabled": true,
    "load": {
      "paths": ["/path/to/my-plugin"]
    },
    "entries": {
      "my-plugin": {
        "enabled": true,
        "config": {
          "prefix": "Hello"
        }
      }
    }
  }
}
```

## 验证与调试

```bash
# 查看插件列表
joyhousebot plugins list

# 查看插件详情
joyhousebot plugins info my-plugin

# 查看已注册工具
joyhousebot plugins tools

# 运行诊断
joyhousebot plugins doctor

# 重载插件
joyhousebot plugins reload

# 调用 CLI 命令
joyhousebot plugins cli-run greet --payload '{"name": "joyhouse"}'
```

## 插件配置

插件可以通过 `plugins.entries.<id>.config` 接收配置：

```json
{
  "plugins": {
    "entries": {
      "my-plugin": {
        "enabled": true,
        "config": {
          "prefix": "Hi",
          "max_items": 100
        }
      }
    }
  }
}
```

在插件中访问配置：

```python
class MyPlugin:
    def register(self, api):
        config = api.plugin_config  # 获取配置字典
        prefix = config.get("prefix", "Hello")
```

## 最佳实践

1. **命名简洁**：工具名和方法名使用简洁的名称，无需命名空间前缀
2. **错误处理**：返回规范的错误结构 `{"ok": False, "error": {"code": "...", "message": "..."}}`
3. **幂等性**：工具和 RPC 方法应尽量幂等
4. **日志记录**：使用标准 logging 模块记录日志
5. **类型注解**：为函数添加类型注解提高可读性

## 完整示例

参考 `examples/native-plugins/hello-native/` 目录。
