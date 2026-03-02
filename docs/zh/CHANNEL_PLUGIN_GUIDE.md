# Channel 插件系统

joyhousebot 的 Channel 系统采用插件架构，支持多种聊天平台的接入。本文档介绍如何使用和扩展 Channel 插件。

## 架构概述

```
joyhousebot/channels/plugins/
├── __init__.py          # 包导出
├── types.py             # 核心类型定义
├── registry.py          # 插件注册表
├── base.py              # 基础插件类
└── builtin/             # 内置插件
    ├── telegram.py
    ├── discord.py
    ├── slack.py
    ├── whatsapp.py
    ├── feishu.py
    ├── dingtalk.py
    ├── mochat.py
    ├── email.py
    └── qq.py
```

### 核心组件

| 组件 | 说明 |
|------|------|
| `ChannelPlugin` | 抽象基类，定义插件接口 |
| `ChannelRegistry` | 全局注册表，管理插件加载 |
| `BaseChannelPlugin` | 提供通用工具方法 |
| `ChannelCapabilities` | 描述插件支持的功能 |
| `ChannelMeta` | 插件元数据 |

## 内置 Channel 插件

| 插件 | 聊天类型 | 媒体支持 | 说明 |
|------|---------|---------|------|
| Telegram | 私聊、群组、频道 | ✅ | 通过 Long Polling |
| Discord | 私聊、群组、线程 | ✅ | 通过 Gateway WebSocket |
| Slack | 私聊、群组、线程 | ❌ | 通过 Socket Mode |
| WhatsApp | 私聊、群组 | ✅ | 通过 Node.js Bridge |
| Feishu/飞书 | 私聊、群组 | ✅ | 通过 lark-oapi SDK |
| DingTalk/钉钉 | 私聊 | ❌ | 通过 Stream Mode |
| Mochat | 私聊、群组 | ❌ | 通过 Socket.IO |
| Email | 私聊 | ❌ | IMAP + SMTP |
| QQ | 私聊 | ❌ | 通过 botpy SDK |

## 扩展 Channel 插件

### 方式一：外部插件覆盖（推荐）

用户可以在安装 joyhousebot 后，通过创建外部插件来扩展或覆盖内置插件功能。

#### 目录结构

```
~/.joyhousebot/plugins/
└── channels/
    └── my_email/           # 任意目录名
        └── plugin.py       # 必须包含 create_plugin() 函数
```

#### 配置

在 `config.json` 中添加：

```json
{
  "plugins_dir": "~/.joyhousebot/plugins"
}
```

#### 插件模板

```python
# ~/.joyhousebot/plugins/channels/my_email/plugin.py

from __future__ import annotations
from typing import Any, TYPE_CHECKING

from joyhousebot.channels.plugins.builtin.email import EmailChannelPlugin
from joyhousebot.channels.plugins.types import (
    ChannelCapabilities,
    ChannelMeta,
    ChatType,
)

if TYPE_CHECKING:
    from joyhousebot.bus.queue import MessageBus


class MyEmailPlugin(EmailChannelPlugin):
    """扩展 Email 插件，增加自定义功能"""
    
    @property
    def id(self) -> str:
        # 返回相同的 id 会覆盖内置插件
        return "email"
    
    @property
    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            display_name="Email (Enhanced)",
            description="Email channel with enhanced features",
            icon="email",
            order=80,
        )
    
    @property
    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT],
            supports_media=True,  # 新增媒体支持
            supports_reactions=False,
            supports_threads=False,
            supports_typing=False,
            text_chunk_limit=10000,
        )
    
    def configure(self, config: dict[str, Any], bus: "MessageBus") -> None:
        super().configure(config, bus)
        # 添加自定义配置处理
        self._custom_setting = config.get("custom_setting", "default")
    
    async def start(self) -> None:
        # 可以在启动前后添加自定义逻辑
        await super().start()
        print(f"Custom email plugin started with: {self._custom_setting}")


def create_plugin() -> MyEmailPlugin:
    """工厂函数，必须定义"""
    return MyEmailPlugin()
```

### 方式二：创建全新 Channel

如果需要接入全新的平台（如微信、Line 等），可以从零创建插件：

```python
# ~/.joyhousebot/plugins/channels/wechat/plugin.py

from __future__ import annotations
import asyncio
from typing import Any, TYPE_CHECKING

from joyhousebot.channels.plugins.base import BaseChannelPlugin
from joyhousebot.channels.plugins.types import (
    ChannelCapabilities,
    ChannelMeta,
    ChatType,
    SendResult,
)

if TYPE_CHECKING:
    from joyhousebot.bus.events import OutboundMessage
    from joyhousebot.bus.queue import MessageBus


class WeChatChannelPlugin(BaseChannelPlugin):
    """微信 Channel 插件"""
    
    def __init__(self):
        super().__init__()
        self._client = None
    
    @property
    def id(self) -> str:
        return "wechat"
    
    @property
    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            display_name="WeChat",
            description="WeChat channel",
            icon="wechat",
            order=100,
        )
    
    @property
    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT, ChatType.GROUP],
            supports_media=True,
            supports_reactions=False,
            text_chunk_limit=4000,
        )
    
    async def start(self) -> None:
        token = self._config.get("token")
        if not token:
            self._log_error("WeChat token not configured")
            return
        
        self._set_running(True)
        self._log_start()
        
        # 初始化微信客户端...
        # self._client = WeChatClient(token)
        
        self._log_started()
    
    async def stop(self) -> None:
        self._set_running(False)
        self._log_stopped()
    
    async def send(self, msg: OutboundMessage) -> SendResult:
        try:
            # 发送消息逻辑...
            # await self._client.send(msg.chat_id, msg.content)
            return SendResult(success=True)
        except Exception as e:
            self._log_error(f"Send error: {e}")
            return SendResult(success=False, error=str(e))


def create_plugin() -> WeChatChannelPlugin:
    return WeChatChannelPlugin()
```

