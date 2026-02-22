import asyncio

import pytest

from joyhousebot.api.rpc.chat_methods import try_handle_chat_runtime_method


def _rpc_error(code: str, message: str, data=None):
    return {"code": code, "message": message, "data": data}


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
async def test_chat_send_expect_final():
    completions = []

    async def _chat(_msg):
        return {"response": "ok"}

    async def _fanout(_session_key, _payload):
        return None

    async def _emit(_event, _payload):
        return None

    async def _wait(_run_id, timeout_ms=0):
        return {"status": "ok", "startedAt": 1, "endedAt": 2, "error": None}

    def _complete(run_id, **kwargs):
        completions.append((run_id, kwargs))

    res = await try_handle_chat_runtime_method(
        method="chat.send",
        params={"message": "hi", "expectFinal": True, "idempotencyKey": "r1"},
        rpc_error=_rpc_error,
        register_agent_job=lambda _rid, session_key=None: True,
        get_running_run_id_for_session=lambda _sk: None,
        complete_agent_job=_complete,
        wait_agent_job=_wait,
        chat=_chat,
        chat_message_cls=_ChatMessage,
        resolve_agent=lambda _aid: _Agent(),
        build_chat_history_payload=lambda _s, _l: {"entries": []},
        now_iso=lambda: "2026-01-01T00:00:00",
        now_ms=lambda: 100,
        emit_event=_emit,
        fanout_chat_to_subscribed_nodes=_fanout,
    )
    await asyncio.sleep(0)
    assert res is not None and res[0] is True
    assert res[1]["runId"] == "r1"
    assert completions and completions[0][1]["status"] == "ok"


@pytest.mark.asyncio
async def test_chat_inject_and_history():
    agent = _Agent()

    async def _noop(*_args, **_kwargs):
        return None

    inject = await try_handle_chat_runtime_method(
        method="chat.inject",
        params={"sessionKey": "s1", "text": "hello", "role": "user"},
        rpc_error=_rpc_error,
        register_agent_job=lambda _rid, session_key=None: True,
        get_running_run_id_for_session=lambda _sk: None,
        complete_agent_job=lambda *_args, **_kwargs: None,
        wait_agent_job=lambda *_args, **_kwargs: _noop(),
        chat=lambda *_args, **_kwargs: _noop(),
        chat_message_cls=_ChatMessage,
        resolve_agent=lambda _aid: agent,
        build_chat_history_payload=lambda s, limit: {"count": len(s.messages), "limit": limit},
        now_iso=lambda: "2026-01-01T00:00:00",
        now_ms=lambda: 100,
        emit_event=None,
        fanout_chat_to_subscribed_nodes=lambda *_args, **_kwargs: _noop(),
    )
    assert inject is not None and inject[0] is True

    history = await try_handle_chat_runtime_method(
        method="chat.history",
        params={"sessionKey": "s1", "limit": 10},
        rpc_error=_rpc_error,
        register_agent_job=lambda _rid, session_key=None: True,
        get_running_run_id_for_session=lambda _sk: None,
        complete_agent_job=lambda *_args, **_kwargs: None,
        wait_agent_job=lambda *_args, **_kwargs: _noop(),
        chat=lambda *_args, **_kwargs: _noop(),
        chat_message_cls=_ChatMessage,
        resolve_agent=lambda _aid: agent,
        build_chat_history_payload=lambda s, limit: {"count": len(s.messages), "limit": limit},
        now_iso=lambda: "2026-01-01T00:00:00",
        now_ms=lambda: 100,
        emit_event=None,
        fanout_chat_to_subscribed_nodes=lambda *_args, **_kwargs: _noop(),
    )
    assert history is not None and history[0] is True
    assert history[1]["count"] == 1

