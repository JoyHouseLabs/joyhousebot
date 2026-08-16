"""Optional WhatsApp channel extension using the Node.js bridge."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from loguru import logger

from porthouse.extension_sdk import ExtensionManifest
from porthouse.extension_sdk.channels import (
    BaseChannelPlugin,
    ChannelCapabilities,
    ChannelMeta,
    ChatType,
    OutboundMessage,
    SendResult,
)
from porthouse.extension_sdk.manifest import source_tree_digest
from porthouse_channel_whatsapp.bridge_client import (
    WHATSAPP_BRIDGE_AVAILABLE,
    WhatsAppBridgeClient,
)

WHATSAPP_EXTENSION_MANIFEST = ExtensionManifest(
    extension_id="channel-whatsapp",
    version="0.1.0",
    name="Porthouse WhatsApp Channel",
    extension_types=("channel",),
    description="Optional WhatsApp transport through a separately deployed Node.js bridge.",
    distribution_name="porthouse-channel-whatsapp",
    build_digest=source_tree_digest(__file__),
    required_permissions=("channel.whatsapp.read", "channel.whatsapp.send"),
    dependencies=(
        {"id": "whatsapp-bridge", "kind": "service", "required": True},
        {"id": "whatsapp-bridge-token", "kind": "credential", "required": True},
    ),
    configuration_schema={
        "type": "object",
        "required": ["bridge_url", "bridge_token"],
        "properties": {
            "enabled": {"type": "boolean"},
            "bridge_url": {"type": "string"},
            "bridge_token": {"type": "string", "writeOnly": True},
            "allow_from": {"type": "array", "items": {"type": "string"}},
            "send_timeout_seconds": {"type": "number", "minimum": 1, "maximum": 120},
        },
    },
)


class WhatsAppChannelPlugin(BaseChannelPlugin):
    """WhatsApp channel that connects to a Node.js bridge."""

    def __init__(self) -> None:
        super().__init__()
        self._bridge: WhatsAppBridgeClient | None = None
        self._ws: Any = None
        self._reconnect_count = 0
        self._pending_sends: dict[str, asyncio.Future[dict[str, Any]]] = {}

    @property
    def id(self) -> str:
        return "whatsapp"

    @property
    def extension_manifest(self) -> ExtensionManifest:
        return WHATSAPP_EXTENSION_MANIFEST

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
        if not WHATSAPP_BRIDGE_AVAILABLE:
            self._log_error(
                "WebSocket SDK not installed. Install porthouse-channel-whatsapp"
            )
            return

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

                    self._fail_pending_sends("WhatsApp bridge disconnected")

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._set_connected(False)
                self._ws = None
                self._fail_pending_sends("WhatsApp bridge connection failed")
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

        self._fail_pending_sends("WhatsApp channel stopped")

        self._log_stopped()

    async def send(self, msg: OutboundMessage) -> SendResult:
        if not self._ws or not self._connected:
            return SendResult(success=False, error="WhatsApp bridge not connected")

        try:
            request_id = str(
                (msg.metadata or {}).get("_outbound_id")
                or msg.request_id
                or uuid.uuid4().hex
            )
            pending = asyncio.get_running_loop().create_future()
            self._pending_sends[request_id] = pending
            payload = {
                "type": "send",
                "requestId": request_id,
                "to": msg.chat_id,
                "text": msg.content,
            }
            await self._ws.send(json.dumps(payload))
            timeout = float(self._config.get("send_timeout_seconds", 30.0))
            receipt = await asyncio.wait_for(pending, timeout=timeout)
            if receipt.get("type") == "sent":
                return SendResult(
                    success=True,
                    message_id=str(receipt.get("messageId") or "") or None,
                    metadata={"to": msg.chat_id, "request_id": request_id},
                )
            error = receipt.get("error")
            if isinstance(error, dict):
                error = error.get("message") or error.get("code")
            return SendResult(success=False, error=str(error or "WhatsApp bridge send failed"))
        except TimeoutError:
            return SendResult(success=False, error="WhatsApp bridge send receipt timed out")
        except Exception as e:
            self._log_error("Error sending message", e)
            return SendResult(success=False, error=str(e))
        finally:
            if "request_id" in locals():
                self._pending_sends.pop(request_id, None)

    async def _handle_bridge_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"[{self.id}] Invalid JSON from bridge: {raw[:100]}")
            return

        msg_type = data.get("type")

        if msg_type in {"sent", "error"}:
            request_id = str(data.get("requestId") or "")
            pending = self._pending_sends.get(request_id)
            if pending and not pending.done():
                pending.set_result(data)
            if msg_type == "sent" or pending:
                return

        if msg_type == "message":
            pn = data.get("pn", "")
            sender = data.get("sender", "")
            content = data.get("content", "")

            user_id = pn if pn else sender
            sender_id = user_id.split("@")[0] if "@" in user_id else user_id
            logger.info(f"[{self.id}] Sender {sender}")

            if content == "[Voice Message]":
                logger.info(
                    f"[{self.id}] Voice message from {sender_id}, transcription not available"
                )
                content = "[Voice Message: Transcription not available for WhatsApp yet]"

            await self._publish_inbound(
                sender_id=sender_id,
                chat_id=sender,
                content=content,
                metadata={
                    "message_id": data.get("id"),
                    "timestamp": data.get("timestamp"),
                    "is_group": data.get("isGroup", False),
                },
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
                logger.error(
                    f"[{self.id}] Bridge error: {err.get('message') or err.get('code') or err}"
                )
            else:
                logger.error(f"[{self.id}] Bridge error: {err}")

    def _fail_pending_sends(self, message: str) -> None:
        for pending in self._pending_sends.values():
            if not pending.done():
                pending.set_exception(ConnectionError(message))
        self._pending_sends.clear()


def create_plugin() -> WhatsAppChannelPlugin:
    """Factory function to create WhatsApp channel plugin."""
    return WhatsAppChannelPlugin()
