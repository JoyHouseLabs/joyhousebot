"""PostgreSQL state machine for governed Memory candidates."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from porthouse.domain.memory import memory_layer_for_path
from porthouse.storage.json_codec import Jsonb
from porthouse.storage.memory_candidate_records import MemoryCandidateRecord

_MEMORY_LAYER_SQL = """CASE
    WHEN document_path ~ '(^|/)PROFILE\\.md$' THEN 'profile'
    WHEN document_path LIKE 'agent/%%' THEN 'agent'
    WHEN document_path ~ '(^|/)(HISTORY\\.md|\\.abstract|[0-9]{4}-[0-9]{2}-[0-9]{2}\\.md)$'
        THEN 'episodic'
    ELSE 'long_term'
END"""


def _content_hash(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def _trim_blocks(content: str, count: int) -> str:
    if count <= 0:
        return content
    blocks = [block.strip() for block in content.split("\n\n") if block.strip()]
    return "\n\n".join(blocks[-count:]) + ("\n" if blocks else "")


class PostgresMemoryCandidateStoreMixin:
    def migrate_memory_candidates(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS memory_documents (
            scope_key TEXT NOT NULL,
            document_path TEXT NOT NULL,
            user_id TEXT,
            agent_id TEXT,
            content TEXT NOT NULL,
            version BIGINT NOT NULL DEFAULT 1,
            created_at_ms BIGINT NOT NULL,
            updated_at_ms BIGINT NOT NULL,
            PRIMARY KEY(scope_key, document_path)
        );
        CREATE INDEX IF NOT EXISTS ix_memory_documents_user
            ON memory_documents(user_id, agent_id, updated_at_ms DESC);

        CREATE TABLE IF NOT EXISTS memory_candidates (
            candidate_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            document_path TEXT NOT NULL,
            layer TEXT NOT NULL,
            operation TEXT NOT NULL CHECK (operation IN ('replace', 'append')),
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            base_document_version BIGINT NOT NULL DEFAULT 0,
            base_content_hash TEXT NOT NULL,
            source_run_id TEXT,
            source_task_id TEXT,
            source_turn_id TEXT,
            source_action_id TEXT,
            source_kind TEXT NOT NULL,
            source_fingerprint TEXT NOT NULL,
            fact_type TEXT NOT NULL,
            confidence DOUBLE PRECISION,
            data_classification TEXT NOT NULL DEFAULT 'confidential',
            supersedes JSONB NOT NULL DEFAULT '[]'::jsonb,
            evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            valid_until TIMESTAMPTZ,
            policy_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
            merge_options JSONB NOT NULL DEFAULT '{}'::jsonb,
            status TEXT NOT NULL DEFAULT 'pending',
            resolution TEXT,
            resolution_note TEXT,
            resolved_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            expires_at TIMESTAMPTZ NOT NULL,
            resolved_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_memory_candidates_owner_status
            ON memory_candidates(user_id, status, created_at DESC);
        CREATE INDEX IF NOT EXISTS ix_memory_candidates_source_run
            ON memory_candidates(source_run_id, created_at DESC)
            WHERE source_run_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS ix_memory_candidates_scope_document
            ON memory_candidates(scope_key, document_path, created_at DESC);
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341927,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="memory_candidates",
                version=1,
                ddl=ddl,
                description="governed long-term Memory candidate inbox and atomic merge",
            )

    def list_memory_documents(
        self,
        *,
        user_id: str,
        agent_id: str,
        layer: str | None = None,
        search: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """List owner-scoped Memory metadata without returning whole documents."""
        term = str(search or "").strip()
        params: list[Any] = [user_id, agent_id]
        filters: list[str] = []
        if layer:
            filters.append("layer=%s")
            params.append(layer)
        if term:
            filters.append("(document_path ILIKE %s OR content ILIKE %s)")
            pattern = f"%{term}%"
            params.extend((pattern, pattern))
        params.append(max(1, min(500, int(limit))))
        where = " WHERE " + " AND ".join(filters) if filters else ""
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT scope_key,document_path,layer,version,created_at_ms,updated_at_ms,
                          octet_length(content) AS size_bytes,left(content,280) AS preview
                     FROM (SELECT scope_key,document_path,content,version,created_at_ms,
                                  updated_at_ms,"""
                + _MEMORY_LAYER_SQL
                + """ AS layer FROM memory_documents
                            WHERE user_id=%s AND agent_id=%s) AS documents"""
                + where
                + " ORDER BY updated_at_ms DESC,document_path LIMIT %s",
                tuple(params),
            ).fetchall()
        items = [
            {
                "scope_key": str(row["scope_key"]),
                "document_path": str(row["document_path"]),
                "layer": str(row["layer"]),
                "version": int(row["version"]),
                "size_bytes": int(row["size_bytes"] or 0),
                "preview": str(row["preview"] or ""),
                "created_at_ms": int(row["created_at_ms"]),
                "updated_at_ms": int(row["updated_at_ms"]),
            }
            for row in rows
        ]
        return items

    def summarize_memory_documents(self, *, user_id: str, agent_id: str) -> dict[str, Any]:
        """Count durable documents by the same layer mapping used by the runtime."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT document_path FROM memory_documents
                    WHERE user_id=%s AND agent_id=%s""",
                (user_id, agent_id),
            ).fetchall()
        by_layer = {"profile": 0, "long_term": 0, "episodic": 0, "agent": 0}
        for row in rows:
            by_layer[memory_layer_for_path(str(row["document_path"]))] += 1
        return {"total": sum(by_layer.values()), "by_layer": by_layer}

    def get_memory_document(
        self,
        *,
        user_id: str,
        agent_id: str,
        scope_key: str,
        document_path: str,
    ) -> dict[str, Any] | None:
        """Read one document only when all owner/scope coordinates match."""
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT scope_key,document_path,content,version,created_at_ms,updated_at_ms,
                          octet_length(content) AS size_bytes
                     FROM memory_documents
                    WHERE user_id=%s AND agent_id=%s AND scope_key=%s AND document_path=%s""",
                (user_id, agent_id, scope_key, document_path),
            ).fetchone()
        if row is None:
            return None
        return {
            "scope_key": str(row["scope_key"]),
            "document_path": str(row["document_path"]),
            "layer": memory_layer_for_path(str(row["document_path"])),
            "content": str(row["content"]),
            "version": int(row["version"]),
            "size_bytes": int(row["size_bytes"] or 0),
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
        }

    def create_memory_candidate(self, **kwargs: Any) -> tuple[MemoryCandidateRecord, bool]:
        """Freeze one proposal against the current document revision."""
        with self._pool.connection() as conn, conn.transaction():
            document = conn.execute(
                """SELECT content,version FROM memory_documents
                   WHERE scope_key=%s AND document_path=%s FOR SHARE""",
                (kwargs["scope_key"], kwargs["document_path"]),
            ).fetchone()
            base_content = str(document["content"]) if document else ""
            base_version = int(document["version"]) if document else 0
            row = conn.execute(
                """INSERT INTO memory_candidates
                       (candidate_id,user_id,agent_id,scope_key,document_path,layer,
                        operation,content,content_hash,base_document_version,
                        base_content_hash,source_run_id,source_task_id,source_turn_id,
                        source_action_id,source_kind,source_fingerprint,fact_type,
                        confidence,data_classification,supersedes,evidence_refs,
                        valid_until,policy_snapshot,merge_options,expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           %s,%s,%s,%s,%s,%s,
                           CASE WHEN %s::integer IS NULL THEN NULL
                                ELSE clock_timestamp()+make_interval(secs => %s) END,
                           %s,%s,clock_timestamp()+make_interval(secs => %s))
                   ON CONFLICT(candidate_id) DO NOTHING RETURNING *,TRUE AS created""",
                (
                    kwargs["candidate_id"], kwargs["user_id"], kwargs["agent_id"],
                    kwargs["scope_key"], kwargs["document_path"], kwargs["layer"],
                    kwargs["operation"], kwargs["content"], kwargs["content_hash"],
                    base_version, _content_hash(base_content), kwargs.get("source_run_id"),
                    kwargs.get("source_task_id"), kwargs.get("source_turn_id"),
                    kwargs.get("source_action_id"), kwargs["source_kind"],
                    kwargs["source_fingerprint"], kwargs.get("fact_type") or kwargs["layer"],
                    kwargs.get("confidence"),
                    kwargs.get("data_classification") or "confidential",
                    Jsonb(kwargs.get("supersedes") or []),
                    Jsonb(kwargs.get("evidence_refs") or []),
                    kwargs.get("valid_for_seconds"), kwargs.get("valid_for_seconds"),
                    Jsonb(kwargs.get("policy_snapshot") or {}),
                    Jsonb(kwargs.get("merge_options") or {}),
                    kwargs["expires_in_seconds"],
                ),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT *,FALSE AS created FROM memory_candidates WHERE candidate_id=%s",
                    (kwargs["candidate_id"],),
                ).fetchone()
        assert row is not None
        record = self._memory_candidate(row)
        frozen = (
            record.user_id == kwargs["user_id"]
            and record.agent_id == kwargs["agent_id"]
            and record.scope_key == kwargs["scope_key"]
            and record.document_path == kwargs["document_path"]
            and record.operation == kwargs["operation"]
            and record.content_hash == kwargs["content_hash"]
            and record.source_fingerprint == kwargs["source_fingerprint"]
        )
        if not frozen:
            raise RuntimeError(f"Memory candidate identity conflict: {record.candidate_id}")
        return record, bool(row["created"])

    def get_memory_candidate(
        self, candidate_id: str, *, expected_user_id: str | None = None
    ) -> MemoryCandidateRecord | None:
        clause = " AND user_id=%s" if expected_user_id is not None else ""
        params = (
            (candidate_id, expected_user_id)
            if expected_user_id is not None
            else (candidate_id,)
        )
        with self._pool.connection() as conn, conn.transaction():
            self._expire_memory_candidates(conn, expected_user_id=expected_user_id)
            row = conn.execute(
                "SELECT * FROM memory_candidates WHERE candidate_id=%s" + clause,
                params,
            ).fetchone()
        return self._memory_candidate(row) if row else None

    def list_memory_candidates(
        self,
        *,
        user_id: str,
        agent_id: str | None = None,
        status: str | None = "pending",
        limit: int = 100,
    ) -> list[MemoryCandidateRecord]:
        filters = ["user_id=%s"]
        params: list[Any] = [user_id]
        if agent_id:
            filters.append("agent_id=%s")
            params.append(agent_id)
        if status:
            filters.append("status=%s")
            params.append(status)
        params.append(max(1, min(500, int(limit))))
        with self._pool.connection() as conn, conn.transaction():
            self._expire_memory_candidates(conn, expected_user_id=user_id)
            rows = conn.execute(
                "SELECT * FROM memory_candidates WHERE "
                + " AND ".join(filters)
                + " ORDER BY created_at DESC LIMIT %s",
                tuple(params),
            ).fetchall()
        return [self._memory_candidate(row) for row in rows]

    def resolve_memory_candidate(self, **kwargs: Any) -> tuple[MemoryCandidateRecord | None, str]:
        """Merge/reject once; document write and state transition share a transaction."""
        resolution = str(kwargs["resolution"])
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT * FROM memory_candidates
                   WHERE candidate_id=%s AND user_id=%s FOR UPDATE""",
                (kwargs["candidate_id"], kwargs["user_id"]),
            ).fetchone()
            if row is None:
                return None, "not_found"
            status = str(row["status"])
            if status in {"pending", "conflicted"} and row["expires_at"] <= conn.execute(
                "SELECT clock_timestamp() AS now"
            ).fetchone()["now"]:
                row = conn.execute(
                    """UPDATE memory_candidates SET status='expired',resolution='expired',
                           resolved_by='system:expiry',resolved_at=clock_timestamp(),
                           updated_at=clock_timestamp() WHERE candidate_id=%s RETURNING *""",
                    (kwargs["candidate_id"],),
                ).fetchone()
                return self._memory_candidate(row), "expired"
            if resolution == "reject":
                if status == "rejected":
                    return self._memory_candidate(row), "idempotent"
                if status not in {"pending", "conflicted"}:
                    return self._memory_candidate(row), "terminal_conflict"
                saved = conn.execute(
                    """UPDATE memory_candidates SET status='rejected',resolution='reject',
                           resolution_note=%s,resolved_by=%s,resolved_at=clock_timestamp(),
                           updated_at=clock_timestamp() WHERE candidate_id=%s RETURNING *""",
                    (kwargs.get("note"), kwargs["actor_id"], kwargs["candidate_id"]),
                ).fetchone()
                return self._memory_candidate(saved), "rejected"
            if status == "merged":
                return self._memory_candidate(row), "idempotent"
            if status != "pending":
                return self._memory_candidate(row), "terminal_conflict"
            if _content_hash(str(row["content"])) != str(row["content_hash"]):
                raise RuntimeError("Memory candidate content hash mismatch")
            document = conn.execute(
                """SELECT content,version FROM memory_documents
                   WHERE scope_key=%s AND document_path=%s FOR UPDATE""",
                (row["scope_key"], row["document_path"]),
            ).fetchone()
            current_content = str(document["content"]) if document else ""
            current_version = int(document["version"]) if document else 0
            if row["operation"] == "replace" and (
                current_version != int(row["base_document_version"])
                or _content_hash(current_content) != str(row["base_content_hash"])
            ):
                saved = conn.execute(
                    """UPDATE memory_candidates SET status='conflicted',
                           resolution_note='target document changed after proposal',
                           updated_at=clock_timestamp() WHERE candidate_id=%s RETURNING *""",
                    (kwargs["candidate_id"],),
                ).fetchone()
                return self._memory_candidate(saved), "document_conflict"
            content = (
                str(row["content"])
                if row["operation"] == "replace"
                else current_content + str(row["content"])
            )
            options = dict(row["merge_options"] or {})
            max_entries = int(options.get("max_entries") or 0)
            if max_entries > 0:
                content = _trim_blocks(content, max_entries)
            now_ms = conn.execute(
                "SELECT floor(extract(epoch FROM clock_timestamp())*1000)::bigint AS now_ms"
            ).fetchone()["now_ms"]
            conn.execute(
                """INSERT INTO memory_documents
                       (scope_key,document_path,user_id,agent_id,content,version,
                        created_at_ms,updated_at_ms)
                   VALUES (%s,%s,%s,%s,%s,1,%s,%s)
                   ON CONFLICT(scope_key,document_path) DO UPDATE SET
                     user_id=EXCLUDED.user_id,agent_id=EXCLUDED.agent_id,
                     content=EXCLUDED.content,version=memory_documents.version+1,
                     updated_at_ms=EXCLUDED.updated_at_ms""",
                (row["scope_key"], row["document_path"], row["user_id"], row["agent_id"],
                 content, now_ms, now_ms),
            )
            saved = conn.execute(
                """UPDATE memory_candidates SET status='merged',resolution='accept',
                       resolution_note=%s,resolved_by=%s,resolved_at=clock_timestamp(),
                       updated_at=clock_timestamp() WHERE candidate_id=%s RETURNING *""",
                (kwargs.get("note"), kwargs["actor_id"], kwargs["candidate_id"]),
            ).fetchone()
        return self._memory_candidate(saved), "merged"

    @staticmethod
    def _expire_memory_candidates(conn: Any, *, expected_user_id: str | None = None) -> None:
        clause = " AND user_id=%s" if expected_user_id is not None else ""
        params = (expected_user_id,) if expected_user_id is not None else ()
        conn.execute(
            """UPDATE memory_candidates SET status='expired',resolution='expired',
                   resolved_by='system:expiry',resolved_at=clock_timestamp(),
                   updated_at=clock_timestamp()
               WHERE status IN ('pending','conflicted')
                 AND expires_at <= clock_timestamp()"""
            + clause,
            params,
        )

    @staticmethod
    def _memory_candidate(row: dict[str, Any]) -> MemoryCandidateRecord:
        from porthouse.storage.postgres_store import _iso, _json

        return MemoryCandidateRecord(
            candidate_id=str(row["candidate_id"]), user_id=str(row["user_id"]),
            agent_id=str(row["agent_id"]), scope_key=str(row["scope_key"]),
            document_path=str(row["document_path"]), layer=str(row["layer"]),
            operation=str(row["operation"]), content=str(row["content"]),
            content_hash=str(row["content_hash"]),
            base_document_version=int(row["base_document_version"]),
            base_content_hash=str(row["base_content_hash"]),
            source_run_id=row["source_run_id"], source_task_id=row["source_task_id"],
            source_turn_id=row["source_turn_id"], source_action_id=row["source_action_id"],
            source_kind=str(row["source_kind"]),
            source_fingerprint=str(row["source_fingerprint"]),
            fact_type=str(row["fact_type"]),
            confidence=float(row["confidence"]) if row["confidence"] is not None else None,
            data_classification=str(row["data_classification"]),
            supersedes=list(_json(row["supersedes"], [])),
            evidence_refs=list(_json(row["evidence_refs"], [])),
            valid_until=_iso(row["valid_until"]),
            policy_snapshot=dict(_json(row["policy_snapshot"], {})),
            merge_options=dict(_json(row["merge_options"], {})),
            status=str(row["status"]), resolution=row["resolution"],
            resolution_note=row["resolution_note"], resolved_by=row["resolved_by"],
            created_at=_iso(row["created_at"]) or "", expires_at=_iso(row["expires_at"]) or "",
            resolved_at=_iso(row["resolved_at"]), updated_at=_iso(row["updated_at"]) or "",
        )
