"""Discord channel plugin using Discord Gateway websocket."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx
import websockets
from loguru import logger

from joyhousebot.channels.messages_ack import DEFAULT_ACK_REACTION, should_send_ack
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


DISCORD_API_BASE = "https://discord.com/api/v10"
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


class DiscordChannelPlugin(BaseChannelPlugin):
    """Discord channel using Gateway websocket."""

    def __init__(self) -> None:
        super().__init__()
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._seq: int | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._typing_tasks: dict[str, asyncio.Task] = {}
        self._http: httpx.AsyncClient | None = None
        self._bot_id: str | None = None
        self._messages_config: Any = None

    @property
    def id(self) -> str:
        return "discord"

    @property
    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            display_name="Discord",
            description="Discord bot via Gateway websocket",
            icon="discord",
            order=20,
        )

    @property
    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT, ChatType.GROUP, ChatType.THREAD],
            supports_media=True,
            supports_reactions=True,
            supports_typing=True,
            supports_threads=True,
            text_chunk_limit=2000,
        )

    def configure(self, config: dict[str, Any], bus: "MessageBus") -> None:
        super().configure(config, bus)
        self._messages_config = config.get("messages_config")
    
    async def start(self) -> None:
        token = self._config.get("token", "")
        if not token:
            self._log_error("Discord bot token not configured")
            return
        
        self._log_start()
        self._set_running(True)
        self._http = httpx.AsyncClient(timeout=30.0)
        
        gateway_url = self._config.get("gateway_url", "wss://gateway.discord.gg/?v=10&encoding=json")
        
        while self._running:
            try:
                logger.info(f"[{self.id}] Connecting to gateway...")
                self._set_connected(False)
                async with websockets.connect(gateway_url) as ws:
                    self._ws = ws
                    await self._gateway_loop()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log_error("Gateway error", e)
                if self._running:
                    logger.info(f"[{self.id}] Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)
        
        self._log_stopped()

    async def stop(self) -> None:
        self._log_stop()
        self._set_running(False)
        
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        
        for task in self._typing_tasks.values():
            task.cancel()
        self._typing_tasks.clear()
        
        if self._ws:
            await self._ws.close()
            self._ws = None
        
        if self._http:
            await self._http.aclose()
            self._http = None
        
        self._set_connected(False)

    async def send(self, msg: "OutboundMessage") -> SendResult:
        if not self._http:
            return SendResult(success=False, error="HTTP client not initialized")
        
        token = getattr(self._config, "token", "")
        url = f"{DISCORD_API_BASE}/channels/{msg.chat_id}/messages"
        payload: dict[str, Any] = {"content": msg.content}
        
        if msg.reply_to:
            payload["message_reference"] = {"message_id": msg.reply_to}
            payload["allowed_mentions"] = {"replied_user": False}
        
        headers = {"Authorization": f"Bot {token}"}
        sent_ok = False
        message_id = None
        
        try:
            for attempt in range(3):
                try:
                    response = await self._http.post(url, headers=headers, json=payload)
                    if response.status_code == 429:
                        data = response.json()
                        retry_after = float(data.get("retry_after", 1.0))
                        logger.warning(f"[{self.id}] Rate limited, retrying in {retry_after}s")
                        await asyncio.sleep(retry_after)
                        continue
                    response.raise_for_status()
                    sent_ok = True
                    result_data = response.json()
                    message_id = str(result_data.get("id", ""))
                    break
                except Exception as e:
                    if attempt == 2:
                        return SendResult(success=False, error=str(e))
                    await asyncio.sleep(1)
        finally:
            await self._stop_typing(msg.chat_id)
        
        if sent_ok and message_id:
            if self._messages_config and getattr(self._messages_config, "remove_ack_after_reply", False) and msg.reply_to:
                emoji = (getattr(self._messages_config, "ack_reaction", "") or "").strip() or DEFAULT_ACK_REACTION
                try:
                    emoji_param = quote(emoji, safe="") if len(emoji) > 2 else emoji
                    url = f"{DISCORD_API_BASE}/channels/{msg.chat_id}/messages/{msg.reply_to}/reactions/{emoji_param}/@me"
                    await self._http.delete(url, headers={"Authorization": f"Bot {token}"})
                except Exception as e:
                    logger.debug(f"[{self.id}] Remove reaction error: {e}")
            
            return SendResult(success=True, message_id=message_id, metadata={"chat_id": msg.chat_id})
        
        return SendResult(success=False, error="Failed to send message")

    async def _gateway_loop(self) -> None:
        if not self._ws:
            return
        
        async for raw in self._ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"[{self.id}] Invalid JSON: {raw[:100]}")
                continue
            
            op = data.get("op")
            event_type = data.get("t")
            seq = data.get("s")
            payload = data.get("d")
            
            if seq is not None:
                self._seq = seq
            
            if op == 10:
                interval_ms = payload.get("heartbeat_interval", 45000)
                await self._start_heartbeat(interval_ms / 1000)
                await self._identify()
            elif op == 0 and event_type == "READY":
                user = (payload or {}).get("user") or {}
                self._bot_id = str(user.get("id", "")) or None
                logger.info(f"[{self.id}] Gateway READY")
                self._set_connected(True)
            elif op == 0 and event_type == "MESSAGE_CREATE":
                await self._handle_message_create(payload)
            elif op == 7:
                logger.info(f"[{self.id}] Gateway requested reconnect")
                break
            elif op == 9:
                logger.warning(f"[{self.id}] Gateway invalid session")
                break

    async def _identify(self) -> None:
        if not self._ws:
            return
        
        token = getattr(self._config, "token", "")
        intents = getattr(self._config, "intents", 513)
        
        identify = {
            "op": 2,
            "d": {
                "token": token,
                "intents": intents,
                "properties": {
                    "os": "joyhousebot",
                    "browser": "joyhousebot",
                    "device": "joyhousebot",
                },
            },
        }
        await self._ws.send(json.dumps(identify))

    async def _start_heartbeat(self, interval_s: float) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        
        async def heartbeat_loop() -> None:
            while self._running and self._ws:
                payload = {"op": 1, "d": self._seq}
                try:
                    await self._ws.send(json.dumps(payload))
                except Exception as e:
                    logger.warning(f"[{self.id}] Heartbeat failed: {e}")
                    break
                await asyncio.sleep(interval_s)
        
        self._heartbeat_task = asyncio.create_task(heartbeat_loop())

    async def _handle_message_create(self, payload: dict[str, Any]) -> None:
        author = payload.get("author") or {}
        if author.get("bot"):
            return
        
        sender_id = str(author.get("id", ""))
        channel_id = str(payload.get("channel_id", ""))
        content = payload.get("content") or ""
        
        if not sender_id or not channel_id:
            return
        
        if not self.is_allowed(sender_id, self._config):
            return
        
        content_parts = [content] if content else []
        media_paths: list[str] = []
        media_dir = Path.home() / ".joyhousebot" / "media"
        
        for attachment in payload.get("attachments") or []:
            url = attachment.get("url")
            filename = attachment.get("filename") or "attachment"
            size = attachment.get("size") or 0
            if not url or not self._http:
                continue
            if size and size > MAX_ATTACHMENT_BYTES:
                content_parts.append(f"[attachment: {filename} - too large]")
                continue
            try:
                media_dir.mkdir(parents=True, exist_ok=True)
                file_path = media_dir / f"{attachment.get('id', 'file')}_{filename.replace('/', '_')}"
                resp = await self._http.get(url)
                resp.raise_for_status()
                file_path.write_bytes(resp.content)
                media_paths.append(str(file_path))
                content_parts.append(f"[attachment: {file_path}]")
            except Exception as e:
                logger.warning(f"[{self.id}] Failed to download attachment: {e}")
                content_parts.append(f"[attachment: {filename} - download failed]")
        
        message_id = str(payload.get("id", ""))
        reply_to = (payload.get("referenced_message") or {}).get("id")
        guild_id = payload.get("guild_id")
        is_direct = not guild_id
        mentions = payload.get("mentions") or []
        is_mention = (
            is_direct
            or (self._bot_id and any(str(m.get("id")) == self._bot_id for m in mentions))
            or bool(payload.get("referenced_message"))
        )
        
        if self._messages_config and self._messages_config.ack_reaction_scope:
            if should_send_ack(self._messages_config.ack_reaction_scope, is_direct, is_mention):
                emoji = (getattr(self._messages_config, "ack_reaction", "") or "").strip() or DEFAULT_ACK_REACTION
                token = getattr(self._config, "token", "")
                if emoji and self._http and message_id:
                    try:
                        emoji_param = quote(emoji, safe="") if len(emoji) > 2 else emoji
                        url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}/reactions/{emoji_param}/@me"
                        await self._http.put(url, headers={"Authorization": f"Bot {token}"})
                    except Exception as e:
                        logger.debug(f"[{self.id}] Add reaction error: {e}")
        
        await self._start_typing(channel_id)
        
        await self._publish_inbound(
            sender_id=sender_id,
            chat_id=channel_id,
            content="\n".join(p for p in content_parts if p) or "[empty message]",
            media=media_paths,
            metadata={
                "message_id": message_id,
                "guild_id": guild_id,
                "reply_to": reply_to,
            },
        )

    async def _start_typing(self, channel_id: str) -> None:
        await self._stop_typing(channel_id)
        
        token = getattr(self._config, "token", "")
        
        async def typing_loop() -> None:
            url = f"{DISCORD_API_BASE}/channels/{channel_id}/typing"
            headers = {"Authorization": f"Bot {token}"}
            while self._running:
                try:
                    await self._http.post(url, headers=headers)
                except Exception:
                    pass
                await asyncio.sleep(8)
        
        self._typing_tasks[channel_id] = asyncio.create_task(typing_loop())

    async def _stop_typing(self, channel_id: str) -> None:
        task = self._typing_tasks.pop(channel_id, None)
        if task:
            task.cancel()


def create_plugin() -> DiscordChannelPlugin:
    """Factory function to create Discord channel plugin."""
    return DiscordChannelPlugin()
