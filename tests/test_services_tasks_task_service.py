import pytest

from joyhousebot.services.tasks.task_service import (
    get_task_http,
    list_task_events_http,
    list_tasks_http,
)
from joyhousebot.services.errors import ServiceError


class _Task:
    task_id = "t1"
    source = "src"
    task_type = "kind"
    task_version = "v1"
    payload = {"x": 1}
    status = "pending"
    retry_count = 0
    next_retry_at = None
    error = None
    created_at = 1
    updated_at = 2


class _Store:
    def list_tasks(self, **_kwargs):
        return [_Task()]

    def get_task(self, task_id: str):
        return _Task() if task_id == "t1" else None

    def list_task_events(self, **_kwargs):
        return [{"event": "start"}]


def test_list_tasks_http():
    payload = list_tasks_http(_Store(), status=None, limit=10)
    assert payload["ok"] is True
    assert len(payload["data"]) == 1
    assert payload["data"][0]["task_id"] == "t1"


def test_get_task_http():
    payload = get_task_http(_Store(), "t1")
    assert payload["ok"] is True
    assert payload["data"]["task_id"] == "t1"
    assert payload["data"]["payload"] == {"x": 1}

    with pytest.raises(ServiceError, match="not found"):
        get_task_http(_Store(), "missing")


def test_list_task_events_http():
    payload = list_task_events_http(_Store(), task_id="t1", limit=5)
    assert payload["ok"] is True
    assert payload["data"][0]["event"] == "start"
