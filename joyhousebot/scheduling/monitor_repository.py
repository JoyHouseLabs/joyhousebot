"""Private, versioned state and deterministic preflight for Agent Monitors."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from typing import Any, Iterator

from joyhousebot.storage.json_codec import Jsonb

_DB_NOW_MS = "(EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::bigint"
_SCRATCH_LIMIT_BYTES = 16 * 1024


class ScratchRevisionConflictError(ValueError):
    """The caller tried to replace a scratch revision that is no longer current."""


def _dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return dict(json.loads(value) or {})
    return dict(value or {})


class MonitorRepository:
    """Own monitor-only state without creating a second execution state machine."""

    def __init__(self, store: Any) -> None:
        self.store = store
        if getattr(store, "backend_name", None) != "postgres":
            raise TypeError("MonitorRepository requires PostgreSQL runtime store")

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        with self.store._pool.connection() as connection:
            with connection.transaction():
                yield connection

    @staticmethod
    def _monitor_schedule(connection: Any, schedule_id: str, user_id: str) -> Any:
        return connection.execute(
            """SELECT schedule_id,user_id FROM schedules
               WHERE schedule_id=%s AND user_id=%s
                 AND payload->>'kind'='agent_monitor'""",
            (schedule_id, user_id),
        ).fetchone()

    @staticmethod
    def _ensure_state(connection: Any, schedule_id: str, user_id: str) -> Any:
        connection.execute(
            f"""INSERT INTO schedule_monitor_state
                   (schedule_id,user_id,updated_at_ms)
               VALUES (%s,%s,{_DB_NOW_MS})
               ON CONFLICT(schedule_id) DO NOTHING""",
            (schedule_id, user_id),
        )
        return connection.execute(
            """SELECT * FROM schedule_monitor_state
               WHERE schedule_id=%s AND user_id=%s FOR UPDATE""",
            (schedule_id, user_id),
        ).fetchone()

    @staticmethod
    def _state(row: Any) -> dict[str, Any]:
        return {
            "schedule_id": str(row["schedule_id"]),
            "user_id": str(row["user_id"]),
            "revision": int(row["scratch_revision"] or 0),
            "content": str(row["scratch_content"] or ""),
            "observation_hash": row["observation_hash"],
            "observation": _dict(row["observation"]),
            "observed_at_ms": row["observed_at_ms"],
            "updated_at_ms": int(row["updated_at_ms"]),
        }

    def get_state(self, schedule_id: str, *, user_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            if self._monitor_schedule(connection, schedule_id, user_id) is None:
                return None
            row = self._ensure_state(connection, schedule_id, user_id)
        return self._state(row)

    def update_scratch(
        self,
        schedule_id: str,
        *,
        user_id: str,
        content: str,
        expected_revision: int,
        actor_type: str,
        actor_id: str,
        run_id: str | None = None,
        action_id: str | None = None,
    ) -> dict[str, Any] | None:
        if len(content.encode("utf-8")) > _SCRATCH_LIMIT_BYTES:
            raise ValueError("monitor scratch must not exceed 16384 UTF-8 bytes")
        if expected_revision < 0:
            raise ValueError("expected_revision must be non-negative")
        with self._connection() as connection:
            if self._monitor_schedule(connection, schedule_id, user_id) is None:
                return None
            if action_id:
                duplicate = connection.execute(
                    """SELECT revision,content,created_at_ms
                       FROM schedule_monitor_scratch_revisions
                       WHERE schedule_id=%s AND user_id=%s AND action_id=%s""",
                    (schedule_id, user_id, action_id),
                ).fetchone()
                if duplicate is not None:
                    if str(duplicate["content"]) != content:
                        raise ScratchRevisionConflictError("scratch action identity conflict")
                    row = self._ensure_state(connection, schedule_id, user_id)
                    value = self._state(row)
                    value.update(
                        revision=int(duplicate["revision"]),
                        content=str(duplicate["content"]),
                        updated_at_ms=int(duplicate["created_at_ms"]),
                    )
                    return value
            current = self._ensure_state(connection, schedule_id, user_id)
            revision = int(current["scratch_revision"] or 0)
            if revision != expected_revision:
                raise ScratchRevisionConflictError(
                    f"scratch revision changed: expected {expected_revision}, current {revision}"
                )
            next_revision = revision + 1
            connection.execute(
                f"""INSERT INTO schedule_monitor_scratch_revisions
                       (schedule_id,revision,user_id,content,actor_type,actor_id,
                        run_id,action_id,created_at_ms)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,{_DB_NOW_MS})""",
                (
                    schedule_id,
                    next_revision,
                    user_id,
                    content,
                    actor_type,
                    actor_id,
                    run_id,
                    action_id,
                ),
            )
            row = connection.execute(
                f"""UPDATE schedule_monitor_state
                   SET scratch_revision=%s,scratch_content=%s,updated_at_ms={_DB_NOW_MS}
                   WHERE schedule_id=%s AND user_id=%s RETURNING *""",
                (next_revision, content, schedule_id, user_id),
            ).fetchone()
        return self._state(row)

    def list_scratch_revisions(
        self, schedule_id: str, *, user_id: str, limit: int = 50
    ) -> list[dict[str, Any]] | None:
        with self._connection() as connection:
            if self._monitor_schedule(connection, schedule_id, user_id) is None:
                return None
            rows = connection.execute(
                """SELECT * FROM schedule_monitor_scratch_revisions
                   WHERE schedule_id=%s AND user_id=%s
                   ORDER BY revision DESC LIMIT %s""",
                (schedule_id, user_id, max(1, min(limit, 200))),
            ).fetchall()
        return [
            {
                "schedule_id": str(row["schedule_id"]),
                "revision": int(row["revision"]),
                "content": str(row["content"]),
                "actor_type": str(row["actor_type"]),
                "actor_id": str(row["actor_id"]),
                "run_id": row["run_id"],
                "created_at_ms": int(row["created_at_ms"]),
            }
            for row in rows
        ]

    def evaluate_runtime_attention(
        self,
        *,
        schedule_id: str,
        occurrence_id: str,
        user_id: str,
        worker_id: str,
        lease_version: int,
    ) -> dict[str, Any] | None:
        """Freeze one user-scoped Runtime snapshot behind the occurrence lease."""
        with self._connection() as connection:
            occurrence = connection.execute(
                """SELECT occurrence_id,monitor_observation_hash,monitor_observation,
                          monitor_preflight_status FROM schedule_occurrences
                   WHERE occurrence_id=%s AND schedule_id=%s AND user_id=%s
                     AND lease_owner=%s AND lease_version=%s FOR UPDATE""",
                (occurrence_id, schedule_id, user_id, worker_id, lease_version),
            ).fetchone()
            if occurrence is None:
                return None
            if self._monitor_schedule(connection, schedule_id, user_id) is None:
                return None
            frozen_decision = occurrence["monitor_preflight_status"]
            if frozen_decision in {"run", "skip"}:
                observation = _dict(occurrence["monitor_observation"])
                has_attention = any(
                    int(observation.get(key, {}).get("total") or 0) > 0
                    for key in (
                        "pending_approvals",
                        "recent_run_failures",
                        "dead_deliveries",
                    )
                )
                return {
                    "should_run": frozen_decision == "run",
                    "changed": frozen_decision == "run",
                    "has_attention": has_attention,
                    "hash": str(occurrence["monitor_observation_hash"] or ""),
                    "observation": observation,
                    "reason": "reused frozen runtime attention preflight",
                }
            state = self._ensure_state(connection, schedule_id, user_id)
            observation = self._runtime_attention_snapshot(connection, user_id, schedule_id)
            encoded = json.dumps(
                observation, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            observation_hash = hashlib.sha256(encoded).hexdigest()
            previous_hash = state["observation_hash"]
            changed = previous_hash != observation_hash
            has_attention = any(
                int(observation[key]["total"]) > 0
                for key in ("pending_approvals", "recent_run_failures", "dead_deliveries")
            )
            should_run = changed and (previous_hash is not None or has_attention)
            connection.execute(
                f"""UPDATE schedule_monitor_state SET observation_hash=%s,
                       observation=%s,observed_at_ms={_DB_NOW_MS},updated_at_ms={_DB_NOW_MS}
                   WHERE schedule_id=%s AND user_id=%s""",
                (observation_hash, Jsonb(observation), schedule_id, user_id),
            )
            connection.execute(
                """UPDATE schedule_occurrences SET monitor_observation_hash=%s,
                       monitor_observation=%s,monitor_preflight_status=%s
                   WHERE occurrence_id=%s""",
                (
                    observation_hash,
                    Jsonb(observation),
                    "run" if should_run else "skip",
                    occurrence_id,
                ),
            )
        return {
            "should_run": should_run,
            "changed": changed,
            "has_attention": has_attention,
            "hash": observation_hash,
            "observation": observation,
            "reason": (
                "runtime attention snapshot changed"
                if should_run
                else (
                    "no runtime attention signals"
                    if previous_hash is None and not has_attention
                    else "runtime attention snapshot is unchanged"
                )
            ),
        }

    @staticmethod
    def _runtime_attention_snapshot(
        connection: Any, user_id: str, schedule_id: str
    ) -> dict[str, Any]:
        approvals = connection.execute(
            """SELECT approval_id,status,subject_type,
                       (EXTRACT(EPOCH FROM updated_at)*1000)::bigint AS changed_at_ms
                FROM approval_requests WHERE user_id=%s AND status IN ('pending','approved')
                ORDER BY updated_at DESC,approval_id LIMIT 50""",
            (user_id,),
        ).fetchall()
        approval_total = connection.execute(
            """SELECT count(*) AS total FROM approval_requests
               WHERE user_id=%s AND status IN ('pending','approved')""",
            (user_id,),
        ).fetchone()
        failures = connection.execute(
            """SELECT run_id,agent_id,status,
                      (EXTRACT(EPOCH FROM updated_at)*1000)::bigint AS changed_at_ms
               FROM runtime_runs WHERE user_id=%s AND parent_run_id IS NULL
                 AND status IN ('failed','timed_out')
                 AND updated_at>=clock_timestamp()-interval '7 days'
                 AND COALESCE(options->'metadata'->>'schedule_payload_kind','')
                     <> 'agent_monitor'
               ORDER BY updated_at DESC,run_id LIMIT 50""",
            (user_id,),
        ).fetchall()
        failure_total = connection.execute(
            """SELECT count(*) AS total FROM runtime_runs
               WHERE user_id=%s AND parent_run_id IS NULL
                 AND status IN ('failed','timed_out')
                 AND updated_at>=clock_timestamp()-interval '7 days'
                 AND COALESCE(options->'metadata'->>'schedule_payload_kind','')
                     <> 'agent_monitor'""",
            (user_id,),
        ).fetchone()
        dead: list[Any] = []
        dead_total = 0
        exists = connection.execute(
            "SELECT to_regclass('channel_outbox') AS table_name"
        ).fetchone()
        if exists and exists["table_name"]:
            dead = connection.execute(
                f"""SELECT outbound_id,channel,status,updated_at_ms AS changed_at_ms
                    FROM channel_outbox WHERE user_id=%s AND status='dead'
                      AND COALESCE(metadata->>'schedule_id','')<>%s
                      AND updated_at_ms>={_DB_NOW_MS}-604800000
                    ORDER BY updated_at_ms DESC,outbound_id LIMIT 50""",
                (user_id, schedule_id),
            ).fetchall()
            total = connection.execute(
                f"""SELECT count(*) AS total FROM channel_outbox
                    WHERE user_id=%s AND status='dead'
                      AND COALESCE(metadata->>'schedule_id','')<>%s
                      AND updated_at_ms>={_DB_NOW_MS}-604800000""",
                (user_id, schedule_id),
            ).fetchone()
            dead_total = int(total["total"] or 0)

        def items(rows: list[Any], keys: tuple[str, ...]) -> list[dict[str, Any]]:
            return [{key: row[key] for key in keys} for row in rows]

        return {
            "pending_approvals": {
                "total": int(approval_total["total"] or 0),
                "items": items(
                    approvals, ("approval_id", "status", "subject_type", "changed_at_ms")
                ),
            },
            "recent_run_failures": {
                "total": int(failure_total["total"] or 0),
                "items": items(
                    failures, ("run_id", "agent_id", "status", "changed_at_ms")
                ),
            },
            "dead_deliveries": {
                "total": dead_total,
                "items": items(dead, ("outbound_id", "channel", "status", "changed_at_ms")),
            },
        }

    def freeze_scratch(
        self,
        *,
        schedule_id: str,
        occurrence_id: str,
        user_id: str,
        worker_id: str,
        lease_version: int,
    ) -> dict[str, Any] | None:
        """Pin a scratch revision to an occurrence and return its exact content."""
        with self._connection() as connection:
            occurrence = connection.execute(
                """SELECT monitor_scratch_revision,monitor_observation_hash,
                          monitor_observation FROM schedule_occurrences
                   WHERE occurrence_id=%s AND schedule_id=%s AND user_id=%s
                     AND lease_owner=%s AND lease_version=%s FOR UPDATE""",
                (occurrence_id, schedule_id, user_id, worker_id, lease_version),
            ).fetchone()
            if occurrence is None:
                return None
            if self._monitor_schedule(connection, schedule_id, user_id) is None:
                return None
            state = self._ensure_state(connection, schedule_id, user_id)
            frozen = occurrence["monitor_scratch_revision"]
            revision = int(state["scratch_revision"] or 0) if frozen is None else int(frozen)
            if frozen is None:
                connection.execute(
                    """UPDATE schedule_occurrences SET monitor_scratch_revision=%s
                       WHERE occurrence_id=%s""",
                    (revision, occurrence_id),
                )
            if revision == 0:
                content = ""
            else:
                row = connection.execute(
                    """SELECT content FROM schedule_monitor_scratch_revisions
                       WHERE schedule_id=%s AND user_id=%s AND revision=%s""",
                    (schedule_id, user_id, revision),
                ).fetchone()
                if row is None:
                    raise RuntimeError("frozen monitor scratch revision is missing")
                content = str(row["content"])
        return {
            "scratch": content,
            "scratch_revision": revision,
            "observation_hash": occurrence["monitor_observation_hash"],
            "observation": _dict(occurrence["monitor_observation"]),
        }

    def occurrence_context(
        self, schedule_id: str, occurrence_id: str, *, user_id: str
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT monitor_scratch_revision,monitor_observation_hash,
                          monitor_observation FROM schedule_occurrences
                   WHERE occurrence_id=%s AND schedule_id=%s AND user_id=%s""",
                (occurrence_id, schedule_id, user_id),
            ).fetchone()
            if row is None:
                return None
            revision = int(row["monitor_scratch_revision"] or 0)
            content = ""
            if revision:
                scratch = connection.execute(
                    """SELECT content FROM schedule_monitor_scratch_revisions
                       WHERE schedule_id=%s AND user_id=%s AND revision=%s""",
                    (schedule_id, user_id, revision),
                ).fetchone()
                if scratch is None:
                    raise RuntimeError("frozen monitor scratch revision is missing")
                content = str(scratch["content"])
        return {
            "scratch": content,
            "scratch_revision": revision,
            "observation_hash": row["monitor_observation_hash"],
            "observation": _dict(row["monitor_observation"]),
        }
