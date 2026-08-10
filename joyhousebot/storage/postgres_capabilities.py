"""PostgreSQL persistence for immutable capabilities and invocations."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from joyhousebot.domain.capabilities import (
    CapabilityDefinition,
    CapabilityInvocation,
)
from joyhousebot.storage.json_codec import Jsonb
from joyhousebot.storage.platform_records import CapabilityInvocationRecord


class PostgresCapabilityStoreMixin:
    def migrate_capabilities(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS capability_definitions (
            capability_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS capability_versions (
            capability_id TEXT NOT NULL REFERENCES capability_definitions(capability_id)
                ON DELETE CASCADE,
            version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'published',
            definition JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            published_at TIMESTAMPTZ,
            PRIMARY KEY(capability_id, version)
        );
        CREATE INDEX IF NOT EXISTS ix_capability_versions_published
            ON capability_versions(capability_id, created_at DESC)
            WHERE status = 'published';
        CREATE TABLE IF NOT EXISTS capability_runtime_settings (
            capability_id TEXT PRIMARY KEY REFERENCES capability_definitions(capability_id)
                ON DELETE CASCADE,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_by TEXT NOT NULL DEFAULT 'system',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS capability_invocations (
            invocation_id TEXT PRIMARY KEY,
            capability_id TEXT NOT NULL,
            capability_version TEXT NOT NULL,
            capability_kind TEXT NOT NULL,
            user_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            task_id TEXT,
            trace_id TEXT NOT NULL,
            status TEXT NOT NULL,
            input JSONB NOT NULL DEFAULT '{}'::jsonb,
            result JSONB,
            error JSONB,
            idempotency_key TEXT NOT NULL,
            timeout_seconds INTEGER NOT NULL,
            attempt INTEGER NOT NULL DEFAULT 0,
            worker_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(user_id, run_id, capability_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS ix_capability_invocations_run_created
            ON capability_invocations(run_id, created_at);
        CREATE INDEX IF NOT EXISTS ix_capability_invocations_user_created
            ON capability_invocations(user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS ix_capability_invocations_active
            ON capability_invocations(status, created_at)
            WHERE status IN ('queued', 'running', 'accepted');
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341908,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="capabilities",
                version=1,
                ddl=ddl,
                description="capability definitions, versions, and invocations",
            )
            governance_ddl = """
            ALTER TABLE capability_definitions
                ADD COLUMN IF NOT EXISTS current_version TEXT;
            UPDATE capability_definitions d SET current_version = selected.version
            FROM (
                SELECT DISTINCT ON (capability_id) capability_id,version
                FROM capability_versions WHERE status='published'
                ORDER BY capability_id,published_at DESC NULLS LAST,created_at DESC
            ) selected
            WHERE selected.capability_id=d.capability_id AND d.current_version IS NULL;
            """
            conn.execute(governance_ddl)
            self._record_migration(
                conn,
                name="capabilities",
                version=2,
                ddl=governance_ddl,
                description="explicit active capability version for staged rollout and rollback",
            )
    def discover_capability_release(
        self, definition: CapabilityDefinition, *, actor_id: str = "system:worker-discovery"
    ) -> None:
        """Record a locally loaded release without making it executable."""
        value = definition.to_dict()
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                """INSERT INTO capability_definitions
                       (capability_id,kind,name,description)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT(capability_id) DO UPDATE SET
                       kind=excluded.kind,name=excluded.name,
                       description=excluded.description,updated_at=clock_timestamp()""",
                (
                    definition.ref.capability_id,
                    definition.ref.kind.value,
                    definition.name,
                    definition.description,
                ),
            )
            existing = conn.execute(
                """SELECT definition FROM capability_versions
                   WHERE capability_id=%s AND version=%s""",
                (definition.ref.capability_id, definition.ref.version),
            ).fetchone()
            if existing and dict(existing["definition"]) != value:
                raise ValueError("discovered capability version conflicts with immutable catalog")
            inserted = conn.execute(
                """INSERT INTO capability_versions
                       (capability_id,version,status,definition)
                   VALUES (%s,%s,'discovered',%s)
                   ON CONFLICT(capability_id,version) DO NOTHING""",
                (
                    definition.ref.capability_id,
                    definition.ref.version,
                    Jsonb(value),
                ),
            )
            if inserted.rowcount:
                conn.execute(
                    """INSERT INTO configuration_events
                           (aggregate_type,aggregate_id,revision_id,event_type,actor_id)
                       VALUES ('capability',%s,%s,'discovered',%s)""",
                    (
                        definition.ref.capability_id,
                        definition.ref.version,
                        actor_id,
                    ),
                )

    def stage_capability_release(
        self,
        definition: CapabilityDefinition,
        *,
        actor_id: str = "system",
        activation_mode: str = "automatic",
        timeout_seconds: int = 300,
        auto_rollback: bool = True,
        require_healthy_workers: bool = True,
    ) -> None:
        value = definition.to_dict()
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                """INSERT INTO capability_definitions
                       (capability_id,kind,name,description)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT(capability_id) DO UPDATE SET
                       kind=excluded.kind,name=excluded.name,
                       description=excluded.description,updated_at=clock_timestamp()""",
                (
                    definition.ref.capability_id,
                    definition.ref.kind.value,
                    definition.name,
                    definition.description,
                ),
            )
            existing = conn.execute(
                """SELECT definition FROM capability_versions
                   WHERE capability_id=%s AND version=%s FOR UPDATE""",
                (definition.ref.capability_id, definition.ref.version),
            ).fetchone()
            if existing and dict(existing["definition"]) != value:
                raise ValueError("published capability versions are immutable")
            conn.execute(
                """INSERT INTO capability_versions
                       (capability_id,version,status,definition)
                   VALUES (%s,%s,'staged',%s)
                   ON CONFLICT(capability_id,version) DO NOTHING""",
                (definition.ref.capability_id, definition.ref.version, Jsonb(value)),
            )
            conn.execute(
                """UPDATE capability_versions SET status='staged'
                   WHERE capability_id=%s AND version=%s AND status='discovered'""",
                (definition.ref.capability_id, definition.ref.version),
            )
            conn.execute(
                """INSERT INTO configuration_events
                       (aggregate_type,aggregate_id,revision_id,event_type,actor_id)
                   VALUES ('capability',%s,%s,'publish.requested',%s)""",
                (definition.ref.capability_id, definition.ref.version, actor_id),
            )
            self._create_configuration_rollout(
                conn,
                aggregate_type="capability",
                aggregate_id=definition.ref.capability_id,
                revision_id=definition.ref.version,
                actor_id=actor_id,
                activation_mode=activation_mode,
                timeout_seconds=timeout_seconds,
                auto_rollback=auto_rollback,
                require_healthy_workers=require_healthy_workers,
            )
            self._notify(conn, f"config:capability:{definition.ref.capability_id}")

    def get_capability_release_definition(
        self, capability_id: str, version: str
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT definition FROM capability_versions
                   WHERE capability_id=%s AND version=%s""",
                (capability_id, version),
            ).fetchone()
        return dict(row["definition"]) if row else None

    def get_capability_definition(
        self, capability_id: str, version: str | None = None
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            if version:
                row = conn.execute(
                    """SELECT definition FROM capability_versions
                       WHERE capability_id=%s AND version=%s AND status='published'""",
                    (capability_id, version),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT v.definition FROM capability_definitions d
                       JOIN capability_versions v ON v.capability_id=d.capability_id
                            AND v.version=d.current_version
                       WHERE d.capability_id=%s AND v.status='published'""",
                    (capability_id,),
                ).fetchone()
        return dict(row["definition"]) if row else None

    def list_capability_definitions(self) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT v.definition FROM capability_definitions d
                   JOIN capability_versions v ON v.capability_id=d.capability_id
                        AND v.version=d.current_version
                   WHERE v.status='published' ORDER BY d.capability_id"""
            ).fetchall()
        return [dict(row["definition"]) for row in rows]

    def get_capability_runtime_settings(self, capability_id: str) -> dict[str, Any]:
        """Return the mutable operational overlay, never the immutable definition."""
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT enabled, configuration, updated_by, updated_at
                   FROM capability_runtime_settings WHERE capability_id=%s""",
                (capability_id,),
            ).fetchone()
        if row is None:
            return {"capability_id": capability_id, "enabled": True, "configuration": {},
                    "updated_by": None, "updated_at": None}
        from joyhousebot.storage.postgres_store import _iso, _json
        return {
            "capability_id": capability_id,
            "enabled": bool(row["enabled"]),
            "configuration": dict(_json(row["configuration"], {})),
            "updated_by": str(row["updated_by"]),
            "updated_at": _iso(row["updated_at"]),
        }

    def save_capability_runtime_settings(
        self,
        capability_id: str,
        *,
        enabled: bool,
        configuration: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        definition = self.get_capability_definition(capability_id)
        if definition is None:
            raise ValueError("capability not found")
        if not isinstance(configuration, dict):
            raise ValueError("runtime configuration must be an object")
        _reject_secret_configuration(configuration)
        schema = dict(definition.get("configuration_schema") or {})
        if schema:
            try:
                Draft202012Validator(schema).validate(configuration)
            except ValidationError as exc:
                path = ".".join(str(item) for item in exc.absolute_path) or "configuration"
                raise ValueError(f"runtime configuration invalid at {path}: {exc.message}") from exc
        elif configuration:
            raise ValueError("capability does not declare configurable runtime parameters")
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                """INSERT INTO capability_runtime_settings
                       (capability_id,enabled,configuration,updated_by,updated_at)
                   VALUES (%s,%s,%s,%s,clock_timestamp())
                   ON CONFLICT(capability_id) DO UPDATE SET
                       enabled=EXCLUDED.enabled, configuration=EXCLUDED.configuration,
                       updated_by=EXCLUDED.updated_by, updated_at=clock_timestamp()""",
                (capability_id, enabled, Jsonb(configuration), actor_id),
            )
            conn.execute(
                """INSERT INTO configuration_events
                       (aggregate_type,aggregate_id,revision_id,event_type,actor_id)
                   VALUES ('capability_runtime_settings',%s,'runtime','updated',%s)""",
                (capability_id, actor_id),
            )
            self._notify(conn, f"config:capability-settings:{capability_id}")
        return self.get_capability_runtime_settings(capability_id)

    def create_capability_invocation(
        self, invocation: CapabilityInvocation
    ) -> tuple[CapabilityInvocationRecord, bool]:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO capability_invocations
                       (invocation_id,capability_id,capability_version,capability_kind,
                        user_id,agent_id,session_id,run_id,task_id,trace_id,status,input,
                        idempotency_key,timeout_seconds)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued',%s,%s,%s)
                   ON CONFLICT(user_id,run_id,capability_id,idempotency_key) DO NOTHING
                   RETURNING *,TRUE AS created""",
                (
                    invocation.invocation_id,
                    invocation.capability.capability_id,
                    invocation.capability.version,
                    invocation.capability.kind.value,
                    invocation.user_id,
                    invocation.agent_id,
                    invocation.session_id,
                    invocation.run_id,
                    invocation.task_id,
                    invocation.trace_id,
                    Jsonb(invocation.input),
                    invocation.idempotency_key,
                    invocation.timeout_seconds,
                ),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """SELECT *,FALSE AS created FROM capability_invocations
                       WHERE user_id=%s AND run_id=%s AND capability_id=%s
                         AND idempotency_key=%s""",
                    (
                        invocation.user_id,
                        invocation.run_id,
                        invocation.capability.capability_id,
                        invocation.idempotency_key,
                    ),
                ).fetchone()
        assert row is not None
        return self._capability_invocation(row), bool(row["created"])

    def get_capability_invocation(
        self, invocation_id: str
    ) -> CapabilityInvocationRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM capability_invocations WHERE invocation_id=%s",
                (invocation_id,),
            ).fetchone()
        return self._capability_invocation(row) if row else None

    def start_capability_invocation(self, invocation_id: str, *, worker_id: str) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE capability_invocations SET status='running',worker_id=%s,
                       attempt=attempt+1,started_at=COALESCE(started_at,clock_timestamp()),
                       updated_at=clock_timestamp()
                   WHERE invocation_id=%s AND status='queued' RETURNING invocation_id""",
                (worker_id, invocation_id),
            ).fetchone()
        return row is not None

    def finish_capability_invocation(
        self,
        invocation_id: str,
        *,
        status: str,
        result: dict[str, Any] | None,
        error: dict[str, Any] | None,
    ) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE capability_invocations SET status=%s,result=%s,error=%s,
                       finished_at=clock_timestamp(),updated_at=clock_timestamp()
                   WHERE invocation_id=%s AND status IN ('queued','running','accepted')
                   RETURNING invocation_id""",
                (status, Jsonb(result) if result is not None else None,
                 Jsonb(error) if error is not None else None, invocation_id),
            ).fetchone()
        return row is not None

    def list_capability_invocations(
        self, run_id: str, *, expected_user_id: str | None = None
    ) -> list[CapabilityInvocationRecord]:
        clauses = ["run_id=%s"]
        params: list[Any] = [run_id]
        if expected_user_id is not None:
            clauses.append("user_id=%s")
            params.append(expected_user_id)
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM capability_invocations WHERE {' AND '.join(clauses)} "
                "ORDER BY created_at",
                params,
            ).fetchall()
        return [self._capability_invocation(row) for row in rows]

    @staticmethod
    def _capability_invocation(row: dict[str, Any]) -> CapabilityInvocationRecord:
        from joyhousebot.storage.postgres_store import _iso, _json

        return CapabilityInvocationRecord(
            invocation_id=str(row["invocation_id"]),
            capability_id=str(row["capability_id"]),
            capability_version=str(row["capability_version"]),
            capability_kind=str(row["capability_kind"]),
            user_id=str(row["user_id"]),
            agent_id=str(row["agent_id"]),
            session_id=str(row["session_id"]),
            run_id=str(row["run_id"]),
            task_id=row["task_id"],
            trace_id=str(row["trace_id"]),
            status=str(row["status"]),
            input=dict(_json(row["input"], {})),
            result=_json(row["result"]),
            error=_json(row["error"]),
            idempotency_key=str(row["idempotency_key"]),
            timeout_seconds=int(row["timeout_seconds"]),
            attempt=int(row["attempt"]),
            worker_id=row["worker_id"],
            created_at=_iso(row["created_at"]) or "",
            started_at=_iso(row["started_at"]),
            finished_at=_iso(row["finished_at"]),
            updated_at=_iso(row["updated_at"]) or "",
        )


def _reject_secret_configuration(value: Any, path: str = "") -> None:
    """Keep credentials out of the control-plane database and UI payloads."""
    secret_markers = ("password", "secret", "token", "api_key", "apikey", "authorization")
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).lower()
            child_path = f"{path}.{key}" if path else str(key)
            if any(marker in key_text for marker in secret_markers):
                raise ValueError(
                    f"runtime configuration must not contain secrets ({child_path}); use deployment secret storage"
                )
            _reject_secret_configuration(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_configuration(child, f"{path}[{index}]")
