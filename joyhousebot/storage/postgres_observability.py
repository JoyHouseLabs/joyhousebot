"""PostgreSQL explainability, model trace, and replay persistence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from joyhousebot.storage.json_codec import Jsonb
from joyhousebot.storage.observability_records import (
    ExecutionSpanRecord,
    ModelInvocationRecord,
    ReasoningSegmentRecord,
    ReplayRunRecord,
    TraceBlobRecord,
)
from joyhousebot.storage.platform_records import RunFeedbackRecord


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else (str(value) if value is not None else None)


def _payload(value: Any) -> tuple[bytes, str, int]:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return raw, hashlib.sha256(raw).hexdigest(), len(raw)


class PostgresObservabilityStoreMixin:
    def migrate_observability(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS trace_blobs (
            blob_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            invocation_id TEXT,
            kind TEXT NOT NULL,
            content_type TEXT NOT NULL,
            content JSONB,
            storage_uri TEXT,
            sha256 TEXT NOT NULL,
            size_bytes BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            expires_at TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS ix_trace_blobs_run_created ON trace_blobs(run_id,created_at);
        CREATE INDEX IF NOT EXISTS ix_trace_blobs_invocation ON trace_blobs(invocation_id,created_at);

        CREATE TABLE IF NOT EXISTS execution_spans (
            span_id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            parent_span_id TEXT,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            task_id TEXT,
            turn_id TEXT,
            span_kind TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            worker_id TEXT,
            attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
            error JSONB,
            started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            first_token_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            duration_ms BIGINT,
            ttft_ms BIGINT
        );
        CREATE INDEX IF NOT EXISTS ix_execution_spans_run_started ON execution_spans(run_id,started_at);
        CREATE INDEX IF NOT EXISTS ix_execution_spans_trace_started ON execution_spans(trace_id,started_at);

        CREATE TABLE IF NOT EXISTS model_invocations (
            invocation_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            task_id TEXT,
            turn_id TEXT,
            span_id TEXT NOT NULL REFERENCES execution_spans(span_id) ON DELETE CASCADE,
            attempt INTEGER NOT NULL DEFAULT 1,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            operation TEXT NOT NULL,
            provider_request_id TEXT,
            agent_revision_id TEXT,
            request_blob_id TEXT,
            response_blob_id TEXT,
            request_hash TEXT,
            response_hash TEXT,
            status TEXT NOT NULL,
            finish_reason TEXT,
            reasoning_availability TEXT NOT NULL DEFAULT 'unavailable',
            usage JSONB NOT NULL DEFAULT '{}'::jsonb,
            cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
            cache_status TEXT NOT NULL DEFAULT 'miss',
            error JSONB,
            started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            first_token_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            duration_ms BIGINT,
            ttft_ms BIGINT
        );
        CREATE INDEX IF NOT EXISTS ix_model_invocations_run_started ON model_invocations(run_id,started_at);
        CREATE INDEX IF NOT EXISTS ix_model_invocations_turn ON model_invocations(run_id,turn_id,attempt);

        CREATE TABLE IF NOT EXISTS model_reasoning_segments (
            segment_id TEXT PRIMARY KEY,
            invocation_id TEXT NOT NULL REFERENCES model_invocations(invocation_id) ON DELETE CASCADE,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            source TEXT NOT NULL,
            kind TEXT NOT NULL,
            content TEXT NOT NULL,
            content_format TEXT NOT NULL DEFAULT 'text',
            fidelity TEXT NOT NULL,
            provider_block_type TEXT,
            token_count INTEGER,
            content_hash TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(invocation_id,sequence)
        );
        CREATE INDEX IF NOT EXISTS ix_reasoning_run_sequence
            ON model_reasoning_segments(run_id,invocation_id,sequence);

        CREATE TABLE IF NOT EXISTS replay_runs (
            replay_id TEXT PRIMARY KEY,
            source_run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            source_turn_id TEXT,
            new_run_id TEXT,
            mode TEXT NOT NULL,
            overrides JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_by TEXT NOT NULL,
            status TEXT NOT NULL,
            comparison JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            finished_at TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS ix_replay_runs_source_created
            ON replay_runs(source_run_id,created_at DESC);

        CREATE TABLE IF NOT EXISTS model_response_cache (
            cache_key TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            response JSONB NOT NULL,
            source_invocation_id TEXT,
            hit_count BIGINT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            last_hit_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS ix_model_cache_expiry ON model_response_cache(expires_at);

        CREATE TABLE IF NOT EXISTS run_feedback (
            feedback_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            agent_revision_id TEXT,
            turn_id TEXT,
            message_id TEXT,
            feedback_type TEXT NOT NULL,
            rating TEXT,
            comment TEXT NOT NULL,
            output_excerpt TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            reviewed_by TEXT,
            reviewed_at TIMESTAMPTZ,
            CHECK (feedback_type IN ('incorrect','missing_data','needs_optimization','helpful','other')),
            CHECK (rating IS NULL OR rating IN ('positive','negative','neutral')),
            CHECK (status IN ('open','reviewed','resolved','dismissed'))
        );
        CREATE INDEX IF NOT EXISTS ix_run_feedback_run_created ON run_feedback(run_id,created_at DESC);
        CREATE INDEX IF NOT EXISTS ix_run_feedback_user_created ON run_feedback(user_id,created_at DESC);
        CREATE INDEX IF NOT EXISTS ix_run_feedback_status_created ON run_feedback(status,created_at DESC);
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341920,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="observability",
                version=1,
                ddl=ddl,
                description="trace blobs, spans, model invocations, and replays",
            )

    def put_trace_blob(self, *, run_id: str, kind: str, content: Any, **kwargs: Any) -> TraceBlobRecord:
        blob_id = str(kwargs.get("blob_id") or f"blob_{uuid4().hex}")
        _, digest, size = _payload(content)
        with self._pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO trace_blobs
                       (blob_id,run_id,invocation_id,kind,content_type,content,storage_uri,
                        sha256,size_bytes,created_at,expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           COALESCE(%s::timestamptz,clock_timestamp()),%s::timestamptz)
                   RETURNING *""",
                (blob_id, run_id, kwargs.get("invocation_id"), kind,
                 str(kwargs.get("content_type") or "application/json"), Jsonb(content),
                 kwargs.get("storage_uri"), digest, size, kwargs.get("created_at"), kwargs.get("expires_at")),
            ).fetchone()
        return self._obs_blob(row)

    def get_trace_blob(self, blob_id: str) -> TraceBlobRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT * FROM trace_blobs WHERE blob_id=%s
                   AND (expires_at IS NULL OR expires_at > clock_timestamp())""",
                (blob_id,),
            ).fetchone()
        return self._obs_blob(row) if row else None

    def list_trace_blobs(self, run_id: str) -> list[TraceBlobRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM trace_blobs WHERE run_id=%s
                   AND (expires_at IS NULL OR expires_at > clock_timestamp())
                   ORDER BY created_at""",
                (run_id,),
            ).fetchall()
        return [self._obs_blob(row) for row in rows]

    def start_execution_span(self, **kwargs: Any) -> ExecutionSpanRecord:
        span_id = str(kwargs.get("span_id") or f"span_{uuid4().hex}")
        with self._pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO execution_spans
                       (span_id,trace_id,parent_span_id,run_id,task_id,turn_id,span_kind,name,
                        status,worker_id,attributes,started_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           COALESCE(%s::timestamptz,clock_timestamp())) RETURNING *""",
                (span_id, kwargs["trace_id"], kwargs.get("parent_span_id"), kwargs["run_id"],
                 kwargs.get("task_id"), kwargs.get("turn_id"), kwargs["span_kind"], kwargs["name"],
                 kwargs.get("status") or "running", kwargs.get("worker_id"),
                 Jsonb(kwargs.get("attributes") or {}), kwargs.get("started_at")),
            ).fetchone()
        return self._obs_span(row)

    def mark_execution_span_first_token(self, span_id: str) -> bool:
        with self._pool.connection() as conn:
            cursor = conn.execute(
                """UPDATE execution_spans SET first_token_at=COALESCE(first_token_at,clock_timestamp()),
                       ttft_ms=COALESCE(ttft_ms,EXTRACT(EPOCH FROM (clock_timestamp()-started_at))*1000)::bigint
                   WHERE span_id=%s""", (span_id,)
            )
            return cursor.rowcount == 1

    def finish_execution_span(self, span_id: str, **kwargs: Any) -> bool:
        with self._pool.connection() as conn:
            cursor = conn.execute(
                """UPDATE execution_spans SET status=%s,error=%s,
                       finished_at=COALESCE(%s::timestamptz,clock_timestamp()),
                       duration_ms=COALESCE(%s,(EXTRACT(EPOCH FROM
                           (COALESCE(%s::timestamptz,clock_timestamp())-started_at))*1000)::bigint),
                       attributes=execution_spans.attributes || %s WHERE span_id=%s""",
                (kwargs.get("status") or "completed", Jsonb(kwargs["error"]) if kwargs.get("error") else None,
                 kwargs.get("finished_at"), kwargs.get("duration_ms"), kwargs.get("finished_at"),
                 Jsonb(kwargs.get("attributes") or {}), span_id),
            )
            return cursor.rowcount == 1

    def get_execution_span(self, span_id: str) -> ExecutionSpanRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT * FROM execution_spans WHERE span_id=%s", (span_id,)).fetchone()
        return self._obs_span(row) if row else None

    def list_execution_spans(self, run_id: str) -> list[ExecutionSpanRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM execution_spans WHERE run_id=%s ORDER BY started_at,span_id", (run_id,)
            ).fetchall()
        return [self._obs_span(row) for row in rows]

    def create_model_invocation(self, **kwargs: Any) -> ModelInvocationRecord:
        invocation_id = str(kwargs.get("invocation_id") or f"model_{uuid4().hex}")
        with self._pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO model_invocations
                       (invocation_id,run_id,task_id,turn_id,span_id,attempt,provider,model,
                        operation,agent_revision_id,request_blob_id,request_hash,status,
                        reasoning_availability,started_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           COALESCE(%s::timestamptz,clock_timestamp())) RETURNING *""",
                (invocation_id, kwargs["run_id"], kwargs.get("task_id"), kwargs.get("turn_id"),
                 kwargs["span_id"], int(kwargs.get("attempt") or 1), kwargs.get("provider") or "unknown",
                 kwargs["model"], kwargs["operation"], kwargs.get("agent_revision_id"),
                 kwargs.get("request_blob_id"), kwargs.get("request_hash"),
                 kwargs.get("status") or "running", kwargs.get("reasoning_availability") or "unavailable",
                 kwargs.get("started_at")),
            ).fetchone()
        return self._obs_invocation(row)

    def mark_model_invocation_first_token(self, invocation_id: str) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE model_invocations SET first_token_at=COALESCE(first_token_at,clock_timestamp()),
                       ttft_ms=COALESCE(ttft_ms,EXTRACT(EPOCH FROM (clock_timestamp()-started_at))*1000)::bigint
                   WHERE invocation_id=%s RETURNING span_id""", (invocation_id,)
            ).fetchone()
            if not row:
                return False
            conn.execute(
                """UPDATE execution_spans SET first_token_at=COALESCE(first_token_at,clock_timestamp()),
                       ttft_ms=COALESCE(ttft_ms,EXTRACT(EPOCH FROM (clock_timestamp()-started_at))*1000)::bigint
                   WHERE span_id=%s""", (row["span_id"],)
            )
        return True

    def finish_model_invocation(self, invocation_id: str, **kwargs: Any) -> bool:
        status = kwargs.get("status") or "completed"
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE model_invocations SET provider_request_id=%s,response_blob_id=%s,
                       response_hash=%s,status=%s,finish_reason=%s,reasoning_availability=%s,
                       usage=%s,cost_usd=%s,cache_status=%s,error=%s,
                       finished_at=COALESCE(%s::timestamptz,clock_timestamp()),
                       duration_ms=COALESCE(%s,(EXTRACT(EPOCH FROM
                           (COALESCE(%s::timestamptz,clock_timestamp())-started_at))*1000)::bigint)
                   WHERE invocation_id=%s RETURNING span_id,finished_at,duration_ms""",
                (kwargs.get("provider_request_id"), kwargs.get("response_blob_id"), kwargs.get("response_hash"),
                 status, kwargs.get("finish_reason"), kwargs.get("reasoning_availability") or "unavailable",
                 Jsonb(kwargs.get("usage") or {}), float(kwargs.get("cost_usd") or 0),
                 kwargs.get("cache_status") or "miss", Jsonb(kwargs["error"]) if kwargs.get("error") else None,
                 kwargs.get("finished_at"), kwargs.get("duration_ms"), kwargs.get("finished_at"), invocation_id),
            ).fetchone()
            if not row:
                return False
            conn.execute(
                """UPDATE execution_spans SET status=%s,error=%s,finished_at=%s,duration_ms=%s,
                       attributes=execution_spans.attributes || %s WHERE span_id=%s""",
                (status, Jsonb(kwargs["error"]) if kwargs.get("error") else None, row["finished_at"],
                 row["duration_ms"], Jsonb({"finish_reason": kwargs.get("finish_reason")}), row["span_id"]),
            )
        return True

    def get_model_invocation(self, invocation_id: str) -> ModelInvocationRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT * FROM model_invocations WHERE invocation_id=%s", (invocation_id,)).fetchone()
        return self._obs_invocation(row) if row else None

    def list_model_invocations(self, run_id: str) -> list[ModelInvocationRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM model_invocations WHERE run_id=%s ORDER BY started_at,attempt", (run_id,)
            ).fetchall()
        return [self._obs_invocation(row) for row in rows]

    def append_reasoning_segment(self, **kwargs: Any) -> ReasoningSegmentRecord:
        content = str(kwargs.get("content") or "")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (str(kwargs["invocation_id"]),),
            )
            sequence = kwargs.get("sequence")
            if sequence is None:
                row = conn.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 AS value FROM model_reasoning_segments WHERE invocation_id=%s",
                    (kwargs["invocation_id"],),
                ).fetchone()
                sequence = int(row["value"])
            segment_id = str(kwargs.get("segment_id") or f"reason_{uuid4().hex}")
            row = conn.execute(
                """INSERT INTO model_reasoning_segments
                       (segment_id,invocation_id,run_id,sequence,source,kind,content,content_format,
                        fidelity,provider_block_type,token_count,content_hash,created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           COALESCE(%s::timestamptz,clock_timestamp())) RETURNING *""",
                (segment_id, kwargs["invocation_id"], kwargs["run_id"], int(sequence), kwargs["source"],
                 kwargs.get("kind") or "analysis", content, kwargs.get("content_format") or "text",
                 kwargs.get("fidelity") or "exact", kwargs.get("provider_block_type"),
                 kwargs.get("token_count"), digest, kwargs.get("created_at")),
            ).fetchone()
        return self._obs_reasoning(row)

    def list_reasoning_segments(self, run_id: str, *, invocation_id: str | None = None) -> list[ReasoningSegmentRecord]:
        query = "SELECT * FROM model_reasoning_segments WHERE run_id=%s"
        params: list[Any] = [run_id]
        if invocation_id:
            query += " AND invocation_id=%s"
            params.append(invocation_id)
        query += " ORDER BY created_at,sequence"
        with self._pool.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._obs_reasoning(row) for row in rows]

    def create_replay_run(self, **kwargs: Any) -> ReplayRunRecord:
        replay_id = str(kwargs.get("replay_id") or f"replay_{uuid4().hex}")
        with self._pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO replay_runs
                       (replay_id,source_run_id,source_turn_id,new_run_id,mode,overrides,created_by,
                        status,comparison,created_at,finished_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           COALESCE(%s::timestamptz,clock_timestamp()),%s::timestamptz) RETURNING *""",
                (replay_id, kwargs["source_run_id"], kwargs.get("source_turn_id"), kwargs.get("new_run_id"),
                 kwargs.get("mode") or "live", Jsonb(kwargs.get("overrides") or {}), kwargs["created_by"],
                 kwargs.get("status") or "queued", Jsonb(kwargs["comparison"]) if kwargs.get("comparison") else None,
                 kwargs.get("created_at"), kwargs.get("finished_at")),
            ).fetchone()
        return self._obs_replay(row)

    def update_replay_run(self, replay_id: str, **kwargs: Any) -> bool:
        status = str(kwargs.get("status") or "completed")
        finished_at = kwargs.get("finished_at")
        if finished_at is None and status in {"completed", "failed", "cancelled"}:
            finished_at = datetime.now().astimezone().isoformat()
        with self._pool.connection() as conn:
            cursor = conn.execute(
                """UPDATE replay_runs SET new_run_id=COALESCE(%s,new_run_id),status=%s,
                       comparison=%s,finished_at=%s::timestamptz
                   WHERE replay_id=%s""",
                (kwargs.get("new_run_id"), status,
                 Jsonb(kwargs["comparison"]) if kwargs.get("comparison") is not None else None,
                 finished_at, replay_id),
            )
            return cursor.rowcount == 1

    def get_replay_run(self, replay_id: str) -> ReplayRunRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT * FROM replay_runs WHERE replay_id=%s", (replay_id,)).fetchone()
        return self._obs_replay(row) if row else None

    def list_replay_runs(self, source_run_id: str) -> list[ReplayRunRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM replay_runs WHERE source_run_id=%s ORDER BY created_at DESC", (source_run_id,)
            ).fetchall()
        return [self._obs_replay(row) for row in rows]

    def create_run_feedback(self, **kwargs: Any) -> RunFeedbackRecord:
        comment = str(kwargs.get("comment") or "").strip()
        if not comment:
            raise ValueError("feedback comment is required")
        if len(comment) > 10000:
            raise ValueError("feedback comment is too long")
        feedback_id = str(kwargs.get("feedback_id") or f"feedback_{uuid4().hex}")
        with self._pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO run_feedback
                       (feedback_id,run_id,user_id,agent_id,session_id,agent_revision_id,
                        turn_id,message_id,feedback_type,rating,comment,output_excerpt,
                        status,metadata,created_at,updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           COALESCE(%s::timestamptz,clock_timestamp()),
                           COALESCE(%s::timestamptz,clock_timestamp()))
                   RETURNING *""",
                (
                    feedback_id,
                    kwargs["run_id"],
                    kwargs["user_id"],
                    kwargs["agent_id"],
                    kwargs["session_id"],
                    kwargs.get("agent_revision_id"),
                    kwargs.get("turn_id"),
                    kwargs.get("message_id"),
                    kwargs.get("feedback_type") or "other",
                    kwargs.get("rating"),
                    comment,
                    (str(kwargs.get("output_excerpt"))[:4000] if kwargs.get("output_excerpt") else None),
                    kwargs.get("status") or "open",
                    Jsonb(kwargs.get("metadata") or {}),
                    kwargs.get("created_at"),
                    kwargs.get("updated_at"),
                ),
            ).fetchone()
        return self._obs_feedback(row)

    def list_run_feedback(
        self, run_id: str, *, user_id: str | None = None, limit: int = 200
    ) -> list[RunFeedbackRecord]:
        query = "SELECT * FROM run_feedback WHERE run_id=%s"
        params: list[Any] = [run_id]
        if user_id is not None:
            query += " AND user_id=%s"
            params.append(user_id)
        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(max(1, min(int(limit), 5000)))
        with self._pool.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._obs_feedback(row) for row in rows]

    def get_model_response_cache(self, cache_key: str) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """UPDATE model_response_cache SET hit_count=hit_count+1,last_hit_at=clock_timestamp()
                   WHERE cache_key=%s AND (expires_at IS NULL OR expires_at>clock_timestamp())
                   RETURNING *""",
                (cache_key,),
            ).fetchone()
        if not row:
            return None
        return {
            "cache_key": str(row["cache_key"]),
            "provider": str(row["provider"]),
            "model": str(row["model"]),
            "response": dict(row["response"] or {}),
            "source_invocation_id": row["source_invocation_id"],
            "hit_count": int(row["hit_count"] or 0),
        }

    def put_model_response_cache(self, cache_key: str, **kwargs: Any) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """INSERT INTO model_response_cache
                       (cache_key,provider,model,response,source_invocation_id,created_at,expires_at)
                   VALUES (%s,%s,%s,%s,%s,COALESCE(%s::timestamptz,clock_timestamp()),%s::timestamptz)
                   ON CONFLICT(cache_key) DO UPDATE SET response=EXCLUDED.response,
                       source_invocation_id=EXCLUDED.source_invocation_id,
                       created_at=EXCLUDED.created_at,expires_at=EXCLUDED.expires_at""",
                (cache_key, kwargs["provider"], kwargs["model"], Jsonb(kwargs["response"]),
                 kwargs.get("source_invocation_id"), kwargs.get("created_at"), kwargs.get("expires_at")),
            )

    @staticmethod
    def _obs_feedback(row: Any) -> RunFeedbackRecord:
        return RunFeedbackRecord(
            feedback_id=str(row["feedback_id"]),
            run_id=str(row["run_id"]),
            user_id=str(row["user_id"]),
            agent_id=str(row["agent_id"]),
            session_id=str(row["session_id"]),
            feedback_type=str(row["feedback_type"]),
            comment=str(row["comment"]),
            agent_revision_id=row["agent_revision_id"],
            turn_id=row["turn_id"],
            message_id=row["message_id"],
            rating=row["rating"],
            output_excerpt=row["output_excerpt"],
            status=str(row["status"]),
            metadata=dict(row["metadata"] or {}),
            created_at=_iso(row["created_at"]),
            updated_at=_iso(row["updated_at"]),
            reviewed_by=row["reviewed_by"],
            reviewed_at=_iso(row["reviewed_at"]),
        )

    @staticmethod
    def _obs_blob(row: Any) -> TraceBlobRecord:
        return TraceBlobRecord(blob_id=str(row["blob_id"]),run_id=str(row["run_id"]),
            invocation_id=row["invocation_id"],kind=str(row["kind"]),content_type=str(row["content_type"]),
            content=row["content"],storage_uri=row["storage_uri"],sha256=str(row["sha256"]),
            size_bytes=int(row["size_bytes"]),created_at=_iso(row["created_at"]),expires_at=_iso(row["expires_at"]))

    @staticmethod
    def _obs_span(row: Any) -> ExecutionSpanRecord:
        return ExecutionSpanRecord(span_id=str(row["span_id"]),trace_id=str(row["trace_id"]),
            parent_span_id=row["parent_span_id"],run_id=str(row["run_id"]),task_id=row["task_id"],turn_id=row["turn_id"],
            span_kind=str(row["span_kind"]),name=str(row["name"]),status=str(row["status"]),worker_id=row["worker_id"],
            attributes=dict(row["attributes"] or {}),error=dict(row["error"]) if row["error"] else None,
            started_at=_iso(row["started_at"]),first_token_at=_iso(row["first_token_at"]),
            finished_at=_iso(row["finished_at"]),duration_ms=row["duration_ms"],ttft_ms=row["ttft_ms"])

    @staticmethod
    def _obs_invocation(row: Any) -> ModelInvocationRecord:
        return ModelInvocationRecord(invocation_id=str(row["invocation_id"]),run_id=str(row["run_id"]),
            task_id=row["task_id"],turn_id=row["turn_id"],span_id=str(row["span_id"]),attempt=int(row["attempt"]),
            provider=str(row["provider"]),model=str(row["model"]),operation=str(row["operation"]),
            provider_request_id=row["provider_request_id"],agent_revision_id=row["agent_revision_id"],
            request_blob_id=row["request_blob_id"],response_blob_id=row["response_blob_id"],
            request_hash=row["request_hash"],response_hash=row["response_hash"],status=str(row["status"]),
            finish_reason=row["finish_reason"],reasoning_availability=str(row["reasoning_availability"]),
            usage=dict(row["usage"] or {}),cost_usd=float(row["cost_usd"] or 0),cache_status=str(row["cache_status"]),
            error=dict(row["error"]) if row["error"] else None,started_at=_iso(row["started_at"]),
            first_token_at=_iso(row["first_token_at"]),finished_at=_iso(row["finished_at"]),
            duration_ms=row["duration_ms"],ttft_ms=row["ttft_ms"])

    @staticmethod
    def _obs_reasoning(row: Any) -> ReasoningSegmentRecord:
        return ReasoningSegmentRecord(segment_id=str(row["segment_id"]),invocation_id=str(row["invocation_id"]),
            run_id=str(row["run_id"]),sequence=int(row["sequence"]),source=str(row["source"]),kind=str(row["kind"]),
            content=str(row["content"]),content_format=str(row["content_format"]),fidelity=str(row["fidelity"]),
            provider_block_type=row["provider_block_type"],token_count=row["token_count"],
            content_hash=str(row["content_hash"]),created_at=_iso(row["created_at"]))

    @staticmethod
    def _obs_replay(row: Any) -> ReplayRunRecord:
        return ReplayRunRecord(replay_id=str(row["replay_id"]),source_run_id=str(row["source_run_id"]),
            source_turn_id=row["source_turn_id"],new_run_id=row["new_run_id"],mode=str(row["mode"]),
            overrides=dict(row["overrides"] or {}),created_by=str(row["created_by"]),status=str(row["status"]),
            comparison=dict(row["comparison"]) if row["comparison"] else None,created_at=_iso(row["created_at"]),
            finished_at=_iso(row["finished_at"]))
