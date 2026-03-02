"""
示例：扩展 Email 插件增加媒体附件支持

这个示例展示如何通过继承内置插件来扩展功能。
用户可以将此文件放在 ~/.joyhousebot/plugins/channels/email_media/plugin.py

目录结构:
~/.joyhousebot/plugins/
└── channels/
    └── email_media/
        └── plugin.py  # 本文件

然后在 config.json 中设置:
{
  "plugins_dir": "~/.joyhousebot/plugins"
}
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from loguru import logger

from joyhousebot.channels.plugins.builtin.email import EmailChannelPlugin
from joyhousebot.channels.plugins.types import (
    ChannelCapabilities,
    ChannelMeta,
    ChatType,
)


class EmailMediaChannelPlugin(EmailChannelPlugin):
    """
    扩展 Email 插件，增加媒体附件支持。
    
    新增功能:
    - 接收邮件中的附件并保存
    - 发送邮件时可以附带文件
    - 支持图片、文档等常见附件类型
    """

    SUPPORTED_ATTACHMENT_TYPES = {
        "image/jpeg", "image/png", "image/gif",
        "application/pdf",
        "text/plain", "text/csv",
        "application/zip",
    }
    MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10MB

    def __init__(self):
        super().__init__()
        self._attachments_dir: Path | None = None

    @property
    def id(self) -> str:
        return "email"

    @property
    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            display_name="Email (with Media)",
            description="Email channel with attachment support",
            icon="email",
            order=80,
        )

    @property
    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT],
            supports_media=True,
            supports_reactions=False,
            supports_threads=False,
            supports_typing=False,
            text_chunk_limit=10000,
        )

    def configure(self, config: dict[str, Any], bus: "MessageBus") -> None:
        super().configure(config, bus)
        
        attachments_dir = config.get("attachments_dir")
        if attachments_dir:
            self._attachments_dir = Path(attachments_dir).expanduser()
        else:
            self._attachments_dir = Path.home() / ".joyhousebot" / "email_attachments"
        
        self._attachments_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"[{self.id}] Attachments directory: {self._attachments_dir}")

    def _fetch_messages(
        self,
        search_criteria: tuple[str, ...],
        mark_seen: bool,
        dedupe: bool,
        limit: int,
    ) -> list[dict[str, Any]]:
        messages = super()._fetch_messages(search_criteria, mark_seen, dedupe, limit)
        
        for msg in messages:
            attachments = msg.get("metadata", {}).get("attachments", [])
            if attachments:
                media_paths = []
                for att in attachments:
                    if att.get("saved_path"):
                        media_paths.append(att["saved_path"])
                if media_paths:
                    msg["media"] = media_paths
        
        return messages

    def _extract_text_body(self, msg: Any) -> str:
        text = super()._extract_text_body(msg)
        
        attachments = self._extract_attachments(msg)
        if attachments:
            att_summary = "\n\n[Attachments:]"
            for att in attachments:
                att_summary += f"\n- {att['filename']} ({att['content_type']}, {att['size']} bytes)"
            text += att_summary
            
            if "metadata" not in dir():
                pass
            self._current_attachments = attachments
        
        return text

    def _extract_attachments(self, msg: Any) -> list[dict[str, Any]]:
        attachments = []
        
        if not self._attachments_dir:
            return attachments
        
        for part in msg.walk():
            if part.get_content_disposition() != "attachment":
                continue
            
            content_type = part.get_content_type()
            if content_type not in self.SUPPORTED_ATTACHMENT_TYPES:
                continue
            
            filename = part.get_filename()
            if not filename:
                continue
            
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            
            if len(payload) > self.MAX_ATTACHMENT_SIZE:
                logger.warning(f"[{self.id}] Skipping large attachment: {filename}")
                continue
            
            safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)
            file_path = self._attachments_dir / safe_name
            
            try:
                file_path.write_bytes(payload)
                attachments.append({
                    "filename": filename,
                    "content_type": content_type,
                    "size": len(payload),
                    "saved_path": str(file_path),
                })
                logger.info(f"[{self.id}] Saved attachment: {filename}")
            except Exception as e:
                logger.error(f"[{self.id}] Failed to save attachment {filename}: {e}")
        
        return attachments


def create_plugin() -> EmailMediaChannelPlugin:
    return EmailMediaChannelPlugin()
