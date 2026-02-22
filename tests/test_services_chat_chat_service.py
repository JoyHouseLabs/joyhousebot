import asyncio

import pytest

from joyhousebot.services.chat.chat_service import try_handle_chat_runtime


class _Sessions:
    def __init__(self):
        self._session = type("Session", (), {"messages": []})()

    def get_or_create(self, _key):
        return self._session

    def save(self, _session):
        return None


class _Agent:
    def __init__(self):
        self.sessions = _Sessions()


class _ChatMessage:
    def __init__(self, message, session_id, agent_id):
        self.message = message
        self.session_id = session_id
        self.agent_id = agent_id


@pytest.mark.asyncio
async def test_try_handle_chat_runtime_send_and_history():
    agent = _Agent()

    async def _chat(_msg):
        return {"response": "ok"}

    async def _wait(_run_id, timeout_ms=0):
        return {"status": "ok", "startedAt": 1, "endedAt": 2, "error": None}

    async def _noop(*_args, **_kwargs):
        return None

    send_payload = await try_handle_chat_runtime(
        method="chat.send",
        params={"message": "hello", "expectFinal": True, "idempotencyKey": "run-1"},
        register_agent_job=lambda _rid, session_key=None: True,
        get_running_run_id_for_session=lambda _sk: None,
        complete_agent_job=lambda *_args, **_kwargs: None,
        wait_agent_job=_wait,
        chat=_chat,
        chat_message_cls=_ChatMessage,
        resolve_agent=lambda _aid: agent,
        build_chat_history_payload=lambda s, limit: {"count": len(s.messages), "limit": limit},
        now_iso=lambda: "2026-01-01T00:00:00Z",
        now_ms=lambda: 123,
        emit_event=_noop,
        fanout_chat_to_subscribed_nodes=_noop,
    )
    await asyncio.sleep(0)
    assert send_payload is not None
    assert send_payload["runId"] == "run-1"

    inject_payload = await try_handle_chat_runtime(
        method="chat.inject",
        params={"sessionKey": "s1", "text": "hello"},
        register_agent_job=lambda _rid, session_key=None: True,
        get_running_run_id_for_session=lambda _sk: None,
        complete_agent_job=lambda *_args, **_kwargs: None,
        wait_agent_job=_wait,
        chat=_chat,
        chat_message_cls=_ChatMessage,
        resolve_agent=lambda _aid: agent,
        build_chat_history_payload=lambda s, limit: {"count": len(s.messages), "limit": limit},
        now_iso=lambda: "2026-01-01T00:00:00Z",
        now_ms=lambda: 123,
        emit_event=None,
        fanout_chat_to_subscribed_nodes=_noop,
    )
    assert inject_payload is not None
    assert inject_payload["ok"] is True


@pytest.mark.asyncio
async def test_try_handle_chat_runtime_queue_full_when_lane_rejected():
    """When lane_enqueue returns rejected (queue full), response is queue_full, not in_flight."""
    payload = await try_handle_chat_runtime(
        method="chat.send",
        params={"message": "hi", "sessionKey": "s1", "idempotencyKey": "run-qf"},
        register_agent_job=lambda _rid, session_key=None: True,
        get_running_run_id_for_session=lambda _sk: None,
        complete_agent_job=lambda *_args, **_kwargs: None,
        wait_agent_job=lambda *_args, **_kwargs: None,
        chat=lambda _msg: {"response": "ok"},
        chat_message_cls=_ChatMessage,
        resolve_agent=lambda _aid: _Agent(),
        build_chat_history_payload=lambda s, limit: {"count": 0, "limit": limit},
        now_iso=lambda: "2026-01-01T00:00:00Z",
        now_ms=lambda: 999,
        emit_event=None,
        fanout_chat_to_subscribed_nodes=lambda *_args, **_kwargs: None,
        lane_can_run=lambda _sk: False,
        lane_enqueue=lambda _sk, _rid, p: {"status": "rejected"},
    )
    assert payload is not None
    assert payload["status"] == "queue_full"
    assert payload["ok"] is False
    assert payload.get("code") == "QUEUE_FULL"
    assert payload.get("message")
    assert payload.get("sessionKey") == "s1"
    assert payload.get("runId") == "run-qf"
    assert "in_flight" not in str(payload.get("status", ""))


@pytest.mark.asyncio
async def test_try_handle_chat_runtime_abort_calls_request_abort():
    """chat.abort calls request_abort with runId when provided."""
    requested = []

    async def _noop_emit(_name, _payload):
        pass

    payload = await try_handle_chat_runtime(
        method="chat.abort",
        params={"runId": "run-abort-1", "sessionKey": "s1"},
        register_agent_job=lambda _rid, session_key=None: True,
        get_running_run_id_for_session=lambda _sk: None,
        complete_agent_job=lambda *_args, **_kwargs: None,
        wait_agent_job=lambda *_args, **_kwargs: None,
        chat=lambda _msg: {},
        chat_message_cls=_ChatMessage,
        resolve_agent=lambda _aid: None,
        build_chat_history_payload=lambda s, limit: {},
        now_iso=lambda: "",
        now_ms=lambda: 0,
        emit_event=_noop_emit,
        fanout_chat_to_subscribed_nodes=lambda *_args, **_kwargs: None,
        request_abort=requested.append,
    )
    assert payload is not None
    assert payload.get("ok") is True
    assert payload.get("aborted") is True
    assert requested == ["run-abort-1"]

