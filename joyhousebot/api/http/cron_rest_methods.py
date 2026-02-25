"""Helpers for cron REST endpoint payloads."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException


def schedule_body_to_internal(body: Any) -> Any:
    """Convert API schedule body to internal CronSchedule."""
    from joyhousebot.cron.types import CronSchedule

    every_ms = body.every_ms
    if every_ms is None and body.every_seconds is not None:
        every_ms = body.every_seconds * 1000
    return CronSchedule(
        kind=body.kind,
        at_ms=body.at_ms,
        every_ms=every_ms,
        expr=body.expr,
        tz=body.tz,
    )


def cron_job_to_dict(job: Any) -> dict[str, Any]:
    """Serialize a CronJob to JSON-safe dict."""
    return {
        "id": job.id,
        "name": job.name,
        "enabled": job.enabled,
        "agent_id": job.agent_id,
        "schedule": {
            "kind": job.schedule.kind,
            "at_ms": job.schedule.at_ms,
            "every_ms": job.schedule.every_ms,
            "expr": job.schedule.expr,
            "tz": job.schedule.tz,
        },
        "payload": {
            "kind": job.payload.kind,
            "message": job.payload.message,
            "deliver": job.payload.deliver,
            "channel": job.payload.channel,
            "to": job.payload.to,
        },
        "state": {
            "next_run_at_ms": job.state.next_run_at_ms,
            "last_run_at_ms": job.state.last_run_at_ms,
            "last_status": job.state.last_status,
            "last_error": job.state.last_error,
        },
        "created_at_ms": job.created_at_ms,
        "updated_at_ms": job.updated_at_ms,
        "delete_after_run": job.delete_after_run,
    }


def list_cron_jobs_response(
    *,
    cron_service: Any,
    include_disabled: bool,
    job_to_dict: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    """Build response for listing cron jobs."""
    jobs = cron_service.list_jobs(include_disabled=include_disabled)
    return {"ok": True, "jobs": [job_to_dict(j) for j in jobs]}


def add_cron_job_response(
    *,
    cron_service: Any,
    body: Any,
    schedule_body_to_internal: Callable[[Any], Any],
    job_to_dict: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    """Build response for creating a cron job."""
    schedule = schedule_body_to_internal(body.schedule)
    payload_kind = getattr(body, "payload_kind", None) or "agent_turn"
    job = cron_service.add_job(
        name=body.name,
        schedule=schedule,
        message=getattr(body, "message", "") or "",
        deliver=body.deliver,
        channel=body.channel,
        to=body.to,
        delete_after_run=body.delete_after_run,
        agent_id=body.agent_id,
        payload_kind=payload_kind,
    )
    return {"ok": True, "job": job_to_dict(job)}


def patch_cron_job_response(
    *,
    cron_service: Any,
    job_id: str,
    body: Any,
    job_to_dict: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    """Build response for enabling/disabling a cron job."""
    if body.enabled is None:
        raise HTTPException(status_code=400, detail="enabled is required")
    job = cron_service.enable_job(job_id, enabled=body.enabled)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True, "job": job_to_dict(job)}


def delete_cron_job_response(*, cron_service: Any, job_id: str) -> dict[str, Any]:
    """Build response for removing a cron job."""
    removed = cron_service.remove_job(job_id)
    return {"ok": True, "removed": removed}


async def run_cron_job_response(*, cron_service: Any, job_id: str, force: bool) -> dict[str, Any]:
    """Build response for running a cron job."""
    ok = await cron_service.run_job(job_id, force=force)
    if not ok:
        raise HTTPException(status_code=404, detail="Job not found or disabled (use force=true)")
    return {"ok": True, "message": "Job executed"}

