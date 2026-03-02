"""Telegram channel plugin using python-telegram-bot."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any

from loguru import logger
from telegram import BotCommand, Message, ReactionTypeEmoji, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

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


def _markdown_to_telegram_html(text: str) -> str:
    """Convert markdown to Telegram-safe HTML."""
    if not text:
        return ""
    
    code_blocks: list[str] = []
    def save_code_block(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"
    
    text = re.sub(r'```[\w]*\n?([\s\S]*?)```', save_code_block, text)
    
    inline_codes: list[str] = []
    def save_inline_code(m: re.Match) -> str:
        inline_codes.append(m.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"
    
    text = re.sub(r'`([^`]+)`', save_inline_code, text)
    text = re.sub(r'^#{1,6}\s+(.+)$', r'\1', text, flags=re.MULTILINE)
    text = re.sub(r'^>\s*(.*)$', r'\1', text, flags=re.MULTILINE)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)
    text = re.sub(r'(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])', r'<i>\1</i>', text)
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)
    text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)
    
    for i, code in enumerate(inline_codes):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00IC{i}\x00", f"<code>{escaped}</code>")
    
    for i, code in enumerate(code_blocks):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{escaped}</code></pre>")
    
    return text


class TelegramChannelPlugin(BaseChannelPlugin):
    """Telegram channel using long polling."""
    
    BOT_COMMANDS = [
        BotCommand("start", "Start the bot"),
        BotCommand("new", "Start a new conversation"),
        BotCommand("help", "Show available commands"),
    ]
    
    def __init__(self) -> None:
        super().__init__()
        self._app: Application | None = None
        self._typing_tasks: dict[str, asyncio.Task] = {}
        self._groq_api_key: str = ""
        self._messages_config: Any = None
        self._commands_config: Any = None
    
    @property
    def id(self) -> str:
        return "telegram"
    
    @property
    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            display_name="Telegram",
            description="Telegram bot via long polling",
            icon="telegram",
            order=10,
        )
    
    @property
    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT, ChatType.GROUP, ChatType.CHANNEL],
            supports_media=True,
            supports_reactions=True,
            supports_typing=True,
            text_chunk_limit=4096,
        )
    
    def _native_commands_enabled(self) -> bool:
        override = self._config.get("commands_native")
        if override is not None:
            return override is True or override == "auto"
        if self._commands_config is None:
            return True
        native = getattr(self._commands_config, "native", "auto")
        return native is True or native == "auto"
    
    def configure(self, config: dict[str, Any], bus: "MessageBus") -> None:
        super().configure(config, bus)
        self._groq_api_key = config.get("groq_api_key", "") or ""
        self._messages_config = config.get("messages_config")
        self._commands_config = config.get("commands_config")
    
    async def start(self) -> None:
        token = self._config.get("token", "")
        if not token:
            self._log_error("Telegram bot token not configured")
            return
        
        self._log_start()
        self._set_running(True)
        
        req = HTTPXRequest(connection_pool_size=16, pool_timeout=5.0, connect_timeout=30.0, read_timeout=30.0)
        builder = Application.builder().token(token).request(req).get_updates_request(req)
        proxy = self._config.get("proxy")
        if proxy:
            builder = builder.proxy(proxy).get_updates_proxy(proxy)
        
        self._app = builder.build()
        self._app.add_error_handler(self._on_error)
        
        self._app.add_handler(CommandHandler("start", self._on_start))
        if self._native_commands_enabled():
            self._app.add_handler(CommandHandler("new", self._forward_command))
            self._app.add_handler(CommandHandler("help", self._forward_command))
        
        self._app.add_handler(
            MessageHandler(
                (filters.TEXT | filters.PHOTO | filters.VOICE | filters.AUDIO | filters.Document.ALL)
                & ~filters.COMMAND,
                self._on_message
            )
        )
        
        await self._app.initialize()
        await self._app.start()
        
        bot_info = await self._app.bot.get_me()
        logger.info(f"[{self.id}] Bot @{bot_info.username} connected")
        self._set_connected(True)
        
        if self._native_commands_enabled():
            try:
                await self._app.bot.set_my_commands(self.BOT_COMMANDS)
            except Exception as e:
                logger.warning(f"[{self.id}] Failed to register bot commands: {e}")
        
        await self._app.updater.start_polling(
            allowed_updates=["message"],
            drop_pending_updates=True
        )
        
        self._log_started()
        
        while self._running:
            await asyncio.sleep(1)
    
    async def stop(self) -> None:
        self._log_stop()
        self._set_running(False)
        
        for chat_id in list(self._typing_tasks):
            self._stop_typing(chat_id)
        
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            self._app = None
        
        self._set_connected(False)
        self._log_stopped()
    
    async def send(self, msg: "OutboundMessage") -> SendResult:
        if not self._app:
            return SendResult(success=False, error="Bot not running")
        
        self._stop_typing(msg.chat_id)
        
        try:
            chat_id = int(msg.chat_id)
            html_content = _markdown_to_telegram_html(msg.content)
            reply_to_msg_id: int | None = None
            if msg.reply_to:
                try:
                    reply_to_msg_id = int(msg.reply_to)
                except (TypeError, ValueError):
                    pass
            
            result = await self._app.bot.send_message(
                chat_id=chat_id,
                text=html_content,
                parse_mode="HTML",
                reply_to_message_id=reply_to_msg_id,
            )
            
            if self._messages_config and getattr(self._messages_config, "remove_ack_after_reply", False) and msg.reply_to:
                try:
                    await self._app.bot.set_message_reaction(
                        chat_id=int(msg.chat_id),
                        message_id=int(msg.reply_to),
                        reaction=[],
                    )
                except Exception as e:
                    logger.debug(f"[{self.id}] Remove reaction error: {e}")
            
            return SendResult(
                success=True,
                message_id=str(result.message_id),
                metadata={"chat_id": str(chat_id)}
            )
            
        except ValueError:
            return SendResult(success=False, error=f"Invalid chat_id: {msg.chat_id}")
        except Exception as e:
            logger.warning(f"[{self.id}] HTML parse failed, trying plain text: {e}")
            try:
                reply_to_msg_id = None
                if msg.reply_to:
                    try:
                        reply_to_msg_id = int(msg.reply_to)
                    except (TypeError, ValueError):
                        pass
                result = await self._app.bot.send_message(
                    chat_id=int(msg.chat_id),
                    text=msg.content,
                    reply_to_message_id=reply_to_msg_id,
                )
                return SendResult(success=True, message_id=str(result.message_id))
            except Exception as e2:
                return SendResult(success=False, error=str(e2))
    
    async def send_typing(self, chat_id: str) -> None:
        if self._app:
            self._start_typing(chat_id)
    
    async def _on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        
        user = update.effective_user
        await update.message.reply_text(
            f"👋 Hi {user.first_name}! I'm joyhousebot.\n\n"
            "Send me a message and I'll respond!\n"
            "Type /help to see available commands."
        )
    
    async def _forward_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        await self._publish_inbound(
            sender_id=str(update.effective_user.id),
            chat_id=str(update.message.chat_id),
            content=update.message.text,
        )
    
    def _is_bot_mentioned(self, message: Message) -> bool:
        if not self._app:
            return False
        bot = self._app.bot
        text = message.text or message.caption or ""
        entities = list(message.entities or []) + list(getattr(message, "caption_entities") or [])
        for e in entities:
            if getattr(e, "type", None) == "text_mention" and getattr(e, "user", None):
                if e.user.id == bot.id:
                    return True
            if getattr(e, "type", None) == "mention" and 0 <= e.offset < len(text):
                mention = text[e.offset : e.offset + e.length]
                if bot.username and mention == ("@" + bot.username):
                    return True
        return False
    
    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        
        message = update.message
        user = update.effective_user
        chat_id = message.chat_id
        
        sender_id = str(user.id)
        if user.username:
            sender_id = f"{sender_id}|{user.username}"
        
        content_parts = []
        media_paths = []
        
        if message.text:
            content_parts.append(message.text)
        if message.caption:
            content_parts.append(message.caption)
        
        media_file = None
        media_type = None
        
        if message.photo:
            media_file = message.photo[-1]
            media_type = "image"
        elif message.voice:
            media_file = message.voice
            media_type = "voice"
        elif message.audio:
            media_file = message.audio
            media_type = "audio"
        elif message.document:
            media_file = message.document
            media_type = "file"
        
        if media_file and self._app:
            try:
                file = await self._app.bot.get_file(media_file.file_id)
                ext = self._get_extension(media_type, getattr(media_file, 'mime_type', None))
                
                from pathlib import Path
                media_dir = Path.home() / ".joyhousebot" / "media"
                media_dir.mkdir(parents=True, exist_ok=True)
                
                file_path = media_dir / f"{media_file.file_id[:16]}{ext}"
                await file.download_to_drive(str(file_path))
                media_paths.append(str(file_path))
                
                if media_type in ("voice", "audio") and self._groq_api_key:
                    from joyhousebot.providers.transcription import GroqTranscriptionProvider
                    transcriber = GroqTranscriptionProvider(api_key=self._groq_api_key)
                    transcription = await transcriber.transcribe(file_path)
                    if transcription:
                        logger.info(f"[{self.id}] Transcribed {media_type}: {transcription[:50]}...")
                        content_parts.append(f"[transcription: {transcription}]")
                    else:
                        content_parts.append(f"[{media_type}: {file_path}]")
                else:
                    content_parts.append(f"[{media_type}: {file_path}]")
                    
            except Exception as e:
                logger.error(f"[{self.id}] Failed to download media: {e}")
                content_parts.append(f"[{media_type}: download failed]")
        
        content = "\n".join(content_parts) if content_parts else "[empty message]"
        
        str_chat_id = str(chat_id)
        is_direct = message.chat.type == "private"
        is_mention = is_direct or bool(message.reply_to_message) or self._is_bot_mentioned(message)
        
        metadata = {
            "message_id": message.message_id,
            "user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "is_group": not is_direct,
            "is_direct": is_direct,
            "is_mention": is_mention,
        }
        
        if self._messages_config and self._messages_config.ack_reaction_scope:
            if should_send_ack(self._messages_config.ack_reaction_scope, is_direct, is_mention):
                emoji = (getattr(self._messages_config, "ack_reaction", "") or "").strip() or DEFAULT_ACK_REACTION
                if emoji and self._app:
                    try:
                        await self._app.bot.set_message_reaction(
                            chat_id=chat_id,
                            message_id=message.message_id,
                            reaction=[ReactionTypeEmoji(emoji)],
                        )
                    except Exception as e:
                        logger.debug(f"[{self.id}] Set reaction error: {e}")
        
        self._start_typing(str_chat_id)
        
        await self._publish_inbound(
            sender_id=sender_id,
            chat_id=str_chat_id,
            content=content,
            media=media_paths,
            metadata=metadata,
        )
    
    def _start_typing(self, chat_id: str) -> None:
        self._stop_typing(chat_id)
        self._typing_tasks[chat_id] = asyncio.create_task(self._typing_loop(chat_id))
    
    def _stop_typing(self, chat_id: str) -> None:
        task = self._typing_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()
    
    async def _typing_loop(self, chat_id: str) -> None:
        try:
            while self._app:
                await self._app.bot.send_chat_action(chat_id=int(chat_id), action="typing")
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"[{self.id}] Typing indicator stopped: {e}")
    
    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error(f"[{self.id}] Error: {context.error}")
    
    def _get_extension(self, media_type: str, mime_type: str | None) -> str:
        if mime_type:
            ext_map = {
                "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
                "audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
            }
            if mime_type in ext_map:
                return ext_map[mime_type]
        
        type_map = {"image": ".jpg", "voice": ".ogg", "audio": ".mp3", "file": ""}
        return type_map.get(media_type, "")


def create_plugin() -> TelegramChannelPlugin:
    """Factory function to create Telegram channel plugin."""
    return TelegramChannelPlugin()