## 插件接口详解

### ChannelPlugin 抽象方法

| 方法 | 必须实现 | 说明 |
|------|---------|------|
| `id` | ✅ | 唯一标识符，如 "telegram" |
| `meta` | ✅ | 元数据（名称、描述、图标等） |
| `capabilities` | ✅ | 功能描述 |
| `start()` | ✅ | 启动插件 |
| `stop()` | ✅ | 停止插件 |
| `send(msg)` | ✅ | 发送消息 |

### 可选方法

| 方法 | 说明 |
|------|------|
| `configure(config, bus)` | 配置插件，在 start() 前调用 |
| `send_typing(chat_id)` | 发送输入状态 |
| `send_reaction(chat_id, msg_id, emoji)` | 发送表情反应 |
| `is_allowed(sender_id, config)` | 检查发送者权限 |

### ChannelCapabilities 字段

```python
@dataclass
class ChannelCapabilities:
    chat_types: list[ChatType]      # 支持的聊天类型
    supports_media: bool            # 支持发送媒体文件
    supports_reactions: bool        # 支持消息反应
    supports_threads: bool          # 支持线程回复
    supports_typing: bool           # 支持输入状态
    supports_polls: bool            # 支持投票
    supports_edit: bool             # 支持编辑消息
    supports_delete: bool           # 支持删除消息
    text_chunk_limit: int           # 单条消息最大字符数
    streaming: bool                 # 支持流式输出
```

### ChatType 枚举

```python
class ChatType(str, Enum):
    DIRECT = "direct"     # 私聊
    GROUP = "group"       # 群组
    CHANNEL = "channel"   # 频道
    THREAD = "thread"     # 线程
```

## BaseChannelPlugin 工具方法

继承 `BaseChannelPlugin` 可以使用以下工具方法：

```python
class MyPlugin(BaseChannelPlugin):
    async def start(self) -> None:
        self._log_start()           # 记录启动日志
        self._set_running(True)     # 设置运行状态
        self._set_connected(True)   # 设置连接状态
        self._log_started()         # 记录启动完成
        
    async def on_message(self, data):
        # 发布入站消息到消息总线
        await self._publish_inbound(
            sender_id="user123",
            chat_id="chat456",
            content="Hello",
            media=["/path/to/file.jpg"],
            metadata={"custom": "data"},
        )
        
    def on_error(self, e):
        self._log_error("Something went wrong", e)
        self._last_error = str(e)
```

## 配置说明

Channel 插件的配置来自 `config.json` 中的对应字段：

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allow_from": ["123456789"]
    },
    "email": {
      "enabled": true,
      "imap_host": "imap.gmail.com",
      "imap_username": "user@gmail.com",
      "imap_password": "app_password"
    }
  }
}
```

配置会通过 `configure(config_dict, bus)` 传递给插件，插件通过 `self._config.get(key)` 访问。

## 示例：扩展 Email 增加媒体支持

完整示例见 `examples/channel-plugins/email_media/plugin.py`

该示例展示如何：
1. 继承内置 `EmailChannelPlugin`
2. 重写 `capabilities` 增加媒体支持
3. 重写 `_extract_text_body` 提取附件
4. 保存附件到本地并返回路径

## 调试技巧

### 测试插件加载

```python
from joyhousebot.channels.plugins import get_channel_registry, reset_channel_registry

reset_channel_registry()
registry = get_channel_registry()

# 加载内置插件
registry.load_all_builtins()

# 加载外部插件
from pathlib import Path
registry.load_from_directory(Path("~/.joyhousebot/plugins/channels").expanduser())

# 检查插件
for channel_id in registry.list_channels():
    plugin = registry.get(channel_id)
    print(f"{channel_id}: {plugin.meta.display_name}")
    print(f"  supports_media: {plugin.capabilities.supports_media}")
```

### 日志级别

设置环境变量启用调试日志：

```bash
export LOGURU_LEVEL=DEBUG
```

## 相关文档

- [PLUGIN_COMPARISON.md](./PLUGIN_COMPARISON.md) - 插件系统对比
- [NATIVE_PLUGIN_GUIDE.md](./NATIVE_PLUGIN_GUIDE.md) - Native 插件开发指南
- [HOOK_SYSTEM_DESIGN.md](./HOOK_SYSTEM_DESIGN.md) - Hook 系统设计
