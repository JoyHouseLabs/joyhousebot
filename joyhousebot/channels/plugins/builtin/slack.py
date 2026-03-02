"""Slack channel plugin using Socket Mode."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any

from loguru import logger
from slack_sdk.socket_mode.websockets import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.web.async_client import AsyncWebClient

from joyhousebot.channels.messages_ack import DEFAULT_ACK_REACTION, ack_emoji_for_slack, should_send_ack
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


class SlackChannelPlugin(BaseChannelPlugin):
    """Slack channel using Socket Mode."""

    def __init__(self) -> None:
        super().__init__()
        self._web_client: AsyncWebClient | None = None
        self._socket_client: SocketModeClient | None = None
        self._bot_user_id: str | None = None
        self._messages_config: Any = None

    @property
    def id(self) -> str:
        return "slack"

    @property
    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            display_name="Slack",
            description="Slack bot via Socket Mode",
            icon="slack",
            order=30,
        )

    @property
    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT, ChatType.GROUP, ChatType.THREAD],
            supports_reactions=True,
            supports_threads=True,
            text_chunk_limit=4000,
        )

    def configure(self, config: dict[str, Any], bus: "MessageBus") -> None:
        super().configure(config, bus)
        self._messages_config = config.get("messages_config")
    
    async def start(self) -> None:
        bot_token = self._config.get("bot_token", "")
        app_token = self._config.get("app_token", "")
        mode = self._config.get("mode", "socket")
        
        if not bot_token or not app_token:
            self._log_error("Slack bot/app token not configured")
            return
        if mode != "socket":
            self._log_error(f"Unsupported Slack mode: {mode}")
            return
        
        self._log_start()
        self._set_running(True)
        
        self._web_client = AsyncWebClient(token=bot_token)
        self._socket_client = SocketModeClient(
            app_token=app_token,
            web_client=self._web_client,
        )
        
        self._socket_client.socket_mode_request_listeners.append(self._on_socket_request)
        
        try:
            auth = await self._web_client.auth_test()
            self._bot_user_id = auth.get("user_id")
            logger.info(f"[{self.id}] Bot connected as {self._bot_user_id}")
            self._set_connected(True)
        except Exception as e:
            logger.warning(f"[{self.id}] auth_test failed: {e}")
        
        logger.info(f"[{self.id}] Starting Socket Mode client...")
        await self._socket_client.connect()
        
        self._log_started()
        
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        self._log_stop()
        self._set_running(False)
        
        if self._socket_client:
            try:
                await self._socket_client.close()
            except Exception as e:
                logger.warning(f"[{self.id}] Socket close failed: {e}")
            self._socket_client = None
        
        self._set_connected(False)
        self._log_stopped()

    async def send(self, msg: "OutboundMessage") -> SendResult:
        if not self._web_client:
            return SendResult(success=False, error="Slack client not running")
        
        try:
            slack_meta = msg.metadata.get("slack", {}) if msg.metadata else {}
            thread_ts = slack_meta.get("thread_ts")
            channel_type = slack_meta.get("channel_type")
            use_thread = thread_ts and channel_type != "im"
            
            result = await self._web_client.chat_postMessage(
                channel=msg.chat_id,
                text=msg.content or "",
                thread_ts=thread_ts if use_thread else None,
            )
            
            if self._messages_config and getattr(self._messages_config, "remove_ack_after_reply", False) and msg.reply_to:
                emoji_name = ack_emoji_for_slack(
                    (getattr(self._messages_config, "ack_reaction", "") or "").strip() or DEFAULT_ACK_REACTION
                )
                try:
                    await self._web_client.reactions_remove(
                        channel=msg.chat_id,
                        name=emoji_name,
                        timestamp=msg.reply_to,
                    )
                except Exception as e:
                    logger.debug(f"[{self.id}] reactions_remove error: {e}")
            
            return SendResult(
                success=True,
                message_id=result.get("ts"),
                metadata={"channel": msg.chat_id}
            )
            
        except Exception as e:
            self._log_error("Error sending message", e)
            return SendResult(success=False, error=str(e))

    async def _on_socket_request(
        self,
        client: SocketModeClient,
        req: SocketModeRequest,
    ) -> None:
        if req.type != "events_api":
            return
        
        await client.send_socket_mode_response(
            SocketModeResponse(envelope_id=req.envelope_id)
        )
        
        payload = req.payload or {}
        event = payload.get("event") or {}
        event_type = event.get("type")
        
        if event_type not in ("message", "app_mention"):
            return
        
        sender_id = event.get("user")
        chat_id = event.get("channel")
        
        if event.get("subtype"):
            return
        if self._bot_user_id and sender_id == self._bot_user_id:
            return
        
        text = event.get("text") or ""
        if event_type == "message" and self._bot_user_id and f"<@{self._bot_user_id}>" in text:
            return
        
        if not sender_id or not chat_id:
            return
        
        channel_type = event.get("channel_type") or ""
        
        if not self._is_allowed(sender_id, chat_id, channel_type):
            return
        
        if channel_type != "im" and not self._should_respond_in_channel(event_type, text, chat_id):
            return
        
        text = self._strip_bot_mention(text)
        
        thread_ts = event.get("thread_ts") or event.get("ts")
        ts = event.get("ts")
        is_direct = channel_type == "im"
        is_mention = is_direct or (event_type == "app_mention") or (
            bool(self._bot_user_id and f"<@{self._bot_user_id}>" in text)
        )
        
        if self._messages_config and self._messages_config.ack_reaction_scope:
            if should_send_ack(self._messages_config.ack_reaction_scope, is_direct, is_mention):
                emoji_name = ack_emoji_for_slack(
                    (getattr(self._messages_config, "ack_reaction", "") or "").strip() or DEFAULT_ACK_REACTION
                )
                if emoji_name and self._web_client and ts:
                    try:
                        await self._web_client.reactions_add(
                            channel=chat_id,
                            name=emoji_name,
                            timestamp=ts,
                        )
                    except Exception as e:
                        logger.debug(f"[{self.id}] reactions_add error: {e}")
        
        await self._publish_inbound(
            sender_id=sender_id,
            chat_id=chat_id,
            content=text,
            metadata={
                "message_id": ts,
                "slack": {
                    "event": event,
                    "thread_ts": thread_ts,
                    "channel_type": channel_type,
                },
            },
        )

    def _is_allowed(self, sender_id: str, chat_id: str, channel_type: str) -> bool:
        dm_config = getattr(self._config, "dm", None)
        group_policy = getattr(self._config, "group_policy", "open")
        group_allow_from = getattr(self._config, "group_allow_from", []) or []
        
        if channel_type == "im":
            if dm_config and not getattr(dm_config, "enabled", True):
                return False
            if dm_config and getattr(dm_config, "policy", "open") == "allowlist":
                allow_from = getattr(dm_config, "allow_from", []) or []
                return sender_id in allow_from
            return True
        
        if group_policy == "allowlist":
            return chat_id in group_allow_from
        return True

    def _should_respond_in_channel(self, event_type: str, text: str, chat_id: str) -> bool:
        group_policy = getattr(self._config, "group_policy", "open")
        group_allow_from = getattr(self._config, "group_allow_from", []) or []
        
        if group_policy == "open":
            return True
        if group_policy == "mention":
            if event_type == "app_mention":
                return True
            return self._bot_user_id is not None and f"<@{self._bot_user_id}>" in text
        if group_policy == "allowlist":
            return chat_id in group_allow_from
        return False

    def _strip_bot_mention(self, text: str) -> str:
        if not text or not self._bot_user_id:
            return text
        return re.sub(rf"<@{re.escape(self._bot_user_id)}>\s*", "", text).strip()


def create_plugin() -> SlackChannelPlugin:
    """Factory function to create Slack channel plugin."""
    return SlackChannelPlugin()
