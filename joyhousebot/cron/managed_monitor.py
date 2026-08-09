"""Desired-state reconciliation for Agent revision managed Monitors."""

from __future__ import annotations

import hashlib
from typing import Any

from joyhousebot.cron.active_hours import normalize_active_hours
from joyhousebot.cron.types import CronJob, CronJobState, CronPayload, CronPolicy, CronSchedule


def managed_monitor_schedule_id(user_id: str, agent_id: str) -> str:
    """Return a stable, non-reversible identity for one user/Agent pair."""
    digest = hashlib.sha256(f"{user_id}\0{agent_id}".encode()).hexdigest()[:24]
    return f"managed_monitor_{digest}"


def _policy_schedule(value: dict[str, Any]) -> CronSchedule:
    raw = dict(value.get("schedule") or {})
    if not raw:
        raw = {"kind": "every", "every_ms": value.get("every_ms", 30 * 60 * 1000)}
    kind = str(raw.get("kind") or "every")
    if kind == "every":
        return CronSchedule(kind="every", every_ms=int(raw.get("every_ms") or 0))
    if kind == "cron":
        return CronSchedule(
            kind="cron",
            expr=str(raw.get("expr") or raw.get("cron_expr") or "").strip(),
            tz=str(raw.get("tz") or raw.get("timezone") or "").strip() or None,
        )
    raise ValueError("managed Monitor schedule.kind must be every or cron")


def _delivery(
    value: dict[str, Any], existing: CronJob | None, channel: str, target: str
) -> tuple[bool, str | None, str | None]:
    if value.get("delivery") != "origin":
        return False, None, None
    if channel not in {"", "api", "cli", "schedule", "system"} and target:
        return True, channel, target
    if existing and existing.payload.deliver:
        return True, existing.payload.channel, existing.payload.to
    return False, None, None


def validate_managed_monitor_policy(value: Any) -> dict[str, Any]:
    """Validate the public, versioned desired-state policy."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("monitor_policy must be an object")
    policy = dict(value)
    if policy.get("context_mode", "light") not in {"full", "light"}:
        raise ValueError("monitor_policy.context_mode must be full or light")
    if policy.get("preflight_mode", "runtime_attention") not in {
        "always",
        "runtime_attention",
    }:
        raise ValueError(
            "monitor_policy.preflight_mode must be always or runtime_attention"
        )
    if policy.get("session_mode", "isolated") not in {"isolated", "main"}:
        raise ValueError("monitor_policy.session_mode must be isolated or main")
    if policy.get("delivery", "none") not in {"none", "origin"}:
        raise ValueError("monitor_policy.delivery must be none or origin")
    if policy.get("active_hours") is not None:
        normalize_active_hours(policy["active_hours"])
    if bool(policy.get("enabled")):
        from joyhousebot.cron.service import _validate_schedule_limits

        schedule = _policy_schedule(policy)
        _validate_schedule_limits(schedule)
        if not str(
            policy.get("message") or "Review Runtime attention and act if needed."
        ).strip():
            raise ValueError("monitor_policy.message is required")
    return policy


def reconcile_agent_monitor(
    repository: Any,
    *,
    user_id: str,
    profile: Any,
    channel: str = "",
    target: str = "",
) -> CronJob | None:
    """Create, update, or disable the managed Schedule for one user/Agent."""
    revision = profile.revision
    agent_id = profile.definition.agent_id
    policy = validate_managed_monitor_policy(revision.monitor_policy)
    schedule_id = managed_monitor_schedule_id(user_id, agent_id)
    existing = next(
        (
            item
            for item in repository.list(user_id=user_id, include_disabled=True)
            if item.id == schedule_id and item.payload.managed_by == "agent_revision"
        ),
        None,
    )
    if not bool(policy.get("enabled")):
        if existing and existing.enabled:
            now_ms = repository.db_now_ms()
            return repository.set_enabled(
                schedule_id,
                False,
                user_id=user_id,
                next_run_at_ms=None,
                now_ms=now_ms,
            )
        return existing

    from joyhousebot.cron.service import _compute_next_run, _validate_schedule_limits

    schedule = _policy_schedule(policy)
    _validate_schedule_limits(schedule)
    message = str(policy.get("message") or "Review Runtime attention and act if needed.").strip()
    if not message:
        raise ValueError("managed Monitor message is required")
    now_ms = repository.db_now_ms()
    next_run = _compute_next_run(schedule, now_ms)
    if next_run is None:
        raise ValueError("managed Monitor schedule does not produce a future occurrence")
    deliver, delivery_channel, delivery_target = _delivery(
        policy, existing, channel, target
    )
    session_mode = "main" if policy.get("session_mode") == "main" else "isolated"
    preflight = (
        "always" if policy.get("preflight_mode") == "always" else "runtime_attention"
    )
    job = CronJob(
        id=schedule_id,
        name=str(policy.get("name") or f"{profile.definition.name} Monitor")[:200],
        user_id=user_id,
        enabled=True,
        agent_id=agent_id,
        schedule=schedule,
        payload=CronPayload(
            kind="agent_monitor",
            message=message,
            deliver=deliver,
            channel=delivery_channel,
            to=delivery_target,
            session_mode=session_mode,
            session_id=(str(policy.get("session_id") or "main") if session_mode == "main" else None),
            quiet_token=str(policy.get("quiet_token") or "NO_ACTION").strip() or "NO_ACTION",
            defer_when_busy=policy.get("defer_when_busy") is not False,
            busy_backoff_ms=min(
                3_600_000, max(1_000, int(policy.get("busy_backoff_ms") or 60_000))
            ),
            preflight_mode=preflight,
            context_mode="full" if policy.get("context_mode") == "full" else "light",
            active_hours=normalize_active_hours(policy.get("active_hours")),
            managed_by="agent_revision",
            managed_revision_id=revision.revision_id,
        ),
        policy=CronPolicy(misfire_policy="skip", overlap_policy="skip"),
        state=CronJobState(next_run_at_ms=next_run),
        created_at_ms=existing.created_at_ms if existing else now_ms,
        updated_at_ms=now_ms,
    )
    if existing:
        return repository.update(job)
    try:
        return repository.create(job)
    except Exception:
        # Deterministic IDs make concurrent first-use reconciliation safe. If
        # another submit won the insert, update that row to the same policy.
        concurrent = next(
            (
                item
                for item in repository.list(user_id=user_id, include_disabled=True)
                if item.id == schedule_id and item.payload.managed_by == "agent_revision"
            ),
            None,
        )
        if concurrent is None:
            raise
        job.created_at_ms = concurrent.created_at_ms
        return repository.update(job)


def reconcile_existing_agent_monitors(repository: Any, profile: Any) -> None:
    """Apply a newly published revision to previously materialized users."""
    agent_id = profile.definition.agent_id
    jobs = repository.list(user_id=None, include_disabled=True)
    for job in jobs:
        if job.agent_id != agent_id or job.payload.managed_by != "agent_revision":
            continue
        reconcile_agent_monitor(repository, user_id=job.user_id, profile=profile)
