from datetime import datetime

import pytest

from joyhousebot.api.rpc.sessions_usage import try_handle_sessions_usage_method
from joyhousebot.config.schema import Config


def _rpc_error(code: str, message: str, data=None):
    return {"code": code, "message": message, "data": data}


class _FakeSession:
    def __init__(self):
        self.messages = [
            {"role": "user", "content": "hi", "timestamp": datetime.now().isoformat()},
            {"role": "assistant", "content": "hello", "timestamp": datetime.now().isoformat()},
        ]

    def clear(self):
        self.messages = []


class _FakeSessions:
    def __init__(self):
        self._session = _FakeSession()

    def list_sessions(self):
        return [{"key": "s1"}]

    def get_or_create(self, _key: str):
        return self._session

    def save(self, _session):
        return None

    def _get_session_path(self, key: str):
        return f"/tmp/{key}.json"


class _FakeAgent:
    def __init__(self):
        self.sessions = _FakeSessions()

    async def _consolidate_memory(self, _session, archive_all: bool = False):
        return archive_all


def _resolve_agent(_agent_id):
    return _FakeAgent()


def _build_sessions_list_payload(_agent, _config):
    return {"sessions": [{"key": "s1"}]}


def _build_chat_history_payload(session, limit: int):
    return {"messages": session.messages[:limit]}


def _apply_session_patch(_session, _params):
    return {"changed": True}


async def _delete_session(_key: str, agent_id=None):
    return {"removed": True, "agent_id": agent_id}


def _empty_usage_totals():
    return {
        "input": 0,
        "output": 0,
        "cacheRead": 0,
        "cacheWrite": 0,
        "totalTokens": 0,
        "totalCost": 0,
        "inputCost": 0,
        "outputCost": 0,
        "cacheReadCost": 0,
        "cacheWriteCost": 0,
        "missingCostEntries": 0,
    }


def _session_usage_entry(key: str, messages: list[dict]):
    return {
        "key": key,
        "usage": {
            "input": 1,
            "output": 2,
            "cacheRead": 0,
            "cacheWrite": 0,
            "totalTokens": 3,
            "totalCost": 0,
            "inputCost": 0,
            "outputCost": 0,
            "cacheReadCost": 0,
            "cacheWriteCost": 0,
            "missingCostEntries": 0,
            "messageCounts": {"total": len(messages), "user": 1, "assistant": 1, "toolCalls": 0, "toolResults": 0, "errors": 0},
        },
    }


def _estimate_tokens(text: str):
    return max(1, len(text))


@pytest.mark.asyncio
async def test_sessions_list_and_patch():
    cfg = Config()
    list_res = await try_handle_sessions_usage_method(
        method="sessions.list",
        params={},
        rpc_error=_rpc_error,
        resolve_agent=_resolve_agent,
        build_sessions_list_payload=_build_sessions_list_payload,
        build_chat_history_payload=_build_chat_history_payload,
        apply_session_patch=_apply_session_patch,
        now_ms=lambda: 100,
        delete_session=_delete_session,
        config=cfg,
        empty_usage_totals=_empty_usage_totals,
        session_usage_entry=_session_usage_entry,
        estimate_tokens=_estimate_tokens,
    )
    assert list_res == (True, {"sessions": [{"key": "s1"}]}, None)

    patch_res = await try_handle_sessions_usage_method(
        method="sessions.patch",
        params={"key": "s1"},
        rpc_error=_rpc_error,
        resolve_agent=_resolve_agent,
        build_sessions_list_payload=_build_sessions_list_payload,
        build_chat_history_payload=_build_chat_history_payload,
        apply_session_patch=_apply_session_patch,
        now_ms=lambda: 200,
        delete_session=_delete_session,
        config=cfg,
        empty_usage_totals=_empty_usage_totals,
        session_usage_entry=_session_usage_entry,
        estimate_tokens=_estimate_tokens,
    )
    assert patch_res is not None and patch_res[0] is True
    assert patch_res[1]["key"] == "s1"


@pytest.mark.asyncio
async def test_usage_and_timeseries_methods():
    cfg = Config()
    usage_res = await try_handle_sessions_usage_method(
        method="sessions.usage",
        params={},
        rpc_error=_rpc_error,
        resolve_agent=_resolve_agent,
        build_sessions_list_payload=_build_sessions_list_payload,
        build_chat_history_payload=_build_chat_history_payload,
        apply_session_patch=_apply_session_patch,
        now_ms=lambda: 300,
        delete_session=_delete_session,
        config=cfg,
        empty_usage_totals=_empty_usage_totals,
        session_usage_entry=_session_usage_entry,
        estimate_tokens=_estimate_tokens,
    )
    assert usage_res is not None and usage_res[0] is True
    assert usage_res[1]["totals"]["totalTokens"] == 3

    ts_res = await try_handle_sessions_usage_method(
        method="sessions.usage.timeseries",
        params={"key": "s1"},
        rpc_error=_rpc_error,
        resolve_agent=_resolve_agent,
        build_sessions_list_payload=_build_sessions_list_payload,
        build_chat_history_payload=_build_chat_history_payload,
        apply_session_patch=_apply_session_patch,
        now_ms=lambda: 400,
        delete_session=_delete_session,
        config=cfg,
        empty_usage_totals=_empty_usage_totals,
        session_usage_entry=_session_usage_entry,
        estimate_tokens=_estimate_tokens,
    )
    assert ts_res is not None and ts_res[0] is True
    assert len(ts_res[1]["points"]) >= 1

