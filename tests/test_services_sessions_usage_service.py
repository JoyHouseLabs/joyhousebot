from datetime import datetime

from joyhousebot.services.sessions.usage_service import (
    build_usage_logs,
    build_usage_payload,
    build_usage_timeseries,
)


class _Session:
    def __init__(self):
        self.messages = [
            {"role": "user", "content": "hi", "timestamp": datetime.now().isoformat()},
            {"role": "assistant", "content": "hello", "timestamp": datetime.now().isoformat()},
        ]


class _Sessions:
    def __init__(self):
        self._session = _Session()

    def list_sessions(self):
        return [{"key": "s1"}]

    def get_or_create(self, _key: str):
        return self._session


class _Agent:
    sessions = _Sessions()


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
            "messageCounts": {"total": len(messages), "user": 1, "assistant": 1, "toolCalls": 0, "toolResults": 0, "errors": 0},
        },
    }


def _estimate_tokens(text: str):
    return max(1, len(text))


def test_build_usage_payload_timeseries_and_logs():
    payload = build_usage_payload(
        params={},
        now_ms=lambda: 1,
        agent=_Agent(),
        empty_usage_totals=_empty_usage_totals,
        session_usage_entry=_session_usage_entry,
    )
    assert payload["totals"]["totalTokens"] == 3

    ts = build_usage_timeseries(
        key="s1",
        agent=_Agent(),
        now_ms=lambda: 2,
        estimate_tokens=_estimate_tokens,
    )
    assert len(ts["points"]) >= 1

    logs = build_usage_logs(
        key="s1",
        limit=10,
        agent=_Agent(),
        estimate_tokens=_estimate_tokens,
    )
    assert len(logs["logs"]) >= 1

