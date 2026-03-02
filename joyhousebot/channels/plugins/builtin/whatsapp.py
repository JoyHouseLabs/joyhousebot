"""WhatsApp channel plugin using Node.js bridge."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from loguru import logger

from joyhousebot.channels.plugins.base import BaseChannelPlugin
from joyhousebot.channels.plugins.types import (
    ChannelCapabilities,
    ChannelMeta,
    ChatType,
    SendResult,
)
from joyhousebot.channels.whatsapp_bridge_client import WhatsAppBridgeClient

if TYPE_CHECKING:
    from joyhousebot.bus.events import OutboundMessage
    from joyhousebot.bus.queue import MessageBus


class WhatsAppChannelPlugin(BaseChannelPlugin):
    """WhatsApp channel that connects to a Node.js bridge."""

    def __init__(self) -> None:
        super().__init__()
        self._bridge: WhatsAppBridgeClient | None = None
        self._ws: Any = None
        self._reconnect_count = 0

    @property
    def id(self) -> str:
        return "whatsapp"

    @property
    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            display_name="WhatsApp",
            description="WhatsApp via Node.js bridge",
            icon="whatsapp",
            order=40,
        )

    @property
    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT, ChatType.GROUP],
            supports_media=True,
            supports_reactions=True,
            text_chunk_limit=4000,
        )

    async def start(self) -> None:
        bridge_url = self._config.get("bridge_url", "")
        bridge_token = self._config.get("bridge_token", "")
        
        if not bridge_url:
            self._log_error("WhatsApp bridge_url not configured")
            return
        
        self._log_start()
        self._set_running(True)
        
        self._bridge = WhatsAppBridgeClient(
            bridge_url=bridge_url,
            bridge_token=bridge_token,
        )
        
        logger.info(f"[{self.id}] Connecting to bridge at {bridge_url}...")
        
        while self._running:
            try:
                async with self._bridge.connect() as ws:
                    self._ws = ws
                    self._reconnect_count = 0
                    self._set_connected(True)
                    logger.info(f"[{self.id}] Connected to bridge")
                    
                    async for message in ws:
                        try:
                            await self._handle_bridge_message(message)
                        except Exception as e:
                            logger.error(f"[{self.id}] Bridge message error: {e}")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._set_connected(False)
                self._ws = None
                self._reconnect_count += 1
                
                if self._reconnect_count <= 1 or self._reconnect_count % 6 == 0:
                    logger.warning(f"[{self.id}] Bridge connection error: {e}")
                else:
                    logger.debug(f"[{self.id}] Bridge connection error: {e}")
                
                if self._running:
                    if self._reconnect_count <= 1:
                        logger.info(f"[{self.id}] Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)
        
        self._log_stopped()

    async def stop(self) -> None:
        self._log_stop()
        self._set_running(False)
        self._set_connected(False)
        
        if self._ws:
            await self._ws.close()
            self._ws = None
        
        self._log_stopped()

    async def send(self, msg: "OutboundMessage") -> SendResult:
        if not self._ws or not self._connected:
            return SendResult(success=False, error="WhatsApp bridge not connected")
        
        try:
            payload = {
                "type": "send",
                "to": msg.chat_id,
                "text": msg.content
            }
            await self._ws.send(json.dumps(payload))
            return SendResult(success=True, metadata={"to": msg.chat_id})
        except Exception as e:
            self._log_error("Error sending message", e)
            return SendResult(success=False, error=str(e))

    async def _handle_bridge_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"[{self.id}] Invalid JSON from bridge: {raw[:100]}")
            return
        
        msg_type = data.get("type")
        
        if msg_type == "message":
            pn = data.get("pn", "")
            sender = data.get("sender", "")
            content = data.get("content", "")
            
            user_id = pn if pn else sender
            sender_id = user_id.split("@")[0] if "@" in user_id else user_id
            logger.info(f"[{self.id}] Sender {sender}")
            
            if content == "[Voice Message]":
                logger.info(f"[{self.id}] Voice message from {sender_id}, transcription not available")
                content = "[Voice Message: Transcription not available for WhatsApp yet]"
            
            await self._publish_inbound(
                sender_id=sender_id,
                chat_id=sender,
                content=content,
                metadata={
                    "message_id": data.get("id"),
                    "timestamp": data.get("timestamp"),
                    "is_group": data.get("isGroup", False)
                }
            )
        
        elif msg_type == "status":
            status = data.get("status")
            logger.info(f"[{self.id}] Status: {status}")
            
            if status == "connected":
                self._set_connected(True)
            elif status == "disconnected":
                self._set_connected(False)
        
        elif msg_type == "qr":
            logger.info(f"[{self.id}] Scan QR code in the bridge terminal to connect")
        
        elif msg_type == "error":
            err = data.get("error")
            if isinstance(err, dict):
                logger.error(f"[{self.id}] Bridge error: {err.get('message') or err.get('code') or err}")
            else:
                logger.error(f"[{self.id}] Bridge error: {err}")


def create_plugin() -> WhatsAppChannelPlugin:
    """Factory function to create WhatsApp channel plugin."""
    return WhatsAppChannelPlugin()
