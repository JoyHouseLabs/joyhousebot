"""Run-scoped serialization for distributed runtime Task claims."""

from __future__ import annotations

from typing import Any


def lock_claimable_task_run(conn: Any, run_id: str | None) -> str | None:
    """Lock one claimable Run before its Task concurrency limits are evaluated.

    PostgreSQL takes a statement snapshot before waiting on ``FOR UPDATE``.
    Selecting and updating a Task in one statement can therefore let two
    workers both observe the same pre-claim sibling count.  A separate
    ``SKIP LOCKED`` Run selection makes the following claim statement use a
    fresh snapshot while the Run row remains locked by this transaction.
    """
    lock_clause = (
        "FOR UPDATE OF r"
        if run_id is not None
        else "FOR UPDATE OF r SKIP LOCKED"
    )
    query = (
        """SELECT r.run_id
           FROM runtime_runs r
           WHERE (%s::text IS NULL OR r.run_id=%s)
             AND EXISTS (
               SELECT 1 FROM runtime_tasks task
               WHERE task.run_id=r.run_id
                 AND (
                   (task.status='queued'
                    AND task.available_at<=clock_timestamp()
                    AND r.status IN ('queued','running'))
                   OR (
                     task.status='waiting_external'
                     AND r.status IN ('running','waiting_external')
                     AND EXISTS (
                       SELECT 1 FROM action_intents action
                       JOIN operation_reconciliations rec
                         ON rec.action_id=action.action_id
                       WHERE action.task_id=task.task_id
                         AND ((rec.status='pending'
                               AND rec.next_attempt_at<=clock_timestamp())
                              OR (rec.status='checking'
                                  AND rec.lease_expires_at<clock_timestamp()))
                     )
                   )
                   OR (
                     task.status='waiting_external'
                     AND task.node_type='subrun'
                     AND r.status IN ('running','waiting_external')
                     AND EXISTS (
                       SELECT 1 FROM runtime_runs child
                       WHERE child.parent_task_id=task.task_id
                         AND child.parent_run_id=r.run_id
                         AND child.status IN ('completed','failed','cancelled','timed_out')
                     )
                   )
                 )
                 AND (
                   task.attempt<task.max_attempts
                   OR task.status='waiting_external'
                   OR COALESCE(task.wait_reason,'') IN
                      ('waiting_approval','durable_recovery','foreach_expanded',
                       'bounded_loop_waiting','subrun_waiting')
                 )
             )
             AND (
               r.parent_run_id IS NOT NULL OR NOT EXISTS (
                 SELECT 1 FROM runtime_runs earlier
                 WHERE earlier.user_id=r.user_id
                   AND earlier.session_id=r.session_id
                   AND earlier.agent_id=r.agent_id
                   AND earlier.run_id<>r.run_id
                   AND earlier.parent_run_id IS NULL
                   AND (
                     earlier.status='running'
                     OR (
                       earlier.status IN ('queued','planning')
                       AND (earlier.created_at,earlier.run_id)<(r.created_at,r.run_id)
                     )
                   )
               )
             )
             AND (
               r.initial_events_required = FALSE
               OR EXISTS (
                 SELECT 1 FROM runtime_events ready
                 WHERE ready.run_id=r.run_id AND ready.event_type='run.queued'
               )
             )
             AND (SELECT count(*) FROM runtime_tasks active
                  WHERE active.run_id=r.run_id AND active.status='running')
                 < r.max_concurrent
           ORDER BY r.created_at,r.run_id
           """
        + lock_clause
        + " LIMIT 1"
    )
    row = conn.execute(
        query,
        (run_id, run_id),
    ).fetchone()
    return str(row["run_id"]) if row is not None else None
