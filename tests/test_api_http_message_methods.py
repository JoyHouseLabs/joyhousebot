import pytest
from fastapi import HTTPException

from joyhousebot.api.http.message_methods import prepare_direct_message_send, publish_direct_message


class _Body:
    def __init__(self, *, channel="wx", target="u1", message="hello", reply_to=None, metadata=None):
        self.channel = channel
        self.target = target
        self.message = message
        self.reply_to = reply_to
        self.metadata = metadata


class _ChannelManager:
    def __init__(self, *, enabled=True):
        self.enabled = enabled

    def get_channel(self, _channel: str):
        return object() if self.enabled else None


def test_prepare_direct_message_send_success():
    bus = object()
    body = _Body(channel=" WX ", target=" u1 ", message="hi", metadata={"k": "v"})
    app_state = {"message_bus": bus, "channel_manager": _ChannelManager(enabled=True)}
    prepared_bus, channel, target, msg = prepare_direct_message_send(body=body, app_state=app_state)
    assert prepared_bus is bus
    assert channel == "wx"
    assert target == "u1"
    assert msg.channel == "wx"
    assert msg.chat_id == "u1"
    assert msg.content == "hi"
    assert msg.metadata == {"k": "v"}


@pytest.mark.parametrize(
    ("body", "status_code", "detail"),
    [
        (_Body(channel="  "), 400, "channel is required"),
        (_Body(target=" "), 400, "target is required"),
        (_Body(message="  "), 400, "message is required"),
    ],
)
def test_prepare_direct_message_send_bad_input(body, status_code, detail):
    with pytest.raises(HTTPException) as exc:
        prepare_direct_message_send(
            body=body,
            app_state={"message_bus": object(), "channel_manager": _ChannelManager(enabled=True)},
        )
    assert exc.value.status_code == status_code
    assert exc.value.detail == detail


def test_prepare_direct_message_send_missing_bus():
    with pytest.raises(HTTPException) as exc:
        prepare_direct_message_send(body=_Body(), app_state={"channel_manager": _ChannelManager(enabled=True)})
    assert exc.value.status_code == 503
    assert exc.value.detail == "Message bus not initialized"


def test_prepare_direct_message_send_missing_manager():
    with pytest.raises(HTTPException) as exc:
        prepare_direct_message_send(body=_Body(), app_state={"message_bus": object()})
    assert exc.value.status_code == 503
    assert exc.value.detail == "Direct outbound requires gateway mode"


def test_prepare_direct_message_send_channel_not_enabled():
    with pytest.raises(HTTPException) as exc:
        prepare_direct_message_send(
            body=_Body(),
            app_state={"message_bus": object(), "channel_manager": _ChannelManager(enabled=False)},
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "Channel not enabled: wx"


class _Bus:
    def __init__(self, *, fail: bool = False):
        self.fail = fail

    async def publish_outbound(self, _msg):
        if self.fail:
            raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_publish_direct_message_success():
    payload = await publish_direct_message(
        bus=_Bus(),
        channel="wx",
        target="u1",
        msg=object(),
        message_text="hello",
        logger_error=lambda _msg: None,
    )
    assert payload == {
        "ok": True,
        "queued": True,
        "channel": "wx",
        "target": "u1",
        "message_length": 5,
    }


@pytest.mark.asyncio
async def test_publish_direct_message_failure_raises_http_500():
    with pytest.raises(HTTPException) as exc:
        await publish_direct_message(
            bus=_Bus(fail=True),
            channel="wx",
            target="u1",
            msg=object(),
            message_text="hello",
            logger_error=lambda _msg: None,
        )
    assert exc.value.status_code == 500
    assert exc.value.detail == "boom"

