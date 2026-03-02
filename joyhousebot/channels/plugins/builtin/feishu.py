"""Feishu/Lark channel plugin using lark-oapi SDK."""

from __future__ import annotations

import asyncio
import json
import re
import threading
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from loguru import logger

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

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
        CreateMessageReactionRequest,
        CreateMessageReactionRequestBody,
        Emoji,
        P2ImMessageReceiveV1,
    )
    FEISHU_AVAILABLE = True
except ImportError:
    FEISHU_AVAILABLE = False
    lark = None
    Emoji = None

MSG_TYPE_MAP = {
    "image": "[image]",
    "audio": "[audio]",
    "file": "[file]",
    "sticker": "[sticker]",
}


def _extract_post_text(content_json: dict) -> str:
    """Extract plain text from Feishu post message content."""
    def extract_from_lang(lang_content: dict) -> str | None:
        if not isinstance(lang_content, dict):
            return None
        title = lang_content.get("title", "")
        content_blocks = lang_content.get("content", [])
        if not isinstance(content_blocks, list):
            return None
        text_parts = []
        if title:
            text_parts.append(title)
        for block in content_blocks:
            if not isinstance(block, list):
                continue
            for element in block:
                if isinstance(element, dict):
                    tag = element.get("tag")
                    if tag == "text":
                        text_parts.append(element.get("text", ""))
                    elif tag == "a":
                        text_parts.append(element.get("text", ""))
                    elif tag == "at":
                        text_parts.append(f"@{element.get('user_name', 'user')}")
        return " ".join(text_parts).strip() if text_parts else None
    
    if "content" in content_json:
        result = extract_from_lang(content_json)
        if result:
            return result
    
    for lang_key in ("zh_cn", "en_us", "ja_jp"):
        lang_content = content_json.get(lang_key)
        result = extract_from_lang(lang_content)
        if result:
            return result
    
    return ""


