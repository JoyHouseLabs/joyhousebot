"""PostgreSQL collection management for private Knowledge assets."""

from __future__ import annotations

import time
import uuid
from typing import Any

from porthouse.storage.json_codec import Jsonb

KNOWLEDGE_BASE_DDL = """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_knowledge_documents_doc_user
        ON knowledge_documents(doc_id, user_id);
    CREATE TABLE IF NOT EXISTS knowledge_bases (
        knowledge_base_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active'
            CHECK (status IN ('active','archived')),
        created_by TEXT NOT NULL,
        created_at_ms BIGINT NOT NULL,
        updated_at_ms BIGINT NOT NULL,
        UNIQUE(user_id, name),
        UNIQUE(knowledge_base_id, user_id)
    );
    CREATE INDEX IF NOT EXISTS ix_knowledge_bases_user
        ON knowledge_bases(user_id, updated_at_ms DESC);
    ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS created_by TEXT;
    UPDATE knowledge_bases SET created_by='schema-migration' WHERE created_by IS NULL;
    ALTER TABLE knowledge_bases ALTER COLUMN created_by SET NOT NULL;
    CREATE UNIQUE INDEX IF NOT EXISTS ux_knowledge_bases_user_name
        ON knowledge_bases(user_id, name);
    CREATE UNIQUE INDEX IF NOT EXISTS ux_knowledge_bases_base_user
        ON knowledge_bases(knowledge_base_id, user_id);
    CREATE TABLE IF NOT EXISTS knowledge_base_documents (
        knowledge_base_id TEXT NOT NULL,
        doc_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at_ms BIGINT NOT NULL,
        PRIMARY KEY(knowledge_base_id, doc_id),
        FOREIGN KEY(knowledge_base_id, user_id)
            REFERENCES knowledge_bases(knowledge_base_id, user_id) ON DELETE CASCADE,
        FOREIGN KEY(doc_id, user_id)
            REFERENCES knowledge_documents(doc_id, user_id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS ix_knowledge_base_documents_user
        ON knowledge_base_documents(user_id, knowledge_base_id, created_at_ms DESC);
    ALTER TABLE knowledge_base_documents ADD COLUMN IF NOT EXISTS created_by TEXT;
    UPDATE knowledge_base_documents
       SET created_by='schema-migration' WHERE created_by IS NULL;
    ALTER TABLE knowledge_base_documents ALTER COLUMN created_by SET NOT NULL;
    CREATE TABLE IF NOT EXISTS knowledge_base_events (
        event_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        knowledge_base_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        data JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at_ms BIGINT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_knowledge_base_events_user
        ON knowledge_base_events(user_id, created_at_ms DESC);
"""


