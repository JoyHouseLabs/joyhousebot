from email.message import EmailMessage

import pytest
from joyhousebot_channel_email import EmailChannelPlugin

from joyhousebot.bus.events import OutboundMessage


def _make_config() -> dict:
    return {
        "enabled": True,
        "consent_granted": True,
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "imap_username": "bot@example.com",
        "imap_password": "secret",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_username": "bot@example.com",
        "smtp_password": "secret",
        "mark_seen": True,
    }


def _make_plugin(config: dict | None = None) -> EmailChannelPlugin:
    plugin = EmailChannelPlugin()
    class Adapter:
        async def handle(self, message):
            return None

    plugin.configure(config or _make_config(), Adapter())
    return plugin


def _make_raw_email(
    from_addr: str = "alice@example.com",
    subject: str = "Hello",
    body: str = "This is the body.",
) -> bytes:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = "bot@example.com"
    msg["Subject"] = subject
    msg["Message-ID"] = "<m1@example.com>"
    msg.set_content(body)
    return msg.as_bytes()


def test_fetch_new_messages_parses_unseen_and_marks_seen(monkeypatch) -> None:
    raw = _make_raw_email(subject="Invoice", body="Please pay")

    class FakeIMAP:
        def __init__(self) -> None:
            self.store_calls: list[tuple[bytes, str, str]] = []

        def login(self, _user: str, _pw: str):
            return "OK", [b"logged in"]

        def select(self, _mailbox: str):
            return "OK", [b"1"]

        def search(self, *_args):
            return "OK", [b"1"]

        def fetch(self, _imap_id: bytes, _parts: str):
            return "OK", [(b"1 (UID 123 BODY[] {200})", raw), b")"]

        def store(self, imap_id: bytes, op: str, flags: str):
            self.store_calls.append((imap_id, op, flags))
            return "OK", [b""]

        def uid(self, command: str, uid: str, op: str, flags: str):
            self.store_calls.append((uid.encode(), f"{command}:{op}", flags))
            return "OK", [b""]

        def logout(self):
            return "BYE", [b""]

    fake = FakeIMAP()
    monkeypatch.setattr(
        "joyhousebot_channel_email.plugin.imaplib.IMAP4_SSL",
        lambda _h, _p: fake,
    )

    plugin = _make_plugin()
    items = plugin._fetch_new_messages()

    assert len(items) == 1
    assert items[0]["sender"] == "alice@example.com"
    assert items[0]["subject"] == "Invoice"
    assert "Please pay" in items[0]["content"]
    assert items[0]["metadata"]["message_id"] == "<m1@example.com>"
    assert fake.store_calls == []

    plugin._remember_uid("123")
    items_again = plugin._fetch_new_messages()
    assert items_again == []

    plugin._mark_uid_seen("123")
    assert fake.store_calls == [(b"123", "STORE:+FLAGS", "\\Seen")]


def test_missing_message_id_uses_imap_uid_as_runtime_idempotency_key(monkeypatch) -> None:
    raw = _make_raw_email().replace(b"Message-ID: <m1@example.com>\n", b"")

    class FakeIMAP:
        def login(self, *_args):
            return "OK", []

        def select(self, *_args):
            return "OK", []

        def search(self, *_args):
            return "OK", [b"1"]

        def fetch(self, *_args):
            return "OK", [(b"1 (UID 456 BODY[] {200})", raw), b")"]

        def logout(self):
            return "BYE", []

    monkeypatch.setattr(
        "joyhousebot_channel_email.plugin.imaplib.IMAP4_SSL",
        lambda *_args: FakeIMAP(),
    )

    item = _make_plugin()._fetch_new_messages()[0]
    assert item["metadata"]["message_id"] == "imap:INBOX:456"
    assert item["metadata"]["email_message_id"] == ""


def test_extract_text_body_falls_back_to_html() -> None:
    msg = EmailMessage()
    msg["From"] = "alice@example.com"
    msg["To"] = "bot@example.com"
    msg["Subject"] = "HTML only"
    msg.add_alternative("<p>Hello<br>world</p>", subtype="html")

    text = EmailChannelPlugin._extract_text_body(msg)
    assert "Hello" in text
    assert "world" in text


@pytest.mark.asyncio
async def test_start_returns_immediately_without_consent(monkeypatch) -> None:
    config = _make_config()
    config["consent_granted"] = False
    plugin = _make_plugin(config)

    called = {"fetch": False}

    def _fake_fetch():
        called["fetch"] = True
        return []

    monkeypatch.setattr(plugin, "_fetch_new_messages", _fake_fetch)
    await plugin.start()
    assert plugin.is_running is False
    assert called["fetch"] is False


