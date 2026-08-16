"""Pure durable Schedule definitions shared across Runtime layers."""

import json
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class CronSchedule:
    """Schedule definition for a cron job."""

    kind: Literal["at", "every", "cron"]
    # For "at": timestamp in ms
    at_ms: int | None = None
    # For "every": interval in ms
    every_ms: int | None = None
    # For "cron": cron expression (e.g. "0 9 * * *")
    expr: str | None = None
    # Timezone for cron expressions
    tz: str | None = None


@dataclass
class CronPayload:
    """What to do when the job runs."""

    kind: Literal["system_event", "agent_turn", "agent_monitor"] = "agent_turn"
    message: str = ""
    # Deliver response to channel
    deliver: bool = False
    channel: str | None = None
    to: str | None = None  # e.g. phone number
    # Agent monitors may run in a private per-monitor session or deliberately
    # re-enter one named user session for context-aware checks.
    session_mode: Literal["isolated", "main"] = "isolated"
    session_id: str | None = None
    # An exact final response matching this token is persisted on the Run but
    # suppressed from external Channel delivery.
    quiet_token: str = "NO_ACTION"
    defer_when_busy: bool = True
    busy_backoff_ms: int = 60_000
    # ``runtime_attention`` is a deterministic, PostgreSQL-only guard. It
    # avoids a model Run until the user's pending approvals, recent Runtime
    # failures, or dead Channel deliveries change.
    preflight_mode: Literal["always", "runtime_attention"] = "always"
    # Light mode admits only immutable Agent/system policy, current request,
    # session routing, and tools; memory, conversation history, and Skill
    # prompt material remain available only in full mode.
    context_mode: Literal["full", "light"] = "full"
    # Local wall-clock window. Equal start/end means all day; an end earlier
    # than start crosses midnight.
    active_hours: dict[str, str] | None = None
    # Internal desired-state ownership. Public Schedule writes never set these
    # fields; they make managed jobs distinguishable without a second table.
    managed_by: Literal["user", "agent_revision"] = "user"
    managed_revision_id: str | None = None


@dataclass
class CronPolicy:
    """Delivery-independent execution policy for one schedule occurrence."""

    # Retrying submission is safe because every attempt reuses the same
    # occurrence/attempt idempotency key.
    max_submit_attempts: int = 3
    # Retrying a terminal Run is opt-in: an Agent may have performed external
    # side effects before it failed, so silently replaying it is unsafe.
    max_run_retries: int = 0
    retry_backoff_ms: int = 60_000
    misfire_policy: Literal["fire_once", "skip"] = "fire_once"
    misfire_grace_ms: int = 5 * 60 * 1000
    overlap_policy: Literal["serialize", "skip"] = "serialize"


@dataclass
class CronJobState:
    """Runtime state of a job."""

    next_run_at_ms: int | None = None
    last_run_at_ms: int | None = None
    last_status: str | None = None
    last_error: str | None = None
    occurrence_id: str | None = None
    scheduled_for_ms: int | None = None
    attempt: int = 1
    submit_attempt: int = 0
    claim_scope: Literal["schedule", "occurrence"] = "schedule"
    monitor_scratch_revision: int | None = None
    monitor_observation_hash: str | None = None
    monitor_observation: dict[str, Any] = field(default_factory=dict)


@dataclass
class CronJob:
    """A user-owned scheduled Agent run."""

    id: str
    name: str
    user_id: str = "system"
    enabled: bool = True
    # Which shared platform Agent runs this job; None selects the default.
    agent_id: str | None = None
    schedule: CronSchedule = field(default_factory=lambda: CronSchedule(kind="every"))
    payload: CronPayload = field(default_factory=CronPayload)
    policy: CronPolicy = field(default_factory=CronPolicy)
    state: CronJobState = field(default_factory=CronJobState)
    created_at_ms: int = 0
    updated_at_ms: int = 0
    delete_after_run: bool = False
    lease_owner: str | None = None
    lease_until_ms: int | None = None
    lease_version: int = 0


def schedule_run_session_id(job: CronJob) -> str:
    """Resolve the durable session used by one scheduled Run."""
    if job.payload.kind != "agent_monitor":
        return f"schedule:{job.id}"
    if job.payload.session_mode == "main":
        return job.payload.session_id or "main"
    return f"monitor:{job.id}"


def schedule_run_prompt(
    job: CronJob,
    *,
    scratch: str = "",
    scratch_revision: int = 0,
    observation: dict[str, Any] | None = None,
) -> str:
    """Build the monitor contract without creating a second execution path."""
    if job.payload.kind != "agent_monitor":
        return job.payload.message
    quiet_token = job.payload.quiet_token or "NO_ACTION"
    monitor_context = (
        f"\n\nPrivate monitor scratch (revision {scratch_revision}):\n"
        f"{scratch or '[empty]'}"
    )
    if observation:
        monitor_context += (
            "\n\nDeterministic Runtime attention snapshot:\n"
            + json.dumps(observation, ensure_ascii=False, sort_keys=True)
        )
    return (
        "You are running a scheduled Agent Monitor.\n"
        "Evaluate the monitor instructions using the current authorized context and tools. "
        "Do not emit a progress-only notification. Use monitor_scratch to read or update "
        "private durable monitor notes; updates require the revision shown below.\n"
        f"If there is no user-visible action, change, warning, or result, respond exactly "
        f"with: {quiet_token}\n\n"
        f"Monitor instructions:\n{job.payload.message}"
        f"{monitor_context}"
    )
