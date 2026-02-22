from datetime import datetime

import pytest

from joyhousebot.services.errors import ServiceError
from joyhousebot.services.sessions.session_service import (
    compact_session,
    delete_session_http,
    get_session_history_http,
    list_sessions_http,
    patch_session,
    preview_session,
    reset_session,
    resolve_session,
)


class _Session:
    def __init__(self):
        self.messages = [{"role": "user", "content": "hi", "timestamp": 1}]
        self.updated_at = datetime(2024, 1, 1, 0, 0, 0)

    def clear(self):
        self.messages = []


class _Sessions:
    def __init__(self):
        self.session = _Session()

    def list_sessions(self):
        return [{"key": "s1"}]

    def get_or_create(self, _key: str):
        return self.session

    def save(self, _session):
        return None

    def delete(self, _key: str):
        return True

    def _get_session_path(self, key: str):
        return f"/tmp/{key}.json"


class _Agent:
    def __init__(self):
        self.sessions = _Sessions()

    async def _consolidate_memory(self, _session, archive_all: bool = False):
        return archive_all


def _build_chat_history_payload(session, limit: int):
    return {"messages": session.messages[:limit]}


def _apply_session_patch(_session, _params):
    return {"changed": True}


@pytest.mark.asyncio
async def test_patch_preview_reset_compact():
    agent = _Agent()
    preview = preview_session(params={"key": "s1", "limit": 10}, agent=agent, build_chat_history_payload=_build_chat_history_payload)
    assert preview["key"] == "s1"
    patched = patch_session(
        params={"key": "s1"},
        agent=agent,
        apply_session_patch=_apply_session_patch,
        now_ms=lambda: 1,
    )
    assert patched["changed"] is True
    reset = reset_session(params={"key": "s1"}, agent=agent)
    assert reset["ok"] is True
    compacted = await compact_session(params={"key": "s1", "archiveAll": True}, agent=agent)
    assert compacted["compacted"] is True


def test_http_session_helpers():
    agent = _Agent()
    assert list_sessions_http(agent=agent)["ok"] is True
    hist = get_session_history_http(agent=agent, session_key="s1")
    assert hist["messages"][0]["role"] == "user"
    assert delete_session_http(agent=agent, session_key="s1")["removed"] is True


def test_resolve_session_requires_key():
    with pytest.raises(ServiceError):
        resolve_session(params={})

