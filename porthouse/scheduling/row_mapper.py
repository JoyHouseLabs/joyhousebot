"""Pure PostgreSQL row to Schedule domain mapping."""

import json
from typing import Any

from porthouse.domain.schedules import (
    CronJob,
    CronJobState,
    CronPayload,
    CronPolicy,
    CronSchedule,
)


def _decoded(value: Any) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def schedule_job_from_row(row: Any) -> CronJob:
    schedule = row["schedule"] if "schedule" in row else row["schedule_json"]
    payload = row["payload"] if "payload" in row else row["payload_json"]
    return CronJob(
        id=str(row["schedule_id"]),
        name=str(row["name"]),
        user_id=str(row["user_id"]),
        enabled=bool(row["enabled"]),
        agent_id=row["agent_id"],
        installation_id=row.get("installation_id"),
        schedule=CronSchedule(**_decoded(schedule)),
        payload=CronPayload(**_decoded(payload)),
        policy=CronPolicy(**_decoded(row.get("policy"))),
        state=CronJobState(
            next_run_at_ms=row["next_run_at_ms"],
            last_run_at_ms=row["last_run_at_ms"],
            last_status=row["last_status"],
            last_error=row["last_error"],
            occurrence_id=row.get("occurrence_id"),
            scheduled_for_ms=row.get("scheduled_for_ms"),
            attempt=int(row.get("attempt") or 1),
            submit_attempt=int(row.get("submit_attempt") or 0),
            claim_scope=str(row.get("claim_scope") or "schedule"),
        ),
        created_at_ms=int(row["created_at_ms"]),
        updated_at_ms=int(row["updated_at_ms"]),
        delete_after_run=bool(row["delete_after_run"]),
        lease_owner=row["lease_owner"],
        lease_until_ms=row["lease_until_ms"],
        lease_version=int(row["lease_version"]),
    )


def occurrence_job_from_row(row: Any) -> CronJob:
    return CronJob(
        id=str(row["schedule_id"]),
        name=str(row.get("name") or row["schedule_id"]),
        user_id=str(row["user_id"]),
        enabled=True,
        agent_id=row["agent_id"],
        schedule=CronSchedule(**_decoded(row["schedule"])),
        payload=CronPayload(**_decoded(row["payload"])),
        policy=CronPolicy(**_decoded(row["policy"])),
        state=CronJobState(
            occurrence_id=str(row["occurrence_id"]),
            scheduled_for_ms=int(row["scheduled_for_ms"]),
            attempt=int(row["attempt"] or 1),
            submit_attempt=int(row["submit_attempt"] or 0),
            claim_scope="occurrence",
            monitor_scratch_revision=row["monitor_scratch_revision"],
            monitor_observation_hash=row["monitor_observation_hash"],
            monitor_observation=_decoded(row["monitor_observation"]),
        ),
        created_at_ms=int(row["started_at_ms"]),
        updated_at_ms=int(row["started_at_ms"]),
        delete_after_run=bool(row["delete_after_run"]),
        lease_owner=row["lease_owner"],
        lease_until_ms=row["lease_until_ms"],
        lease_version=int(row["lease_version"]),
    )


__all__ = ["occurrence_job_from_row", "schedule_job_from_row"]
