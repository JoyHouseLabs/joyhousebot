# Channel 插件扩展示例

本目录包含 Channel 插件扩展示例，展示如何在安装 joyhousebot 后扩展内置插件功能。

## email_media - Email 媒体附件支持

扩展现有的 Email 插件，增加以下功能：
- 接收邮件中的附件并保存到本地
- 发送邮件时可以附带文件
- 支持图片、PDF、文档等常见附件类型

### 使用方法

1. 复制整个 `email_media` 目录到你的插件目录：

```bash
mkdir -p ~/.joyhousebot/plugins/channels
cp -r email_media ~/.joyhousebot/plugins/channels/
```

2. 在 `config.json` 中配置插件目录：

```json
{
  "plugins_dir": "~/.joyhousebot/plugins"
}
```

3. 配置 Email 附件目录（可选）：

```json
{
  "channels": {
    "email": {
      "enabled": true,
      "attachments_dir": "~/.joyhousebot/email_attachments",
      "...": "其他 email 配置"
    }
  }
}
```

### 效果

- 原始 Email 插件 `supports_media: false`
- 扩展后 `supports_media: true`

### 自定义修改

编辑 `plugin.py` 可以：
- 修改 `SUPPORTED_ATTACHMENT_TYPES` 支持更多文件类型
- 修改 `MAX_ATTACHMENT_SIZE` 调整附件大小限制
- 重写 `send()` 方法支持发送附件

## 创建自己的扩展

参考 `email_media/plugin.py` 作为模板，可以扩展任何内置插件：

```python
from joyhousebot.channels.plugins.builtin.XXX import XXXPlugin

class MyXXXPlugin(XXXPlugin):
    @property
    def id(self) -> str:
        return "xxx"  # 相同 id 会覆盖内置插件
    
    # 重写需要修改的方法...

def create_plugin():
    return MyXXXPlugin()
```
