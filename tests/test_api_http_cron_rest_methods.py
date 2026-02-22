import pytest
from fastapi import HTTPException

from joyhousebot.api.http.cron_rest_methods import (
    add_cron_job_response,
    cron_job_to_dict,
    delete_cron_job_response,
    list_cron_jobs_response,
    patch_cron_job_response,
    run_cron_job_response,
    schedule_body_to_internal,
)


class _Body:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.name = "job"
        self.schedule = {"kind": "every"}
        self.message = "m"
        self.deliver = False
        self.channel = None
        self.to = None
        self.delete_after_run = False
        self.agent_id = None


class _CronService:
    def __init__(self, *, run_ok=True, has_job=True):
        self.run_ok = run_ok
        self.has_job = has_job

    def list_jobs(self, include_disabled=True):
        return [{"id": "j1", "include_disabled": include_disabled}]

    def add_job(self, **kwargs):
        return {"id": "new", **kwargs}

    def enable_job(self, _job_id, enabled=True):
        if not self.has_job:
            return None
        return {"id": "j1", "enabled": enabled}

    def remove_job(self, _job_id):
        return True

    async def run_job(self, _job_id, force=False):
        return self.run_ok or force


def _job_to_dict(job):
    return {"id": job["id"]}


def _schedule_to_internal(schedule):
    return {"internal": schedule}


def test_list_cron_jobs_response():
    payload = list_cron_jobs_response(
        cron_service=_CronService(),
        include_disabled=False,
        job_to_dict=_job_to_dict,
    )
    assert payload == {"ok": True, "jobs": [{"id": "j1"}]}


def test_add_cron_job_response():
    payload = add_cron_job_response(
        cron_service=_CronService(),
        body=_Body(),
        schedule_body_to_internal=_schedule_to_internal,
        job_to_dict=_job_to_dict,
    )
    assert payload["ok"] is True
    assert payload["job"]["id"] == "new"


def test_patch_cron_job_response_validation_and_not_found():
    with pytest.raises(HTTPException) as exc_enabled:
        patch_cron_job_response(
            cron_service=_CronService(),
            job_id="j1",
            body=_Body(enabled=None),
            job_to_dict=_job_to_dict,
        )
    assert exc_enabled.value.status_code == 400

    with pytest.raises(HTTPException) as exc_not_found:
        patch_cron_job_response(
            cron_service=_CronService(has_job=False),
            job_id="j1",
            body=_Body(enabled=True),
            job_to_dict=_job_to_dict,
        )
    assert exc_not_found.value.status_code == 404


def test_delete_cron_job_response():
    payload = delete_cron_job_response(cron_service=_CronService(), job_id="j1")
    assert payload == {"ok": True, "removed": True}


@pytest.mark.asyncio
async def test_run_cron_job_response_ok_and_not_found():
    payload = await run_cron_job_response(cron_service=_CronService(run_ok=True), job_id="j1", force=False)
    assert payload == {"ok": True, "message": "Job executed"}

    with pytest.raises(HTTPException) as exc:
        await run_cron_job_response(cron_service=_CronService(run_ok=False), job_id="j1", force=False)
    assert exc.value.status_code == 404


def test_schedule_body_to_internal_supports_every_seconds():
    body = type(
        "Body",
        (),
        {
            "kind": "every",
            "at_ms": None,
            "every_ms": None,
            "every_seconds": 3,
            "expr": None,
            "tz": None,
        },
    )()
    schedule = schedule_body_to_internal(body)
    assert schedule.kind == "every"
    assert schedule.every_ms == 3000


def test_cron_job_to_dict_serializes_nested_fields():
    schedule = type("Schedule", (), {"kind": "every", "at_ms": None, "every_ms": 1000, "expr": None, "tz": "UTC"})()
    payload = type("Payload", (), {"kind": "message", "message": "hi", "deliver": False, "channel": None, "to": None})()
    state = type(
        "State",
        (),
        {"next_run_at_ms": 1, "last_run_at_ms": 2, "last_status": "ok", "last_error": None},
    )()
    job = type(
        "Job",
        (),
        {
            "id": "j1",
            "name": "job",
            "enabled": True,
            "agent_id": None,
            "schedule": schedule,
            "payload": payload,
            "state": state,
            "created_at_ms": 1,
            "updated_at_ms": 2,
            "delete_after_run": False,
        },
    )()
    data = cron_job_to_dict(job)
    assert data["id"] == "j1"
    assert data["schedule"]["every_ms"] == 1000
    assert data["payload"]["message"] == "hi"
    assert data["state"]["last_status"] == "ok"

