"""PostgreSQL transaction-level admission control for top-level runs."""

from __future__ import annotations

from typing import Any


def check_top_level_submission_quota(
    conn: Any,
    *,
    user_id: str,
    agent_id: str,
    session_id: str,
    idempotency_key: str | None,
    max_active_per_user: int | None,
    max_submissions_per_minute: int | None,
) -> Any:
    """Lock one user's admission lane, returning an idempotent prior run if any."""

    if (
        max_active_per_user is None
        and max_submissions_per_minute is None
        and idempotency_key is None
    ):
        return None
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 918273))",
        (user_id,),
    )
    if idempotency_key:
        existing = conn.execute(
            """SELECT *,FALSE AS created FROM runtime_runs
               WHERE user_id=%s AND agent_id=%s AND session_id=%s
                 AND idempotency_key=%s""",
            (user_id, agent_id, session_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            return existing
    if max_active_per_user is not None:
        row = conn.execute(
            """SELECT COUNT(*) AS count FROM runtime_runs
               WHERE user_id=%s AND parent_run_id IS NULL
                 AND status IN ('queued','planning','running')""",
            (user_id,),
        ).fetchone()
        if int(row["count"]) >= max(1, int(max_active_per_user)):
            raise ValueError(
                "user_id has reached the in-flight run limit "
                f"({max_active_per_user} queued/planning/running)"
            )
    if max_submissions_per_minute is not None:
        row = conn.execute(
            """SELECT COUNT(*) AS count FROM runtime_runs
               WHERE user_id=%s AND parent_run_id IS NULL
                 AND created_at >= clock_timestamp() - INTERVAL '1 minute'""",
            (user_id,),
        ).fetchone()
        if int(row["count"]) >= max(1, int(max_submissions_per_minute)):
            raise ValueError(
                "submission rate limit exceeded "
                f"({max_submissions_per_minute} runs per minute)"
            )
    return None
