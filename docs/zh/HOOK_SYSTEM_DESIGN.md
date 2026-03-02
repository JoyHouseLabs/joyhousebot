# 钩子系统设计文档

> **状态**: ✅ 已实现

joyhousebot 钩子系统已完整实现，对齐 OpenClaw 的 14 种生命周期钩子。

## 实现概览

### 文件结构

```
joyhousebot/plugins/hooks/
├── __init__.py      # 模块导出
├── types.py         # 钩子类型定义（事件、结果、处理器签名）
└── dispatcher.py    # 钩子调度器（注册、触发、优先级）
```

### 核心组件

1. **HookName** - 枚举，定义 14 种钩子
2. **事件类型** - 每种钩子对应的事件数据类
3. **结果类型** - 可修改行为的钩子返回值
4. **HookDispatcher** - 全局调度器，管理注册和触发
5. **集成点** - 在 Agent Loop 中的钩子调用位置

## 已实现的钩子

| 钩子名 | 触发时机 | 可修改 | 状态 |
|--------|---------|--------|------|
| `before_agent_start` | Agent 开始处理消息前 | ✅ 系统提示词 | ✅ |
| `agent_end` | Agent 处理完成后 | ❌ | ✅ |
| `message_received` | 收到用户消息时 | ❌ | ✅ |
| `message_sending` | 发送响应消息前 | ✅ 可取消/修改内容 | ✅ |
| `message_sent` | 发送响应消息后 | ❌ | ✅ |
| `before_tool_call` | 执行工具调用前 | ✅ 可拦截/修改参数 | ✅ |
| `after_tool_call` | 执行工具调用后 | ❌ | ✅ |
| `session_start` | 会话开始时 | ❌ | ✅ |
| `session_end` | 会话结束时 | ❌ | ✅ |
| `before_compaction` | 记忆压缩前 | ❌ | ✅ |
| `after_compaction` | 记忆压缩后 | ❌ | ✅ |
| `before_reset` | 会话重置前 | ❌ | ✅ |
| `gateway_start` | 网关启动时 | ❌ | ✅ |
| `gateway_stop` | 网关停止时 | ❌ | ✅ |
| `tool_result_persist` | 工具结果持久化时 | ✅ | ✅ |

## 使用方式

### 插件中注册钩子

```python
from joyhousebot.plugins.hooks.types import (
    BeforeToolCallEvent, BeforeToolCallResult,
    AfterToolCallEvent, MessageSendingEvent, MessageSendingResult
)

class MyPlugin:
    def register(self, api):
        # 方式 1：装饰器
        @api.on("before_tool_call")
        def check_tool(event: BeforeToolCallEvent, context):
            if event.tool_name == "shell":
                return BeforeToolCallResult(
                    block=True,
                    block_reason="Shell disabled"
                )
            return None
        
        # 方式 2：函数注册
        api.register_hook("after_tool_call", log_tool, priority=10)
        
        # 方式 3：装饰器带优先级
        @api.on("message_sending", priority=-100)
        def filter_message(event: MessageSendingEvent, context):
            if "secret" in event.content:
                return MessageSendingResult(cancel=True)
            return None

plugin = MyPlugin()
```

### 钩子调度器 API

```python
from joyhousebot.plugins.hooks import (
    get_hook_dispatcher,
    emit_hook,
    emit_hook_first_result,
    HookName,
)

# 获取全局调度器
dispatcher = get_hook_dispatcher()

# 手动注册
dispatcher.register(
    hook_name="before_tool_call",
    handler=my_handler,
    priority=0,
    plugin_id="my-plugin",
)

# 触发（收集所有结果）
results = await dispatcher.emit(HookName.BEFORE_TOOL_CALL, event, context)

# 触发（返回第一个非 None 结果）
result = await dispatcher.emit_first_result(HookName.MESSAGE_SENDING, event, context)

# 便捷函数
results = await emit_hook("after_tool_call", event, context)
result = await emit_hook_first_result("message_sending", event, context)
```

## 集成点

### Agent Loop (joyhousebot/agent/loop.py)

```python
# 1. 消息接收时
received_event = MessageReceivedEvent(...)
await hook_dispatcher.emit(HookName.MESSAGE_RECEIVED, received_event, hook_ctx)

# 2. 工具调用前
before_event = BeforeToolCallEvent(tool_name=tool_name, params=tool_args)
before_result = await hook_dispatcher.emit_first_result(
    HookName.BEFORE_TOOL_CALL, before_event, hook_ctx
)
if before_result and before_result.block:
    # 阻止工具调用
    ...
if before_result and before_result.params:
    tool_args = before_result.params  # 修改参数

# 3. 工具调用后
after_event = AfterToolCallEvent(tool_name=tool_name, result=result)
await hook_dispatcher.emit(HookName.AFTER_TOOL_CALL, after_event, hook_ctx)

# 4. 消息发送前
sending_event = MessageSendingEvent(content=final_content)
sending_result = await hook_dispatcher.emit_first_result(
    HookName.MESSAGE_SENDING, sending_event, hook_ctx
)
if sending_result and sending_result.cancel:
    return None  # 取消发送

# 5. 消息发送后
sent_event = MessageSentEvent(content=final_content)
await hook_dispatcher.emit(HookName.MESSAGE_SENT, sent_event, hook_ctx)
```

### Native Plugin Loader (joyhousebot/plugins/native/loader.py)

```python
# 加载时自动注册到全局调度器
for h in api.hooks:
    dispatcher = get_hook_dispatcher()
    dispatcher.register(
        hook_name=str(h.get("hookName") or ""),
        handler=h.get("handler"),
        priority=int(h.get("priority") or 0),
        plugin_id=plugin_id,
    )
```

## 类型定义

详见 `joyhousebot/plugins/hooks/types.py`：

- **HookName** - 钩子名称枚举
- **HookContext** - 上下文对象
- **事件类型** - BeforeToolCallEvent, MessageReceivedEvent 等
- **结果类型** - BeforeToolCallResult, MessageSendingResult 等
- **处理器签名** - 类型别名

## 与 OpenClaw 对比

| 特性 | OpenClaw | joyhousebot |
|------|----------|-------------|
| 钩子数量 | 14 | 14 ✅ |
| 工具拦截 | ✅ | ✅ |
| 消息修改 | ✅ | ✅ |
| 优先级 | ✅ | ✅ |
| 异步支持 | ✅ | ✅ |
| 装饰器注册 | ✅ | ✅ |

## 参考文档

- [插件开发指南](./NATIVE_PLUGIN_GUIDE.md) - 完整的钩子使用示例
- [插件对比](./PLUGIN_COMPARISON.md) - OpenClaw vs joyhousebot 插件系统对比
