"""PostgreSQL repository for scoped Agent memory documents."""

from __future__ import annotations

import re
import time
from contextlib import contextmanager
from typing import Any, Iterator

_CANONICAL_SCOPE = re.compile(r"^user:(?P<user>.+):agent:(?P<agent>[^:]+)$")


class MemoryRepository:
    """Store each memory document as a row; PostgreSQL is the shared authority."""

    def __init__(self, store: Any) -> None:
        self.store = store
        if getattr(store, "backend_name", None) != "postgres":
            raise TypeError("MemoryRepository requires PostgreSQL runtime store")
        self.migrate()

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        with self.store._pool.connection() as connection:
            with connection.transaction():
                yield connection

    def migrate(self) -> None:
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
        """
        with self.store._pool.connection() as connection:
            with connection.transaction():
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (872341913,))
                connection.execute(ddl)

    @staticmethod
    def _identity(scope_key: str) -> tuple[str | None, str | None]:
        match = _CANONICAL_SCOPE.match(scope_key)
        if not match:
            return None, None
        return match.group("user"), match.group("agent")

    def read(self, scope_key: str, document_path: str) -> str:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT content FROM memory_documents
                    WHERE scope_key=%s AND document_path=%s""",
                (scope_key, document_path),
            ).fetchone()
        return str(row["content"]) if row else ""

    def write(self, scope_key: str, document_path: str, content: str) -> int:
        now_ms = int(time.time() * 1000)
        user_id, agent_id = self._identity(scope_key)
        query = """INSERT INTO memory_documents
            (scope_key,document_path,user_id,agent_id,content,version,created_at_ms,updated_at_ms)
            VALUES (%s,%s,%s,%s,%s,1,%s,%s)
            ON CONFLICT(scope_key,document_path) DO UPDATE SET
              user_id=EXCLUDED.user_id,agent_id=EXCLUDED.agent_id,
              content=EXCLUDED.content,version=memory_documents.version+1,
              updated_at_ms=EXCLUDED.updated_at_ms RETURNING version"""
        with self._connection() as connection:
            row = connection.execute(
                query,
                (scope_key, document_path, user_id, agent_id, content, now_ms, now_ms),
            ).fetchone()
        return int(row["version"])

    def append(self, scope_key: str, document_path: str, suffix: str) -> str:
        """Append atomically so concurrent agent turns cannot overwrite one another."""
        now_ms = int(time.time() * 1000)
        user_id, agent_id = self._identity(scope_key)
        query = """INSERT INTO memory_documents
            (scope_key,document_path,user_id,agent_id,content,version,created_at_ms,updated_at_ms)
            VALUES (%s,%s,%s,%s,%s,1,%s,%s)
            ON CONFLICT(scope_key,document_path) DO UPDATE SET
              content=memory_documents.content || EXCLUDED.content,
              version=memory_documents.version+1,
              updated_at_ms=EXCLUDED.updated_at_ms RETURNING content"""
        with self._connection() as connection:
            row = connection.execute(
                query,
                (scope_key, document_path, user_id, agent_id, suffix, now_ms, now_ms),
            ).fetchone()
        return str(row["content"])

    def list_documents(self, scope_key: str) -> dict[str, str]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT document_path,content FROM memory_documents
                    WHERE scope_key=%s ORDER BY document_path""",
                (scope_key,),
            ).fetchall()
        return {str(row["document_path"]): str(row["content"]) for row in rows}