@pytest.mark.asyncio
async def test_start_supports_smtp_outbound_only_without_imap() -> None:
    config = _make_config()
    config.pop("imap_host")
    config.pop("imap_username")
    config.pop("imap_password")
    plugin = _make_plugin(config)

    await plugin.start()

    assert plugin.is_running is True
    assert plugin._poll_task is None
    await plugin.stop()


@pytest.mark.asyncio
async def test_resend_requires_verified_from_address() -> None:
    config = _make_config()
    config.update(
        {
            "smtp_host": "smtp.resend.com",
            "smtp_username": "resend",
            "smtp_password": "secret",
        }
    )
    plugin = _make_plugin(config)

    await plugin.start()

    assert plugin.is_running is False


@pytest.mark.asyncio
async def test_send_uses_smtp_and_reply_subject(monkeypatch) -> None:
    class FakeSMTP:
        def __init__(self, _host: str, _port: int, timeout: int = 30) -> None:
            self.timeout = timeout
            self.started_tls = False
            self.logged_in = False
            self.sent_messages: list[EmailMessage] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def starttls(self, context=None):
            self.started_tls = True

        def login(self, _user: str, _pw: str):
            self.logged_in = True

        def send_message(self, msg: EmailMessage):
            self.sent_messages.append(msg)

    fake_instances: list[FakeSMTP] = []

    def _smtp_factory(host: str, port: int, timeout: int = 30):
        instance = FakeSMTP(host, port, timeout=timeout)
        fake_instances.append(instance)
        return instance

    monkeypatch.setattr(
        "joyhousebot_channel_email.plugin.smtplib.SMTP",
        _smtp_factory,
    )

    plugin = _make_plugin()
    plugin._last_subject_by_chat["alice@example.com"] = "Invoice #42"
    plugin._last_message_id_by_chat["alice@example.com"] = "<m1@example.com>"

    await plugin.send(
        OutboundMessage(
            channel="email",
            chat_id="alice@example.com",
            content="Acknowledged.",
        )
    )

    assert len(fake_instances) == 1
    smtp = fake_instances[0]
    assert smtp.started_tls is True
    assert smtp.logged_in is True
    assert len(smtp.sent_messages) == 1
    sent = smtp.sent_messages[0]
    assert sent["Subject"] == "Re: Invoice #42"
    assert sent["To"] == "alice@example.com"
    assert sent["In-Reply-To"] == "<m1@example.com>"


@pytest.mark.asyncio
async def test_send_skips_when_auto_reply_disabled(monkeypatch) -> None:
    class FakeSMTP:
        def __init__(self, _host: str, _port: int, timeout: int = 30) -> None:
            self.sent_messages: list[EmailMessage] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def starttls(self, context=None):
            return None

        def login(self, _user: str, _pw: str):
            return None

        def send_message(self, msg: EmailMessage):
            self.sent_messages.append(msg)

    fake_instances: list[FakeSMTP] = []

    def _smtp_factory(host: str, port: int, timeout: int = 30):
        instance = FakeSMTP(host, port, timeout=timeout)
        fake_instances.append(instance)
        return instance

    monkeypatch.setattr(
        "joyhousebot_channel_email.plugin.smtplib.SMTP",
        _smtp_factory,
    )

    config = _make_config()
    config["auto_reply_enabled"] = False
    plugin = _make_plugin(config)
    await plugin.send(
        OutboundMessage(
            channel="email",
            chat_id="alice@example.com",
            content="Should not send.",
        )
    )
    assert fake_instances == []

    await plugin.send(
        OutboundMessage(
            channel="email",
            chat_id="alice@example.com",
            content="Force send.",
            metadata={"force_send": True},
        )
    )
    assert len(fake_instances) == 1
    assert len(fake_instances[0].sent_messages) == 1


@pytest.mark.asyncio
async def test_send_skips_when_consent_not_granted(monkeypatch) -> None:
    class FakeSMTP:
        def __init__(self, _host: str, _port: int, timeout: int = 30) -> None:
            self.sent_messages: list[EmailMessage] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def starttls(self, context=None):
            return None

        def login(self, _user: str, _pw: str):
            return None

        def send_message(self, msg: EmailMessage):
            self.sent_messages.append(msg)

    called = {"smtp": False}

    def _smtp_factory(host: str, port: int, timeout: int = 30):
        called["smtp"] = True
        return FakeSMTP(host, port, timeout=timeout)

    monkeypatch.setattr(
        "joyhousebot_channel_email.plugin.smtplib.SMTP",
        _smtp_factory,
    )

    config = _make_config()
    config["consent_granted"] = False
    plugin = _make_plugin(config)
    await plugin.send(
        OutboundMessage(
            channel="email",
            chat_id="alice@example.com",
            content="Should not send.",
            metadata={"force_send": True},
        )
    )
    assert called["smtp"] is False
