# Error Handling Convention

## Overview

joyhousebot 采用统一的异常处理系统，提供以下能力：

1. **异常类层次结构** - 8 种专用异常类，覆盖所有错误场景
2. **错误分类** - `ErrorCategory` 枚举，支持自动判断重试策略
3. **敏感信息过滤** - `sanitize_error_message()` 自动过滤 API key、token 等敏感数据
4. **异常自动分类** - `classify_exception()` 自动判断异常类型和重试策略
5. **工具装饰器** - `@tool_error_handler` 统一工具异常处理

## 核心模块

### `joyhousebot.utils.exceptions`

提供完整的异常处理工具集：

| 组件 | 功能 |
|--------|------|
| **异常类** | `JoyhouseBotError` 基类 + 8 个专用异常 |
| **错误分类** | `ErrorCategory` 枚举 (RECOVERABLE/RETRYABLE/FATAL/...) |
| **敏感信息过滤** | `sanitize_error_message()` 函数 |
| **异常分类器** | `classify_exception()` 自动分类任意异常 |
| **工具装饰器** | `@tool_error_handler` 统一处理工具异常 |
| **安全执行** | `safe_execute()` 包装函数 |

### 异常类层次

```python
JoyhouseBotError          # 基类
├── ValidationError       # 输入验证错误
├── NotFoundError        # 资源未找到
├── PermissionError      # 权限拒绝
├── TimeoutError         # 超时
├── RateLimitError       # 限流
├── LLMError            # LLM 调用错误
├── ToolError           # 工具执行错误
├── ChannelError        # 通道错误
└── PluginError         # 插件错误
```

### 错误分类

| 分类 | 说明 | 是否重试 |
|------|------|----------|
| `RECOVERABLE` | 可恢复的错误（如文件锁） | 否 |
| `RETRYABLE` | 可重试的错误（如网络连接） | 是 |
| `FATAL` | 致命错误（如无效配置） | 否 |
| `VALIDATION` | 输入验证错误 | 否 |
| `NOT_FOUND` | 资源未找到 | 否 |
| `PERMISSION` | 权限拒绝 | 否 |
| `TIMEOUT` | 操作超时 | 是 |
| `RATE_LIMIT` | 限流 | 是 |

## 使用指南

### 1. 在工具中使用异常类

```python
from joyhousebot.utils.exceptions import ToolError, ValidationError

@tool_error_handler("Failed to read file")
async def execute(self, path: str, **kwargs) -> str:
    if not path:
        raise ValidationError("path is required", field="path")

    if not os.path.exists(path):
        raise ToolError(self.name, f"File not found: {path}", is_recoverable=False)

    return file.read_text(path)
```

### 2. 使用异常分类器

```python
from joyhousebot.utils.exceptions import classify_exception

try:
    response = await api_call()
except Exception as e:
    code, category, should_retry = classify_exception(e)
    if should_retry and retry_count < max_retries:
        await asyncio.sleep(backoff)
        return await api_call()
    else:
        logger.error(f"Failed [{code}]: {e}")
        raise
```

### 3. 过滤敏感信息

```python
from joyhousebot.utils.exceptions import sanitize_error_message

try:
    response = await api_call_with_token(api_key)
except Exception as e:
    safe_msg = sanitize_error_message(str(e))
    logger.error(f"API call failed: {safe_msg}")
```

### 4. 在服务层使用 ServiceError

`joyhousebot.services` 中的服务使用 `ServiceError` 进行业务错误处理：

- **Definition**: `joyhousebot.services.errors.ServiceError(code: str, message: str)`
- **Usage**: 服务层使用它处理无效请求、缺失资源和不可用依赖

### Code → HTTP 映射

API 层（HTTP 和 RPC）捕获异常并映射到 HTTP 状态码：

| code             | HTTP status | 场景 |
|------------------|-------------|--------|
| `NOT_FOUND`      | 404         | 资源（agent、task、session 等）未找到 |
| `INVALID_REQUEST`| 400         | 缺少/无效参数，验证失败 |
| `UNAVAILABLE`    | 503         | Agent 未初始化，依赖服务不可用 |

API 处理器应将 `ServiceError` 或 `JoyhouseBotError` 转换为上述状态码并返回一致的 JSON 响应体（例如 `{"error": {"code": "...", "message": "..."}}`）。

## 已优化的模块

以下模块已集成新的异常处理系统：

| 模块 | 文件 | 优化内容 |
|------|------|----------|
| **核心循环** | [agent/loop.py](../joyhousebot/agent/loop.py) | LLM 错误分类 + 敏感信息过滤 |
| **文件系统** | [agent/tools/filesystem.py](../joyhousebot/agent/tools/filesystem.py) | `@tool_error_handler` + `ToolError` |
| **Shell** | [agent/tools/shell.py](../joyhousebot/agent/tools/shell.py) | 细化错误分类 + 敏感信息过滤 |
| **Web** | [agent/tools/web.py](../joyhousebot/agent/tools/web.py) | `RateLimitError` + `TimeoutError` |
| **代码运行** | [agent/tools/code_runner.py](../joyhousebot/agent/tools/code_runner.py) | `ValidationError` + `TimeoutError` |
| **知识检索** | [agent/tools/retrieve.py](../joyhousebot/agent/tools/retrieve.py) | 错误分类 + 敏感信息过滤 |
| **MCP** | [agent/tools/mcp.py](../joyhousebot/agent/tools/mcp.py) | 连接错误 + 超时处理 |
| **API 层** | [api/server.py](../joyhousebot/api/server.py) | 全局异常处理器 |
| **RPC 边界** | [api/rpc/error_boundary.py](../joyhousebot/api/rpc/error_boundary.py) | `joyhousebot_error_result` + HTTP 状态映射 |
| **通道管理** | [channels/manager.py](../joyhousebot/channels/manager.py) | `ChannelError` + 细化异常处理 |
| **插件管理** | [plugins/manager.py](../joyhousebot/plugins/manager.py) | `PluginError` + 错误分类 |

## 测试覆盖

新增 **52 个异常处理测试**，覆盖：

- 10 个异常类测试
- 7 个敏感信息过滤测试
- 17 个异常分类测试
- 3 个错误格式化测试
- 4 个安全执行测试
- 11 个装饰器测试

测试文件：[tests/test_utils_exceptions.py](../tests/test_utils_exceptions.py)

## 添加新代码

引入新错误代码时：

1. 在本文档中记录代码和用途
2. 确保所有调用该服务的 API 入口点将代码映射到预期的 HTTP 状态
3. 添加对应的测试用例
