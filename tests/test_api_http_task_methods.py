import pytest
from fastapi import HTTPException

from joyhousebot.api.http.task_methods import (
    get_task_response,
    list_task_events_response,
    list_tasks_response,
)


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


def test_list_tasks_response():
    payload = list_tasks_response(store=_Store(), status=None, limit=10)
    assert payload["ok"] is True
    assert payload["data"][0]["task_id"] == "t1"


def test_get_task_response_found_and_missing():
    payload = get_task_response(store=_Store(), task_id="t1")
    assert payload["ok"] is True
    with pytest.raises(HTTPException) as exc:
        get_task_response(store=_Store(), task_id="missing")
    assert exc.value.status_code == 404


def test_list_task_events_response():
    payload = list_task_events_response(store=_Store(), task_id="t1", limit=5)
    assert payload["ok"] is True
    assert payload["data"][0]["event"] == "start"
