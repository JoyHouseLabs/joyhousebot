"""Pagination queries for the runtime Run monitoring table."""

from __future__ import annotations


class PostgresRunListingStoreMixin:
    def count_runtime_runs(
        self,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        status: str | None = None,
        search: str | None = None,
    ) -> int:
        """Count a filtered Run list for the admin pagination envelope."""
        clauses, params = [], []
        if user_id:
            clauses.append("user_id=%s")
            params.append(user_id)
        if session_id:
            clauses.append("session_id=%s")
            params.append(session_id)
        if agent_id:
            clauses.append("agent_id=%s")
            params.append(agent_id)
        if status:
            clauses.append("status=%s")
            params.append(status)
        if search and search.strip():
            pattern = f"%{search.strip()}%"
            clauses.append(
                "(run_id ILIKE %s OR session_id ILIKE %s OR agent_id ILIKE %s "
                "OR COALESCE(status_summary, '') ILIKE %s OR COALESCE(prompt, '') ILIKE %s)"
            )
            params.extend([pattern] * 5)
        query = "SELECT COUNT(*) AS count FROM runtime_runs"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with self._pool.connection() as conn:
            row = conn.execute(query, params).fetchone()
        return int(row["count"] if isinstance(row, dict) else row[0])
