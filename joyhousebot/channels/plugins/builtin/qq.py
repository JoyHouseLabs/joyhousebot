"""QQ channel plugin using botpy SDK."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import TYPE_CHECKING, Any

from loguru import logger

from joyhousebot.bus.events import OutboundMessage
from joyhousebot.channels.plugins.base import BaseChannelPlugin
from joyhousebot.channels.plugins.types import (
    ChannelCapabilities,
    ChannelMeta,
    ChatType,
    SendResult,
)

try:
    import botpy
    from botpy.message import C2CMessage

    QQ_AVAILABLE = True
except ImportError:
    QQ_AVAILABLE = False
    botpy = None
    C2CMessage = None

if TYPE_CHECKING:
    from botpy.message import C2CMessage


def _make_bot_class(channel: "QQChannelPlugin") -> "type[botpy.Client]":
    intents = botpy.Intents(public_messages=True, direct_message=True)

    class _Bot(botpy.Client):
        def __init__(self):
            super().__init__(intents=intents)

        async def on_ready(self):
            logger.info(f"QQ bot ready: {self.robot.name}")

        async def on_c2c_message_create(self, message: "C2CMessage"):
            await channel._on_message(message)

        async def on_direct_message_create(self, message):
            await channel._on_message(message)

    return _Bot


class QQChannelPlugin(BaseChannelPlugin):
    """QQ channel via botpy SDK."""

    def __init__(self):
        super().__init__()
        self._client: "botpy.Client | None" = None
        self._processed_ids: deque = deque(maxlen=1000)
        self._bot_task: asyncio.Task | None = None

    @property
    def id(self) -> str:
        return "qq"

    @property
    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            display_name="QQ",
            description="QQ channel via botpy SDK",
            icon="qq",
            order=90,
        )

    @property
    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT],
            supports_media=False,
            supports_reactions=False,
            supports_threads=False,
            supports_typing=False,
            text_chunk_limit=4000,
        )

    async def start(self) -> None:
        if not QQ_AVAILABLE:
            logger.error("QQ SDK not installed. Run: pip install qq-botpy")
            return

        if not self._config.get("app_id") or not self._config.get("secret"):
            logger.error("QQ app_id and secret not configured")
            return

        self._set_running(True)
        self._log_start()

        BotClass = _make_bot_class(self)
        self._client = BotClass()

        self._bot_task = asyncio.create_task(self._run_bot())
        logger.info("QQ bot started (C2C private message)")

    async def _run_bot(self) -> None:
        while self._running:
            try:
                await self._client.start(appid=self._config.get("app_id"), secret=self._config.get("secret"))
            except Exception as e:
                self._log_error(f"QQ bot error: {e}")
            if self._running:
                logger.info("Reconnecting QQ bot in 5 seconds...")
                await asyncio.sleep(5)

    async def stop(self) -> None:
        self._set_running(False)
        if self._bot_task:
            self._bot_task.cancel()
            try:
                await self._bot_task
            except asyncio.CancelledError:
                pass
        logger.info("QQ bot stopped")

    async def send(self, msg: OutboundMessage) -> SendResult:
        if not self._client:
            logger.warning("QQ client not initialized")
            return SendResult(success=False, error="client_not_initialized")
        try:
            await self._client.api.post_c2c_message(
                openid=msg.chat_id,
                msg_type=0,
                content=msg.content,
            )
            return SendResult(success=True)
        except Exception as e:
            self._log_error(f"Error sending QQ message: {e}")
            return SendResult(success=False, error=str(e))

    async def _on_message(self, data: "C2CMessage") -> None:
        try:
            if data.id in self._processed_ids:
                return
            self._processed_ids.append(data.id)

            author = data.author
            user_id = str(getattr(author, 'id', None) or getattr(author, 'user_openid', 'unknown'))
            content = (data.content or "").strip()
            if not content:
                return

            await self._publish_inbound(
                sender_id=user_id,
                chat_id=user_id,
                content=content,
                metadata={"message_id": data.id},
            )
        except Exception as e:
            self._log_error(f"Error handling QQ message: {e}")


def create_plugin() -> QQChannelPlugin:
    return QQChannelPlugin()
