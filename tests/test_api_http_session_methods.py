from datetime import datetime

from joyhousebot.api.http.session_methods import (
    delete_session_response,
    get_session_history_response,
    list_sessions_response,
)


class _SessionStore:
    def list_sessions(self):
        return [{"key": "s1"}]

    def get_or_create(self, _key: str):
        return type(
            "Session",
            (),
            {
                "messages": [{"content": "hello"}, {"role": "assistant", "content": "world", "timestamp": 1}],
                "updated_at": datetime(2024, 1, 1, 0, 0, 0),
            },
        )()

    def delete(self, _key: str):
        return True


class _Agent:
    sessions = _SessionStore()


def test_list_sessions_response():
    payload = list_sessions_response(agent=_Agent())
    assert payload["ok"] is True
    assert payload["sessions"][0]["key"] == "s1"


def test_get_session_history_response():
    payload = get_session_history_response(agent=_Agent(), session_key="abc")
    assert payload["ok"] is True
    assert payload["key"] == "abc"
    assert payload["messages"][0]["role"] == "user"
    assert payload["updated_at"] == "2024-01-01T00:00:00"


def test_delete_session_response():
    payload = delete_session_response(agent=_Agent(), session_key="abc")
    assert payload == {"ok": True, "removed": True}

