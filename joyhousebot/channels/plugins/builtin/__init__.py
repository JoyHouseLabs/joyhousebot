"""Built-in channel plugins package."""

from joyhousebot.channels.plugins.builtin.telegram import create_plugin as create_telegram
from joyhousebot.channels.plugins.builtin.discord import create_plugin as create_discord
from joyhousebot.channels.plugins.builtin.slack import create_plugin as create_slack
from joyhousebot.channels.plugins.builtin.whatsapp import create_plugin as create_whatsapp
from joyhousebot.channels.plugins.builtin.feishu import create_plugin as create_feishu
from joyhousebot.channels.plugins.builtin.dingtalk import create_plugin as create_dingtalk
from joyhousebot.channels.plugins.builtin.mochat import create_plugin as create_mochat
from joyhousebot.channels.plugins.builtin.email import create_plugin as create_email
from joyhousebot.channels.plugins.builtin.qq import create_plugin as create_qq

BUILTIN_CHANNELS = {
    "telegram": create_telegram,
    "discord": create_discord,
    "slack": create_slack,
    "whatsapp": create_whatsapp,
    "feishu": create_feishu,
    "dingtalk": create_dingtalk,
    "mochat": create_mochat,
    "email": create_email,
    "qq": create_qq,
}

__all__ = [
    "BUILTIN_CHANNELS",
    "create_telegram",
    "create_discord",
    "create_slack",
    "create_whatsapp",
    "create_feishu",
    "create_dingtalk",
    "create_mochat",
    "create_email",
    "create_qq",
]
