import pytest

from joyhousebot.api.rpc.cron_methods import (
    build_cron_add_args,
    build_cron_add_body_from_params,
    build_cron_patch_body_from_params,
    try_handle_cron_method,
)


def _rpc_error(code: str, message: str, data=None):
    return {"code": code, "message": message, "data": data}


@pytest.mark.asyncio
async def test_cron_add_and_run_update_state():
    app_state = {"rpc_cron_runs": []}
    saved = {}
    events = []

    async def _list_jobs(**kwargs):
        return {"ok": True, "jobs": [{"id": "j1"}], "kwargs": kwargs}

    async def _add_job(body):
        return {"ok": True, "job": {"id": "j1"}, "body": body}

    async def _patch_job(_job_id, _body):
        return {"ok": True}

    async def _delete_job(_job_id):
        return {"ok": True}

    async def _run_job(job_id, force=False):
        return {"ok": True, "jobId": job_id, "force": force}

    async def _emit(topic, payload):
        events.append((topic, payload))

    result = await try_handle_cron_method(
        method="cron.add",
        params={"name": "n1", "schedule": {"kind": "every", "everyMs": 1000}},
        app_state=app_state,
        rpc_error=_rpc_error,
        now_ms=lambda: 123,
        load_persistent_state=lambda k, d: saved.get(k, d),
        save_persistent_state=lambda k, v: saved.__setitem__(k, v),
        cron_list_jobs=_list_jobs,
        cron_add_job=_add_job,
        cron_patch_job=_patch_job,
        cron_delete_job=_delete_job,
        cron_run_job=_run_job,
        build_cron_add_body=lambda p: {"name": p.get("name")},
        build_cron_patch_body=lambda _p: {},
        emit_event=_emit,
    )
    assert result is not None and result[0] is True
    assert events[-1][1]["action"] == "add"

    run_res = await try_handle_cron_method(
        method="cron.run",
        params={"id": "j1", "force": True},
        app_state=app_state,
        rpc_error=_rpc_error,
        now_ms=lambda: 200,
        load_persistent_state=lambda k, d: saved.get(k, d),
        save_persistent_state=lambda k, v: saved.__setitem__(k, v),
        cron_list_jobs=_list_jobs,
        cron_add_job=_add_job,
        cron_patch_job=_patch_job,
        cron_delete_job=_delete_job,
        cron_run_job=_run_job,
        build_cron_add_body=lambda p: {"name": p.get("name")},
        build_cron_patch_body=lambda _p: {},
        emit_event=_emit,
    )
    assert run_res is not None and run_res[0] is True
    assert app_state["rpc_cron_runs"][0]["jobId"] == "j1"
    assert "rpc.cron_runs" in saved


@pytest.mark.asyncio
async def test_cron_status_and_runs_filter():
    app_state = {"cron_service": type("Svc", (), {"status": lambda self: {"enabled": True, "jobs": 2, "next_wake_at_ms": 999}})()}
    saved = {"rpc.cron_runs": [{"jobId": "a"}, {"jobId": "b"}]}

    async def _noop(*_args, **_kwargs):
        return {"ok": True}

    status_res = await try_handle_cron_method(
        method="cron.status",
        params={},
        app_state=app_state,
        rpc_error=_rpc_error,
        now_ms=lambda: 1,
        load_persistent_state=lambda k, d: saved.get(k, d),
        save_persistent_state=lambda _k, _v: None,
        cron_list_jobs=_noop,
        cron_add_job=_noop,
        cron_patch_job=_noop,
        cron_delete_job=_noop,
        cron_run_job=_noop,
        build_cron_add_body=lambda _p: {},
        build_cron_patch_body=lambda _p: {},
        emit_event=None,
    )
    assert status_res is not None and status_res[1]["enabled"] is True

    runs_res = await try_handle_cron_method(
        method="cron.runs",
        params={"id": "b", "limit": 10},
        app_state=app_state,
        rpc_error=_rpc_error,
        now_ms=lambda: 1,
        load_persistent_state=lambda k, d: saved.get(k, d),
        save_persistent_state=lambda _k, _v: None,
        cron_list_jobs=_noop,
        cron_add_job=_noop,
        cron_patch_job=_noop,
        cron_delete_job=_noop,
        cron_run_job=_noop,
        build_cron_add_body=lambda _p: {},
        build_cron_patch_body=lambda _p: {},
        emit_event=None,
    )
    assert runs_res is not None and runs_res[1]["entries"] == [{"jobId": "b"}]


def test_build_cron_add_args_normalizes_input():
    args = build_cron_add_args({"name": "job", "schedule": {"kind": "once", "at_ms": 123}, "agentId": "a1"})
    assert args["name"] == "job"
    assert args["schedule"]["kind"] == "once"
    assert args["schedule"]["at_ms"] == 123
    assert args["agent_id"] == "a1"


def test_build_cron_body_builders_from_params():
    class _Schedule:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _Create:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _Patch:
        def __init__(self, enabled=None):
            self.enabled = enabled

    create_body = build_cron_add_body_from_params(
        {"name": "job", "schedule": {"kind": "every", "everyMs": 1000}, "agentId": "ag1"},
        cron_job_create_cls=_Create,
        cron_schedule_body_cls=_Schedule,
    )
    patch_body = build_cron_patch_body_from_params(
        {"patch": {"enabled": True}},
        cron_job_patch_cls=_Patch,
    )
    assert create_body.kwargs["name"] == "job"
    assert create_body.kwargs["schedule"].kwargs["kind"] == "every"
    assert create_body.kwargs["agent_id"] == "ag1"
    assert patch_body.enabled is True

