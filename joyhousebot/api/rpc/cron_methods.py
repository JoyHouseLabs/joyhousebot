"""RPC handlers for cron.* methods."""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


async def try_handle_cron_method(
    *,
    method: str,
    params: dict[str, Any],
    app_state: dict[str, Any],
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
    now_ms: Callable[[], int],
    load_persistent_state: Callable[[str, Any], Any],
    save_persistent_state: Callable[[str, Any], None],
    cron_list_jobs: Callable[..., Awaitable[dict[str, Any]]],
    cron_add_job: Callable[[Any], Awaitable[dict[str, Any]]],
    cron_patch_job: Callable[[str, Any], Awaitable[dict[str, Any]]],
    cron_delete_job: Callable[[str], Awaitable[dict[str, Any]]],
    cron_run_job: Callable[..., Awaitable[dict[str, Any]]],
    build_cron_add_body: Callable[[dict[str, Any]], Any],
    build_cron_patch_body: Callable[[dict[str, Any]], Any],
    emit_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
) -> RpcResult | None:
    """Handle cron RPC methods and return None for unrelated methods."""
    if method == "cron.list":
        payload = await cron_list_jobs(include_disabled=bool(params.get("includeDisabled", params.get("include_disabled", True))))
        jobs = payload.get("jobs", [])
        return True, {"jobs": jobs}, None

    if method == "cron.status":
        cron_service = app_state.get("cron_service")
        if not cron_service:
            return True, {"enabled": False, "jobs": 0, "nextWakeAtMs": None}, None
        st = cron_service.status()
        return True, {"enabled": st.get("enabled", False), "jobs": st.get("jobs", 0), "nextWakeAtMs": st.get("next_wake_at_ms")}, None

    if method == "cron.add":
        body = build_cron_add_body(params)
        payload = await cron_add_job(body)
        if emit_event:
            await emit_event("cron", {"action": "add", "jobId": payload.get("job", {}).get("id")})
        return True, payload, None

    if method == "cron.update":
        job_id = str(params.get("id") or params.get("job_id") or "")
        if not job_id:
            return False, None, rpc_error("INVALID_REQUEST", "cron.update requires id", None)
        body = build_cron_patch_body(params)
        payload = await cron_patch_job(job_id, body)
        if emit_event:
            await emit_event("cron", {"action": "update", "jobId": job_id})
        return True, payload, None

    if method == "cron.remove":
        job_id = str(params.get("id") or params.get("job_id") or "")
        if not job_id:
            return False, None, rpc_error("INVALID_REQUEST", "cron.remove requires id", None)
        payload = await cron_delete_job(job_id)
        if emit_event:
            await emit_event("cron", {"action": "remove", "jobId": job_id})
        return True, payload, None

    if method == "cron.run":
        job_id = str(params.get("id") or params.get("job_id") or "")
        if not job_id:
            return False, None, rpc_error("INVALID_REQUEST", "cron.run requires id", None)
        payload = await cron_run_job(job_id, force=bool(params.get("force", False)))
        runs = app_state.get("rpc_cron_runs") or []
        runs.insert(0, {"ts": now_ms(), "jobId": job_id, "status": "ok"})
        app_state["rpc_cron_runs"] = runs[:200]
        save_persistent_state("rpc.cron_runs", runs[:200])
        save_persistent_state("rpc.last_heartbeat", {"ts": now_ms()})
        if emit_event:
            await emit_event("cron", {"action": "run", "jobId": job_id})
        return True, payload, None

    if method == "cron.runs":
        job_id = str(params.get("id") or "")
        entries = load_persistent_state("rpc.cron_runs", [])
        if job_id:
            entries = [e for e in entries if e.get("jobId") == job_id]
        return True, {"entries": entries[: int(params.get("limit") or 50)]}, None

    return None


def build_cron_add_args(params: dict[str, Any]) -> dict[str, Any]:
    """Build normalized arguments for CronJobCreate and CronScheduleBody."""
    schedule = params.get("schedule") or {}
    return {
        "name": str(params.get("name") or ""),
        "schedule": {
            "kind": str(schedule.get("kind") or ""),
            "at_ms": schedule.get("at_ms") or (int(time.time() * 1000) if schedule.get("at") else None),
            "every_ms": schedule.get("every_ms") or schedule.get("everyMs"),
            "every_seconds": schedule.get("every_seconds"),
            "expr": schedule.get("expr"),
            "tz": schedule.get("tz"),
        },
        "message": str((params.get("payload") or {}).get("message") or params.get("message") or ""),
        "deliver": bool(params.get("deliver", False)),
        "channel": params.get("channel"),
        "to": params.get("to"),
        "delete_after_run": bool(params.get("delete_after_run", False)),
        "agent_id": params.get("agent_id") or params.get("agentId"),
    }


def build_cron_add_body_from_params(
    params: dict[str, Any],
    *,
    cron_job_create_cls: type,
    cron_schedule_body_cls: type,
) -> Any:
    """Build CronJobCreate instance from RPC params."""
    add_args = build_cron_add_args(params)
    return cron_job_create_cls(
        name=add_args["name"],
        schedule=cron_schedule_body_cls(**add_args["schedule"]),
        message=add_args["message"],
        deliver=add_args["deliver"],
        channel=add_args["channel"],
        to=add_args["to"],
        delete_after_run=add_args["delete_after_run"],
        agent_id=add_args["agent_id"],
    )


def build_cron_patch_body_from_params(
    params: dict[str, Any],
    *,
    cron_job_patch_cls: type,
) -> Any:
    """Build CronJobPatch instance from RPC params."""
    patch = params.get("patch") if isinstance(params.get("patch"), dict) else {}
    return cron_job_patch_cls(enabled=patch.get("enabled", params.get("enabled")))