class FeishuChannelPlugin(BaseChannelPlugin):
    """Feishu/Lark channel using WebSocket long connection."""

    _TABLE_RE = re.compile(
        r"((?:^[ \t]*\|.+\|[ \t]*\n)(?:^[ \t]*\|[-:\s|]+\|[ \t]*\n)(?:^[ \t]*\|.+\|[ \t]*\n?)+)",
        re.MULTILINE,
    )
    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    _CODE_BLOCK_RE = re.compile(r"(```[\s\S]*?```)", re.MULTILINE)

    def __init__(self) -> None:
        super().__init__()
        self._client: Any = None
        self._ws_client: Any = None
        self._ws_thread: threading.Thread | None = None
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def id(self) -> str:
        return "feishu"

    @property
    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            display_name="Feishu/Lark",
            description="Feishu/Lark bot via WebSocket",
            icon="feishu",
            order=50,
        )

    @property
    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT, ChatType.GROUP],
            supports_media=True,
            supports_reactions=True,
            text_chunk_limit=30000,
        )

    async def start(self) -> None:
        if not FEISHU_AVAILABLE:
            self._log_error("Feishu SDK not installed. Run: pip install lark-oapi")
            return
        
        app_id = self._config.get("app_id", "")
        app_secret = self._config.get("app_secret", "")
        
        if not app_id or not app_secret:
            self._log_error("Feishu app_id and app_secret not configured")
            return
        
        self._log_start()
        self._set_running(True)
        self._loop = asyncio.get_running_loop()
        
        encrypt_key = self._config.get("encrypt_key", "") or ""
        verification_token = self._config.get("verification_token", "") or ""
        
        self._client = lark.Client.builder() \
            .app_id(app_id) \
            .app_secret(app_secret) \
            .log_level(lark.LogLevel.INFO) \
            .build()
        
        event_handler = lark.EventDispatcherHandler.builder(
            encrypt_key, verification_token
        ).register_p2_im_message_receive_v1(
            self._on_message_sync
        ).build()
        
        self._ws_client = lark.ws.Client(
            app_id,
            app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO
        )
        
        def run_ws():
            while self._running:
                try:
                    self._ws_client.start()
                except Exception as e:
                    logger.warning(f"[{self.id}] WebSocket error: {e}")
                if self._running:
                    import time
                    time.sleep(5)
        
        self._ws_thread = threading.Thread(target=run_ws, daemon=True)
        self._ws_thread.start()
        
        self._set_connected(True)
        self._log_started()
        logger.info(f"[{self.id}] No public IP required - using WebSocket to receive events")
        
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        self._log_stop()
        self._set_running(False)
        
        if self._ws_client:
            try:
                self._ws_client.stop()
            except Exception as e:
                logger.warning(f"[{self.id}] Error stopping WebSocket: {e}")
        
        self._set_connected(False)
        self._log_stopped()

    async def send(self, msg: "OutboundMessage") -> SendResult:
        if not self._client:
            return SendResult(success=False, error="Feishu client not initialized")
        
        try:
            if msg.chat_id.startswith("oc_"):
                receive_id_type = "chat_id"
            else:
                receive_id_type = "open_id"
            
            elements = self._build_card_elements(msg.content)
            card = {
                "config": {"wide_screen_mode": True},
                "elements": elements,
            }
            content = json.dumps(card, ensure_ascii=False)
            
            request = CreateMessageRequest.builder() \
                .receive_id_type(receive_id_type) \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(msg.chat_id)
                    .msg_type("interactive")
                    .content(content)
                    .build()
                ).build()
            
            response = self._client.im.v1.message.create(request)
            
            if not response.success():
                logger.error(
                    f"[{self.id}] Failed to send: code={response.code}, msg={response.msg}"
                )
                return SendResult(success=False, error=response.msg)
            
            logger.debug(f"[{self.id}] Message sent to {msg.chat_id}")
            return SendResult(success=True)
            
        except Exception as e:
            self._log_error("Error sending message", e)
            return SendResult(success=False, error=str(e))

    def _on_message_sync(self, data: "P2ImMessageReceiveV1") -> None:
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._on_message(data), self._loop)

    async def _on_message(self, data: "P2ImMessageReceiveV1") -> None:
        try:
            event = data.event
            message = event.message
            sender = event.sender
            
            message_id = message.message_id
            if message_id in self._processed_message_ids:
                return
            self._processed_message_ids[message_id] = None
            
            while len(self._processed_message_ids) > 1000:
                self._processed_message_ids.popitem(last=False)
            
            if sender.sender_type == "bot":
                return
            
            sender_id = sender.sender_id.open_id if sender.sender_id else "unknown"
            chat_id = message.chat_id
            chat_type = message.chat_type
            msg_type = message.message_type
            
            await self._add_reaction(message_id, "THUMBSUP")
            
            if msg_type == "text":
                try:
                    content = json.loads(message.content).get("text", "")
                except json.JSONDecodeError:
                    content = message.content or ""
            elif msg_type == "post":
                try:
                    content_json = json.loads(message.content)
                    content = _extract_post_text(content_json)
                except (json.JSONDecodeError, TypeError):
                    content = message.content or ""
            else:
                content = MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]")
            
            if not content:
                return
            
            reply_to = chat_id if chat_type == "group" else sender_id
            await self._publish_inbound(
                sender_id=sender_id,
                chat_id=reply_to,
                content=content,
                metadata={
                    "message_id": message_id,
                    "chat_type": chat_type,
                    "msg_type": msg_type,
                }
            )
            
        except Exception as e:
            logger.error(f"[{self.id}] Error processing message: {e}")

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        if not self._client or not Emoji:
            return
        
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._add_reaction_sync, message_id, emoji_type)

    def _add_reaction_sync(self, message_id: str, emoji_type: str) -> None:
        try:
            request = CreateMessageReactionRequest.builder() \
                .message_id(message_id) \
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                    .build()
                ).build()
            
            response = self._client.im.v1.message_reaction.create(request)
            
            if not response.success():
                logger.warning(f"[{self.id}] Failed to add reaction: code={response.code}")
        except Exception as e:
            logger.warning(f"[{self.id}] Error adding reaction: {e}")

    @staticmethod
    def _parse_md_table(table_text: str) -> dict | None:
        lines = [l.strip() for l in table_text.strip().split("\n") if l.strip()]
        if len(lines) < 3:
            return None
        split = lambda l: [c.strip() for c in l.strip("|").split("|")]
        headers = split(lines[0])
        rows = [split(l) for l in lines[2:]]
        columns = [{"tag": "column", "name": f"c{i}", "display_name": h, "width": "auto"}
                   for i, h in enumerate(headers)]
        return {
            "tag": "table",
            "page_size": len(rows) + 1,
            "columns": columns,
            "rows": [{f"c{i}": r[i] if i < len(r) else "" for i in range(len(headers))} for r in rows],
        }

    def _build_card_elements(self, content: str) -> list[dict]:
        elements, last_end = [], 0
        for m in self._TABLE_RE.finditer(content):
            before = content[last_end:m.start()]
            if before.strip():
                elements.extend(self._split_headings(before))
            elements.append(self._parse_md_table(m.group(1)) or {"tag": "markdown", "content": m.group(1)})
            last_end = m.end()
        remaining = content[last_end:]
        if remaining.strip():
            elements.extend(self._split_headings(remaining))
        return elements or [{"tag": "markdown", "content": content}]

    def _split_headings(self, content: str) -> list[dict]:
        protected = content
        code_blocks = []
        for m in self._CODE_BLOCK_RE.finditer(content):
            code_blocks.append(m.group(1))
            protected = protected.replace(m.group(1), f"\x00CODE{len(code_blocks)-1}\x00", 1)

        elements = []
        last_end = 0
        for m in self._HEADING_RE.finditer(protected):
            before = protected[last_end:m.start()].strip()
            if before:
                elements.append({"tag": "markdown", "content": before})
            text = m.group(2).strip()
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**{text}**"},
            })
            last_end = m.end()
        remaining = protected[last_end:].strip()
        if remaining:
            elements.append({"tag": "markdown", "content": remaining})

        for i, cb in enumerate(code_blocks):
            for el in elements:
                if el.get("tag") == "markdown":
                    el["content"] = el["content"].replace(f"\x00CODE{i}\x00", cb)

        return elements or [{"tag": "markdown", "content": content}]


def create_plugin() -> FeishuChannelPlugin:
    """Factory function to create Feishu channel plugin."""
    return FeishuChannelPlugin()