class KnowledgeBaseRepositoryMixin:
    """Owner-scoped collection operations mixed into ``KnowledgeRepository``."""

    def create_knowledge_base(
        self,
        *,
        knowledge_base_id: str,
        user_id: str,
        name: str,
        description: str,
        actor_id: str,
    ) -> dict[str, Any] | None:
        now_ms = int(time.time() * 1000)
        with self._connection() as connection:
            row = connection.execute(
                """INSERT INTO knowledge_bases
                   (knowledge_base_id,user_id,name,description,status,created_by,
                    created_at_ms,updated_at_ms)
                   VALUES (%s,%s,%s,%s,'active',%s,%s,%s)
                   ON CONFLICT(user_id,name) DO NOTHING RETURNING *""",
                (
                    knowledge_base_id,
                    user_id,
                    name,
                    description,
                    actor_id,
                    now_ms,
                    now_ms,
                ),
            ).fetchone()
            if row is None:
                return None
            self._record_base_event(
                connection,
                user_id=user_id,
                knowledge_base_id=knowledge_base_id,
                event_type="created",
                actor_id=actor_id,
                data={"name": name, "description": description},
                now_ms=now_ms,
            )
        return self._knowledge_base_summary(row)

    def list_knowledge_bases(
        self, *, user_id: str, status: str | None = None
    ) -> list[dict[str, Any]]:
        clauses = ["b.user_id=%s"]
        params: list[Any] = [user_id]
        if status:
            clauses.append("b.status=%s")
            params.append(status)
        with self._connection() as connection:
            rows = connection.execute(
                f"""SELECT b.knowledge_base_id,b.name,b.description,b.status,
                           b.created_by,b.created_at_ms,b.updated_at_ms,
                           COUNT(DISTINCT m.doc_id) AS document_count,
                           COUNT(c.doc_id) AS chunk_count,
                           COALESCE(SUM(octet_length(c.content)),0) AS size_bytes
                      FROM knowledge_bases b
                 LEFT JOIN knowledge_base_documents m
                        ON m.knowledge_base_id=b.knowledge_base_id AND m.user_id=b.user_id
                 LEFT JOIN knowledge_chunks c
                        ON c.doc_id=m.doc_id AND c.user_id=b.user_id
                     WHERE {" AND ".join(clauses)}
                  GROUP BY b.knowledge_base_id,b.name,b.description,b.status,
                           b.created_by,b.created_at_ms,b.updated_at_ms
                  ORDER BY CASE b.status WHEN 'active' THEN 0 ELSE 1 END,
                           b.updated_at_ms DESC,b.name""",
                tuple(params),
            ).fetchall()
        return [self._knowledge_base_summary(row) for row in rows]

    def get_knowledge_base(
        self, *, user_id: str, knowledge_base_id: str
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT b.knowledge_base_id,b.name,b.description,b.status,
                          b.created_by,b.created_at_ms,b.updated_at_ms,
                          COUNT(DISTINCT m.doc_id) AS document_count,
                          COUNT(c.doc_id) AS chunk_count,
                          COALESCE(SUM(octet_length(c.content)),0) AS size_bytes
                     FROM knowledge_bases b
                LEFT JOIN knowledge_base_documents m
                       ON m.knowledge_base_id=b.knowledge_base_id AND m.user_id=b.user_id
                LEFT JOIN knowledge_chunks c
                       ON c.doc_id=m.doc_id AND c.user_id=b.user_id
                    WHERE b.user_id=%s AND b.knowledge_base_id=%s
                 GROUP BY b.knowledge_base_id,b.name,b.description,b.status,
                          b.created_by,b.created_at_ms,b.updated_at_ms""",
                (user_id, knowledge_base_id),
            ).fetchone()
        return self._knowledge_base_summary(row) if row else None

    def update_knowledge_base(
        self,
        *,
        user_id: str,
        knowledge_base_id: str,
        actor_id: str,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        now_ms = int(time.time() * 1000)
        with self._connection() as connection:
            current = connection.execute(
                """SELECT * FROM knowledge_bases
                    WHERE user_id=%s AND knowledge_base_id=%s FOR UPDATE""",
                (user_id, knowledge_base_id),
            ).fetchone()
            if current is None:
                return None, "not_found"
            if name and name != current["name"]:
                duplicate = connection.execute(
                    """SELECT 1 FROM knowledge_bases
                        WHERE user_id=%s AND name=%s AND knowledge_base_id<>%s""",
                    (user_id, name, knowledge_base_id),
                ).fetchone()
                if duplicate:
                    return None, "name_conflict"
            next_name = name if name is not None else str(current["name"])
            next_description = (
                description if description is not None else str(current["description"])
            )
            next_status = status if status is not None else str(current["status"])
            connection.execute(
                """UPDATE knowledge_bases
                      SET name=%s,description=%s,status=%s,updated_at_ms=%s
                    WHERE user_id=%s AND knowledge_base_id=%s""",
                (
                    next_name,
                    next_description,
                    next_status,
                    now_ms,
                    user_id,
                    knowledge_base_id,
                ),
            )
            self._record_base_event(
                connection,
                user_id=user_id,
                knowledge_base_id=knowledge_base_id,
                event_type="updated",
                actor_id=actor_id,
                data={
                    "name": next_name,
                    "description": next_description,
                    "status": next_status,
                },
                now_ms=now_ms,
            )
        updated = self.get_knowledge_base(
            user_id=user_id, knowledge_base_id=knowledge_base_id
        )
        return updated, "updated"

    def delete_knowledge_base(
        self, *, user_id: str, knowledge_base_id: str, actor_id: str
    ) -> dict[str, Any] | None:
        now_ms = int(time.time() * 1000)
        with self._connection() as connection:
            row = connection.execute(
                """SELECT knowledge_base_id,name,description,status
                     FROM knowledge_bases
                    WHERE user_id=%s AND knowledge_base_id=%s FOR UPDATE""",
                (user_id, knowledge_base_id),
            ).fetchone()
            if row is None:
                return None
            snapshot = {
                "name": str(row["name"]),
                "description": str(row["description"]),
                "status": str(row["status"]),
            }
            connection.execute(
                "DELETE FROM knowledge_bases WHERE user_id=%s AND knowledge_base_id=%s",
                (user_id, knowledge_base_id),
            )
            self._record_base_event(
                connection,
                user_id=user_id,
                knowledge_base_id=knowledge_base_id,
                event_type="deleted",
                actor_id=actor_id,
                data=snapshot,
                now_ms=now_ms,
            )
        return snapshot

    def bind_document(
        self,
        *,
        user_id: str,
        knowledge_base_id: str,
        doc_id: str,
        actor_id: str,
    ) -> str:
        now_ms = int(time.time() * 1000)
        with self._connection() as connection:
            base = connection.execute(
                """SELECT 1 FROM knowledge_bases
                    WHERE user_id=%s AND knowledge_base_id=%s FOR SHARE""",
                (user_id, knowledge_base_id),
            ).fetchone()
            if base is None:
                return "base_not_found"
            document = connection.execute(
                """SELECT 1 FROM knowledge_documents
                    WHERE user_id=%s AND doc_id=%s FOR SHARE""",
                (user_id, doc_id),
            ).fetchone()
            if document is None:
                return "document_not_found"
            created = connection.execute(
                """INSERT INTO knowledge_base_documents
                   (knowledge_base_id,doc_id,user_id,created_by,created_at_ms)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT(knowledge_base_id,doc_id) DO NOTHING RETURNING doc_id""",
                (knowledge_base_id, doc_id, user_id, actor_id, now_ms),
            ).fetchone()
            if created:
                connection.execute(
                    """UPDATE knowledge_bases SET updated_at_ms=%s
                        WHERE user_id=%s AND knowledge_base_id=%s""",
                    (now_ms, user_id, knowledge_base_id),
                )
                self._record_base_event(
                    connection,
                    user_id=user_id,
                    knowledge_base_id=knowledge_base_id,
                    event_type="document_added",
                    actor_id=actor_id,
                    data={"doc_id": doc_id},
                    now_ms=now_ms,
                )
        return "bound" if created else "already_bound"

    def unbind_document(
        self,
        *,
        user_id: str,
        knowledge_base_id: str,
        doc_id: str,
        actor_id: str,
    ) -> str:
        now_ms = int(time.time() * 1000)
        with self._connection() as connection:
            base = connection.execute(
                """SELECT 1 FROM knowledge_bases
                    WHERE user_id=%s AND knowledge_base_id=%s FOR SHARE""",
                (user_id, knowledge_base_id),
            ).fetchone()
            if base is None:
                return "base_not_found"
            removed = connection.execute(
                """DELETE FROM knowledge_base_documents
                    WHERE user_id=%s AND knowledge_base_id=%s AND doc_id=%s
                    RETURNING doc_id""",
                (user_id, knowledge_base_id, doc_id),
            ).fetchone()
            if removed:
                connection.execute(
                    """UPDATE knowledge_bases SET updated_at_ms=%s
                        WHERE user_id=%s AND knowledge_base_id=%s""",
                    (now_ms, user_id, knowledge_base_id),
                )
                self._record_base_event(
                    connection,
                    user_id=user_id,
                    knowledge_base_id=knowledge_base_id,
                    event_type="document_removed",
                    actor_id=actor_id,
                    data={"doc_id": doc_id},
                    now_ms=now_ms,
                )
        return "unbound" if removed else "not_bound"

    @staticmethod
    def _knowledge_base_summary(row: Any) -> dict[str, Any]:
        return {
            "knowledge_base_id": str(row["knowledge_base_id"]),
            "name": str(row["name"]),
            "description": str(row["description"] or ""),
            "status": str(row["status"]),
            "created_by": str(row["created_by"]),
            "document_count": int(row.get("document_count") or 0),
            "chunk_count": int(row.get("chunk_count") or 0),
            "size_bytes": int(row.get("size_bytes") or 0),
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
        }

    @staticmethod
    def _record_base_event(
        connection: Any,
        *,
        user_id: str,
        knowledge_base_id: str,
        event_type: str,
        actor_id: str,
        data: dict[str, Any],
        now_ms: int,
    ) -> None:
        connection.execute(
            """INSERT INTO knowledge_base_events
               (event_id,user_id,knowledge_base_id,event_type,actor_id,data,created_at_ms)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (
                f"kbe_{uuid.uuid4().hex}",
                user_id,
                knowledge_base_id,
                event_type,
                actor_id,
                Jsonb(data),
                now_ms,
            ),
        )
