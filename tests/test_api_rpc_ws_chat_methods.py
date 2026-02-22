import pytest

from joyhousebot.api.rpc.ws_chat_methods import (
    cleanup_chat_presence_and_connection,
    process_websocket_chat_message,
    run_chat_ws_loop,
    try_handle_chat_presence_frame,
)


def test_try_handle_chat_presence_frame_updates_presence():
    upserts = []
    handled = try_handle_chat_presence_frame(
        data={"type": "presence", "instanceId": "i1"},
        connection_key="ws_1",
        presence_upsert=lambda *args, **kwargs: upserts.append((args, kwargs)),
    )
    assert handled is True
    assert upserts


@pytest.mark.asyncio
async def test_process_websocket_chat_message_sends_response():
    sent = []

    class _Agent:
        async def process_direct(self, **kwargs):
            return f"ok:{kwargs.get('content')}"

    class _Ws:
        async def send_json(self, payload):
            sent.append(payload)

    await process_websocket_chat_message(
        data={"message": "hi", "session_id": "s1", "agent_id": "a1"},
        resolve_agent=lambda _aid: _Agent(),
        websocket=_Ws(),
        logger_error=lambda _msg: None,
    )
    assert sent and sent[0]["type"] == "response"


def test_cleanup_chat_presence_and_connection():
    removed = []
    disconnected = []
    ws = object()
    key_map = {ws: "k1"}
    cleanup_chat_presence_and_connection(
        websocket=ws,
        ws_to_presence_key=key_map,
        presence_remove_by_connection=lambda key: removed.append(key),
        manager_disconnect=lambda sock: disconnected.append(sock),
    )
    assert removed == ["k1"]
    assert disconnected == [ws]
    assert ws not in key_map


@pytest.mark.asyncio
async def test_run_chat_ws_loop_processes_presence_and_message():
    sent = []
    frames = [
        {"type": "presence", "instanceId": "i1"},
        {"message": "hello", "session_id": "s1", "agent_id": "a1"},
    ]

    class _Agent:
        async def process_direct(self, **kwargs):
            return kwargs.get("content")

    class _Ws:
        async def receive_json(self):
            if frames:
                return frames.pop(0)
            raise StopAsyncIteration

        async def send_json(self, payload):
            sent.append(payload)

    with pytest.raises(StopAsyncIteration):
        await run_chat_ws_loop(
            websocket=_Ws(),
            connection_key="ws_1",
            presence_upsert=lambda *_args, **_kwargs: None,
            resolve_agent=lambda _aid: _Agent(),
            logger_error=lambda _msg: None,
        )
    assert sent and sent[0]["type"] == "response"

