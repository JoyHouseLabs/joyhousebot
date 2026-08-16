"""Optional DingTalk channel extension using Stream Mode."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
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
from porthouse.extension_sdk.network import TrackedAsyncClient

DINGTALK_EXTENSION_MANIFEST = ExtensionManifest(
    extension_id="channel-dingtalk",
    version="0.1.0",
    name="Porthouse DingTalk Channel",
    extension_types=("channel",),
    description="Optional DingTalk Stream transport for Porthouse.",
    distribution_name="porthouse-channel-dingtalk",
    build_digest=source_tree_digest(__file__),
    required_permissions=("channel.dingtalk.read", "channel.dingtalk.send"),
    dependencies=(
        {"id": "dingtalk-open-platform", "kind": "service", "required": True},
        {"id": "dingtalk-credentials", "kind": "credential", "required": True},
    ),
    configuration_schema={
        "type": "object",
        "required": ["client_id", "client_secret"],
        "properties": {
            "enabled": {"type": "boolean"},
            "client_id": {"type": "string"},
            "client_secret": {"type": "string", "writeOnly": True},
            "allow_from": {"type": "array", "items": {"type": "string"}},
        },
    },
)

try:
    from dingtalk_stream import (
        AckMessage,
        CallbackHandler,
        CallbackMessage,
        Credential,
        DingTalkStreamClient,
    )
    from dingtalk_stream.chatbot import ChatbotMessage

    DINGTALK_AVAILABLE = True
except ImportError:
    DINGTALK_AVAILABLE = False
    CallbackHandler = object
    CallbackMessage = None
    AckMessage = None
    ChatbotMessage = None


class PorthousebotDingTalkHandler(CallbackHandler):
    """DingTalk Stream SDK Callback Handler."""

    def __init__(self, plugin: "DingTalkChannelPlugin"):
        super().__init__()
        self.plugin = plugin

    async def process(self, message: CallbackMessage):
        try:
            chatbot_msg = ChatbotMessage.from_dict(message.data)

            content = ""
            if chatbot_msg.text:
                content = chatbot_msg.text.content.strip()
            if not content:
                content = message.data.get("text", {}).get("content", "").strip()

            if not content:
                logger.warning(f"[{self.plugin.id}] Empty or unsupported message type")
                return AckMessage.STATUS_OK, "OK"

            sender_id = chatbot_msg.sender_staff_id or chatbot_msg.sender_id
            sender_name = chatbot_msg.sender_nick or "Unknown"
            message_id = str(
                getattr(chatbot_msg, "message_id", "")
                or message.data.get("msgId")
                or message.data.get("messageId")
                or ""
            )

            logger.info(f"[{self.plugin.id}] Message from {sender_name} ({sender_id}): {content}")

            task = asyncio.create_task(
                self.plugin._on_message(content, sender_id, sender_name, message_id=message_id)
            )
            self.plugin._background_tasks.add(task)
            task.add_done_callback(self.plugin._background_tasks.discard)

            return AckMessage.STATUS_OK, "OK"

        except Exception as e:
            logger.error(f"[{self.plugin.id}] Error processing message: {e}")
            return AckMessage.STATUS_OK, "Error"


class DingTalkChannelPlugin(BaseChannelPlugin):
    """DingTalk channel using Stream Mode."""

    def __init__(self) -> None:
        super().__init__()
        self._client: Any = None
        self._http: httpx.AsyncClient | None = None
        self._access_token: str | None = None
        self._token_expiry: float = 0
        self._background_tasks: set[asyncio.Task] = set()

    @property
    def id(self) -> str:
        return "dingtalk"

    @property
    def extension_manifest(self) -> ExtensionManifest:
        return DINGTALK_EXTENSION_MANIFEST

    @property
    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            display_name="DingTalk",
            description="DingTalk bot via Stream Mode",
            icon="dingtalk",
            order=60,
        )

    @property
    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT],
            supports_media=False,
            text_chunk_limit=20000,
        )

    async def start(self) -> None:
        if not DINGTALK_AVAILABLE:
            self._log_error("DingTalk SDK not installed. Run: pip install dingtalk-stream")
            return

        client_id = self._config.get("client_id", "")
        client_secret = self._config.get("client_secret", "")

        if not client_id or not client_secret:
            self._log_error("DingTalk client_id and client_secret not configured")
            return

        self._log_start()
        self._set_running(True)
        self._http = TrackedAsyncClient()

        logger.info(f"[{self.id}] Initializing with Client ID: {client_id}...")
        credential = Credential(client_id, client_secret)
        self._client = DingTalkStreamClient(credential)

        handler = PorthousebotDingTalkHandler(self)
        self._client.register_callback_handler(ChatbotMessage.TOPIC, handler)

        self._set_connected(True)
        self._log_started()

        while self._running:
            try:
                await self._client.start()
            except Exception as e:
                logger.warning(f"[{self.id}] Stream error: {e}")
            if self._running:
                logger.info(f"[{self.id}] Reconnecting in 5 seconds...")
                await asyncio.sleep(5)

        self._log_stopped()

    async def stop(self) -> None:
        self._log_stop()
        self._set_running(False)

        if self._http:
            await self._http.aclose()
            self._http = None

        for task in self._background_tasks:
            task.cancel()
        self._background_tasks.clear()

        self._set_connected(False)
        self._log_stopped()

    async def send(self, msg: OutboundMessage) -> SendResult:
        token = await self._get_access_token()
        if not token:
            return SendResult(success=False, error="Failed to get access token")

        client_id = self._config_value("client_id", "")
        url = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
        headers = {"x-acs-dingtalk-access-token": token}

        data = {
            "robotCode": client_id,
            "userIds": [msg.chat_id],
            "msgKey": "sampleMarkdown",
            "msgParam": json.dumps(
                {
                    "text": msg.content,
                    "title": "Porthousebot Reply",
                }
            ),
        }

        if not self._http:
            return SendResult(success=False, error="HTTP client not initialized")

        try:
            resp = await self._http.post(url, json=data, headers=headers)
            if resp.status_code != 200:
                logger.error(f"[{self.id}] Send failed: {resp.text}")
                return SendResult(success=False, error=resp.text)

            logger.debug(f"[{self.id}] Message sent to {msg.chat_id}")
            return SendResult(success=True)
        except Exception as e:
            self._log_error("Error sending message", e)
            return SendResult(success=False, error=str(e))

    async def _get_access_token(self) -> str | None:
        if self._access_token and time.time() < self._token_expiry:
            return self._access_token

        client_id = self._config_value("client_id", "")
        client_secret = self._config_value("client_secret", "")

        url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
        data = {
            "appKey": client_id,
            "appSecret": client_secret,
        }

        if not self._http:
            return None

        try:
            resp = await self._http.post(url, json=data)
            resp.raise_for_status()
            res_data = resp.json()
            self._access_token = res_data.get("accessToken")
            self._token_expiry = time.time() + int(res_data.get("expireIn", 7200)) - 60
            return self._access_token
        except Exception as e:
            logger.error(f"[{self.id}] Failed to get access token: {e}")
            return None

    async def _on_message(
        self,
        content: str,
        sender_id: str,
        sender_name: str,
        *,
        message_id: str = "",
    ) -> None:
        try:
            logger.info(f"[{self.id}] Inbound: {content} from {sender_name}")
            await self._publish_inbound(
                sender_id=sender_id,
                chat_id=sender_id,
                content=str(content),
                metadata={
                    "sender_name": sender_name,
                    "platform": "dingtalk",
                    **({"message_id": message_id} if message_id else {}),
                },
            )
        except Exception as e:
            logger.error(f"[{self.id}] Error publishing message: {e}")


def create_plugin() -> DingTalkChannelPlugin:
    """Factory function to create DingTalk channel plugin."""
    return DingTalkChannelPlugin()
