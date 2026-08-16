"""Official Email channel extension using IMAP polling + SMTP replies."""

from __future__ import annotations

import asyncio
import html
import imaplib
import re
import smtplib
import ssl
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr
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

EMAIL_EXTENSION_MANIFEST = ExtensionManifest(
    extension_id="channel-email",
    version="0.1.0",
    name="Porthouse Email Channel",
    extension_types=("channel",),
    description="IMAP inbound and SMTP outbound for personal and OPC workflows.",
    distribution_name="porthouse-channel-email",
    build_digest=source_tree_digest(__file__),
    required_permissions=("channel.email.read", "channel.email.send"),
    dependencies=(
        {"id": "imap", "kind": "service", "required": False},
        {"id": "smtp", "kind": "service", "required": True},
        {"id": "email-credentials", "kind": "credential", "required": True},
    ),
    configuration_schema={
        "type": "object",
        "required": ["consent_granted", "smtp_host"],
        "properties": {
            "enabled": {"type": "boolean"},
            "consent_granted": {"type": "boolean"},
            "imap_host": {"type": "string"},
            "smtp_host": {"type": "string"},
            "from_address": {"type": "string"},
        },
    },
)


class EmailChannelPlugin(BaseChannelPlugin):
    """Email channel via IMAP polling + SMTP replies."""

    def __init__(self):
        super().__init__()
        self._last_subject_by_chat: dict[str, str] = {}
        self._last_message_id_by_chat: dict[str, str] = {}
        self._processed_uids: set[str] = set()
        self._MAX_PROCESSED_UIDS = 100000
        self._poll_task: asyncio.Task | None = None

    @property
    def id(self) -> str:
        return "email"

    @property
    def extension_manifest(self) -> ExtensionManifest:
        return EMAIL_EXTENSION_MANIFEST

    @property
    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            display_name="Email",
            description="Email channel via IMAP polling + SMTP replies",
            icon="email",
            order=80,
        )

    @property
    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT],
            supports_media=False,
            supports_reactions=False,
            supports_threads=False,
            supports_typing=False,
            text_chunk_limit=10000,
        )

    async def start(self) -> None:
        if not self._config.get("consent_granted"):
            logger.warning(
                "Email channel disabled: consent_granted is false. "
                "Set extensions.settings.channel-email.consentGranted=true after explicit "
                "user permission."
            )
            return

        if not self._validate_outbound_config():
            return

        self._set_running(True)
        self._log_start()
        if not self._inbound_configured():
            logger.info("Email channel started in SMTP outbound-only mode")
            return

        poll_seconds = max(5, int(self._config.get("poll_interval_seconds", 60)))
        self._poll_task = asyncio.create_task(self._poll_loop(poll_seconds))

    async def _poll_loop(self, poll_seconds: int) -> None:
        while self._running:
            try:
                inbound_items = await asyncio.to_thread(self._fetch_new_messages)
                for item in inbound_items:
                    sender = item["sender"]
                    subject = item.get("subject", "")
                    message_id = item.get("message_id", "")

                    if subject:
                        self._last_subject_by_chat[sender] = subject
                    if message_id:
                        self._last_message_id_by_chat[sender] = message_id

                    await self._publish_inbound(
                        sender_id=sender,
                        chat_id=sender,
                        content=item["content"],
                        metadata=item.get("metadata", {}),
                    )
                    uid = str((item.get("metadata") or {}).get("uid") or "")
                    if uid:
                        if self._config.get("mark_seen", True):
                            await asyncio.to_thread(self._mark_uid_seen, uid)
                        self._remember_uid(uid)
            except Exception as e:
                self._log_error(f"Email polling error: {e}")

            await asyncio.sleep(poll_seconds)

    async def stop(self) -> None:
        self._set_running(False)
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("Email channel stopped")

    async def send(self, msg: OutboundMessage) -> SendResult:
        if not self._config.get("consent_granted"):
            logger.warning("Skip email send: consent_granted is false")
            return SendResult(success=False, error="consent_not_granted")

        force_send = bool((msg.metadata or {}).get("force_send"))
        if not self._config.get("auto_reply_enabled", True) and not force_send:
            logger.info("Skip automatic email reply: auto_reply_enabled is false")
            return SendResult(success=False, error="auto_reply_disabled")

        if not self._config.get("smtp_host"):
            logger.warning("Email channel SMTP host not configured")
            return SendResult(success=False, error="smtp_not_configured")

        to_addr = msg.chat_id.strip()
        if not to_addr:
            logger.warning("Email channel missing recipient address")
            return SendResult(success=False, error="missing_recipient")

        base_subject = self._last_subject_by_chat.get(to_addr, "porthouse reply")
        subject = self._reply_subject(base_subject)
        if msg.metadata and isinstance(msg.metadata.get("subject"), str):
            override = msg.metadata["subject"].strip()
            if override:
                subject = override

        email_msg = EmailMessage()
        email_msg["From"] = (
            self._config.get("from_address")
            or self._config.get("smtp_username")
            or self._config.get("imap_username")
        )
        email_msg["To"] = to_addr
        email_msg["Subject"] = subject
        email_msg.set_content(msg.content or "")

        in_reply_to = self._last_message_id_by_chat.get(to_addr)
        if in_reply_to:
            email_msg["In-Reply-To"] = in_reply_to
            email_msg["References"] = in_reply_to

        try:
            await asyncio.to_thread(self._smtp_send, email_msg)
            return SendResult(success=True)
        except Exception as e:
            self._log_error(f"Error sending email to {to_addr}: {e}")
            return SendResult(success=False, error=str(e))

    def _validate_outbound_config(self) -> bool:
        missing = []
        if not self._config.get("smtp_host"):
            missing.append("smtp_host")
        if not self._config.get("smtp_username"):
            missing.append("smtp_username")
        if not self._config.get("smtp_password"):
            missing.append("smtp_password")
        if (
            str(self._config.get("smtp_host") or "").strip().lower()
            == "smtp.resend.com"
            and not self._config.get("from_address")
        ):
            missing.append("from_address")

        if missing:
            logger.error(
                f"Email channel outbound delivery is not configured, missing: {', '.join(missing)}"
            )
            return False
        return True

    def _inbound_configured(self) -> bool:
        return all(
            self._config.get(key)
            for key in ("imap_host", "imap_username", "imap_password")
        )

    def _smtp_send(self, msg: EmailMessage) -> None:
        timeout = 30
        if self._config.get("smtp_use_ssl"):
            with smtplib.SMTP_SSL(
                self._config.get("smtp_host"),
                self._config.get("smtp_port", 465),
                timeout=timeout,
            ) as smtp:
                smtp.login(self._config.get("smtp_username"), self._config.get("smtp_password"))
                smtp.send_message(msg)
            return

        with smtplib.SMTP(
            self._config.get("smtp_host"), self._config.get("smtp_port", 587), timeout=timeout
        ) as smtp:
            if self._config.get("smtp_use_tls", True):
                smtp.starttls(context=ssl.create_default_context())
            smtp.login(self._config.get("smtp_username"), self._config.get("smtp_password"))
            smtp.send_message(msg)

    def _fetch_new_messages(self) -> list[dict[str, Any]]:
        return self._fetch_messages(
            search_criteria=("UNSEEN",),
            dedupe=True,
            limit=0,
        )

    def _remember_uid(self, uid: str) -> None:
        self._processed_uids.add(uid)
        if len(self._processed_uids) > self._MAX_PROCESSED_UIDS:
            self._processed_uids.clear()

    def _mark_uid_seen(self, uid: str) -> None:
        """Acknowledge an IMAP item only after Runtime accepted the message."""
        mailbox = self._config.get("imap_mailbox") or "INBOX"
        if self._config.get("imap_use_ssl", True):
            client = imaplib.IMAP4_SSL(
                self._config.get("imap_host"), self._config.get("imap_port", 993)
            )
        else:
            client = imaplib.IMAP4(
                self._config.get("imap_host"), self._config.get("imap_port", 143)
            )
        try:
            client.login(self._config.get("imap_username"), self._config.get("imap_password"))
            status, _ = client.select(mailbox)
            if status == "OK":
                client.uid("STORE", uid, "+FLAGS", "\\Seen")
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def _fetch_messages(
        self,
        search_criteria: tuple[str, ...],
        dedupe: bool,
        limit: int,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        mailbox = self._config.get("imap_mailbox") or "INBOX"

        if self._config.get("imap_use_ssl", True):
            client = imaplib.IMAP4_SSL(
                self._config.get("imap_host"), self._config.get("imap_port", 993)
            )
        else:
            client = imaplib.IMAP4(
                self._config.get("imap_host"), self._config.get("imap_port", 143)
            )

        try:
            client.login(self._config.get("imap_username"), self._config.get("imap_password"))
            status, _ = client.select(mailbox)
            if status != "OK":
                return messages

            status, data = client.search(None, *search_criteria)
            if status != "OK" or not data:
                return messages

            ids = data[0].split()
            if limit > 0 and len(ids) > limit:
                ids = ids[-limit:]
            for imap_id in ids:
                status, fetched = client.fetch(imap_id, "(BODY.PEEK[] UID)")
                if status != "OK" or not fetched:
                    continue

                raw_bytes = self._extract_message_bytes(fetched)
                if raw_bytes is None:
                    continue

                uid = self._extract_uid(fetched)
                if dedupe and uid and uid in self._processed_uids:
                    continue

                parsed = BytesParser(policy=policy.default).parsebytes(raw_bytes)
                sender = parseaddr(parsed.get("From", ""))[1].strip().lower()
                if not sender:
                    continue

                subject = self._decode_header_value(parsed.get("Subject", ""))
                date_value = parsed.get("Date", "")
                message_id = parsed.get("Message-ID", "").strip()
                body = self._extract_text_body(parsed)

                if not body:
                    body = "(empty email body)"

                max_body_chars = self._config.get("max_body_chars", 10000)
                body = body[:max_body_chars]
                content = (
                    f"Email received.\n"
                    f"From: {sender}\n"
                    f"Subject: {subject}\n"
                    f"Date: {date_value}\n\n"
                    f"{body}"
                )

                metadata = {
                    # Runtime uses this field as the durable inbound idempotency
                    # identity. IMAP UID is stable even when Message-ID is absent.
                    "message_id": message_id or f"imap:{mailbox}:{uid}",
                    "email_message_id": message_id,
                    "subject": subject,
                    "date": date_value,
                    "sender_email": sender,
                    "uid": uid,
                }
                messages.append(
                    {
                        "sender": sender,
                        "subject": subject,
                        "message_id": message_id,
                        "content": content,
                        "metadata": metadata,
                    }
                )

        finally:
            try:
                client.logout()
            except Exception:
                pass

        return messages

    @staticmethod
    def _extract_message_bytes(fetched: list[Any]) -> bytes | None:
        for item in fetched:
            if (
                isinstance(item, tuple)
                and len(item) >= 2
                and isinstance(item[1], (bytes, bytearray))
            ):
                return bytes(item[1])
        return None

    @staticmethod
    def _extract_uid(fetched: list[Any]) -> str:
        for item in fetched:
            if isinstance(item, tuple) and item and isinstance(item[0], (bytes, bytearray)):
                head = bytes(item[0]).decode("utf-8", errors="ignore")
                m = re.search(r"UID\s+(\d+)", head)
                if m:
                    return m.group(1)
        return ""

    @staticmethod
    def _decode_header_value(value: str) -> str:
        if not value:
            return ""
        try:
            return str(make_header(decode_header(value)))
        except Exception:
            return value

    @classmethod
    def _extract_text_body(cls, msg: Any) -> str:
        if msg.is_multipart():
            plain_parts: list[str] = []
            html_parts: list[str] = []
            for part in msg.walk():
                if part.get_content_disposition() == "attachment":
                    continue
                content_type = part.get_content_type()
                try:
                    payload = part.get_content()
                except Exception:
                    payload_bytes = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    payload = payload_bytes.decode(charset, errors="replace")
                if not isinstance(payload, str):
                    continue
                if content_type == "text/plain":
                    plain_parts.append(payload)
                elif content_type == "text/html":
                    html_parts.append(payload)
            if plain_parts:
                return "\n\n".join(plain_parts).strip()
            if html_parts:
                return cls._html_to_text("\n\n".join(html_parts)).strip()
            return ""

        try:
            payload = msg.get_content()
        except Exception:
            payload_bytes = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or "utf-8"
            payload = payload_bytes.decode(charset, errors="replace")
        if not isinstance(payload, str):
            return ""
        if msg.get_content_type() == "text/html":
            return cls._html_to_text(payload).strip()
        return payload.strip()

    @staticmethod
    def _html_to_text(raw_html: str) -> str:
        text = re.sub(r"<\s*br\s*/?>", "\n", raw_html, flags=re.IGNORECASE)
        text = re.sub(r"<\s*/\s*p\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        return html.unescape(text)

    def _reply_subject(self, base_subject: str) -> str:
        subject = (base_subject or "").strip() or "porthouse reply"
        prefix = self._config.get("subject_prefix") or "Re: "
        if subject.lower().startswith("re:"):
            return subject
        return f"{prefix}{subject}"


def create_plugin() -> EmailChannelPlugin:
    return EmailChannelPlugin()
