"""PostgreSQL persistence for immutable platform Agent revisions."""

from __future__ import annotations

from typing import Any

from joyhousebot.domain.agents import (
    AgentDefinition,
    AgentExecutionSnapshot,
    AgentProfile,
    AgentRevision,
    ExtensionReleaseRequirement,
)
from joyhousebot.domain.capabilities import resolve_capability_policy
from joyhousebot.storage.json_codec import Jsonb


class PostgresAgentStoreMixin:
    def _apply_agent_migration(
        self,
        conn: Any,
        *,
        version: int,
        script: str,
        description: str,
    ) -> None:
        if self._migration_is_recorded(
            conn,
            name="agents",
            version=version,
            ddl=script,
            description=description,
        ):
            return
        conn.execute(script)
        self._record_migration(
            conn,
            name="agents",
            version=version,
            ddl=script,
            description=description,
        )

    def migrate_agents(self) -> None:
        """Apply the ordered Agent schema chain under one advisory transaction lock."""

        ddl = """
        CREATE TABLE IF NOT EXISTS agent_definitions (
            agent_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            current_revision_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_definitions_default
            ON agent_definitions(is_default) WHERE is_default;
        CREATE TABLE IF NOT EXISTS agent_revisions (
            revision_id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL REFERENCES agent_definitions(agent_id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            status TEXT NOT NULL,
            persona JSONB NOT NULL DEFAULT '{}'::jsonb,
            instructions TEXT NOT NULL DEFAULT '',
            model_policy JSONB NOT NULL,
            planning_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
            capability_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
            memory_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
            output_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
            monitor_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
            plugin_requirements JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            published_at TIMESTAMPTZ,
            UNIQUE(agent_id,version)
        );
        CREATE INDEX IF NOT EXISTS ix_agent_revisions_published
            ON agent_revisions(agent_id,published_at DESC) WHERE status='published';
        CREATE TABLE IF NOT EXISTS agent_skill_bindings (
            agent_revision_id TEXT NOT NULL REFERENCES agent_revisions(revision_id)
                ON DELETE CASCADE,
            skill_id TEXT NOT NULL,
            skill_version TEXT NOT NULL,
            activation_mode TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 100,
            configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY(agent_revision_id,skill_id,skill_version)
        );
        CREATE TABLE IF NOT EXISTS configuration_events (
            sequence BIGSERIAL PRIMARY KEY,
            aggregate_type TEXT NOT NULL,
            aggregate_id TEXT NOT NULL,
            revision_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_configuration_events_aggregate
            ON configuration_events(aggregate_type,aggregate_id,sequence DESC);
        CREATE TABLE IF NOT EXISTS configuration_rollouts (
            rollout_id TEXT PRIMARY KEY,
            aggregate_type TEXT NOT NULL,
            aggregate_id TEXT NOT NULL,
            revision_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_by TEXT NOT NULL,
            target_worker_count INTEGER NOT NULL DEFAULT 0,
            acknowledged_worker_count INTEGER NOT NULL DEFAULT 0,
            failed_worker_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            completed_at TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS ix_configuration_rollouts_created
            ON configuration_rollouts(created_at DESC);
        CREATE INDEX IF NOT EXISTS ix_configuration_rollouts_revision
            ON configuration_rollouts(revision_id,created_at DESC);
        CREATE TABLE IF NOT EXISTS configuration_rollout_targets (
            rollout_id TEXT NOT NULL REFERENCES configuration_rollouts(rollout_id)
                ON DELETE CASCADE,
            worker_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error JSONB,
            acknowledged_at TIMESTAMPTZ,
            PRIMARY KEY(rollout_id,worker_id)
        );
        CREATE INDEX IF NOT EXISTS ix_configuration_rollout_targets_worker
            ON configuration_rollout_targets(worker_id,status);
        CREATE TABLE IF NOT EXISTS run_execution_snapshots (
            run_id TEXT PRIMARY KEY REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            agent_id TEXT NOT NULL,
            agent_revision_id TEXT NOT NULL REFERENCES agent_revisions(revision_id),
            snapshot JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341914,))
            self._apply_agent_migration(
                conn,
                version=1,
                script=ddl,
                description="agent definitions, revisions, and execution snapshots",
            )
            governance_ddl = """
            ALTER TABLE configuration_rollouts
                ADD COLUMN IF NOT EXISTS previous_revision_id TEXT,
                ADD COLUMN IF NOT EXISTS activation_mode TEXT NOT NULL DEFAULT 'automatic',
                ADD COLUMN IF NOT EXISTS timeout_seconds INTEGER NOT NULL DEFAULT 300,
                ADD COLUMN IF NOT EXISTS deadline_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS auto_rollback BOOLEAN NOT NULL DEFAULT TRUE,
                ADD COLUMN IF NOT EXISTS retry_of_rollout_id TEXT,
                ADD COLUMN IF NOT EXISTS approved_by TEXT,
                ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS cancelled_by TEXT,
                ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS rollback_revision_id TEXT;
            ALTER TABLE configuration_rollout_targets
                ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 1;
            CREATE INDEX IF NOT EXISTS ix_configuration_rollouts_active
                ON configuration_rollouts(status,deadline_at)
                WHERE status IN ('rolling_out','awaiting_approval');
            """
            self._apply_agent_migration(
                conn,
                version=2,
                script=governance_ddl,
                description="governed rollout lifecycle, deadlines, approval, retry, and rollback",
            )
            rollback_ddl = """
            ALTER TABLE configuration_rollouts
                ADD COLUMN IF NOT EXISTS rollback_of_rollout_id TEXT;
            CREATE INDEX IF NOT EXISTS ix_configuration_rollouts_rollback_of
                ON configuration_rollouts(rollback_of_rollout_id)
                WHERE rollback_of_rollout_id IS NOT NULL;
            """
            self._apply_agent_migration(
                conn,
                version=3,
                script=rollback_ddl,
                description="rollback preheat rollout relationship",
            )
            skill_binding_ddl = """
            ALTER TABLE agent_skill_bindings
                ADD COLUMN IF NOT EXISTS skill_content_sha256 TEXT NOT NULL DEFAULT '';
            """
            self._apply_agent_migration(
                conn,
                version=4,
                script=skill_binding_ddl,
                description="pin Agent Skill bindings to immutable content digests",
            )
            extension_requirements_ddl = """
            DO $$ BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                     WHERE table_schema='public' AND table_name='agent_revisions'
                       AND column_name='plugin_requirements'
                ) AND NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                     WHERE table_schema='public' AND table_name='agent_revisions'
                       AND column_name='extension_requirements'
                ) THEN
                    ALTER TABLE agent_revisions
                        RENAME COLUMN plugin_requirements TO extension_requirements;
                ELSIF EXISTS (
                    SELECT 1 FROM information_schema.columns
                     WHERE table_schema='public' AND table_name='agent_revisions'
                       AND column_name='plugin_requirements'
                ) AND EXISTS (
                    SELECT 1 FROM information_schema.columns
                     WHERE table_schema='public' AND table_name='agent_revisions'
                       AND column_name='extension_requirements'
                ) THEN
                    UPDATE agent_revisions
                       SET extension_requirements=plugin_requirements
                     WHERE extension_requirements='[]'::jsonb
                       AND plugin_requirements<>'[]'::jsonb;
                END IF;
            END $$;
            """
            self._apply_agent_migration(
                conn,
                version=5,
                script=extension_requirements_ddl,
                description="rename Agent release requirements to Extension terminology",
            )
        self._seed_default_agents()

    def _seed_default_agents(self) -> None:
        from joyhousebot.domain.agents import default_agent_profiles

        # Defaults bootstrap a genuinely empty catalog only.  They must never
        # be re-created after an operator has published a replacement revision
        # (or intentionally pruned an old revision): doing so would silently
        # move ``current_revision_id`` back to ``*:v1`` on every process
        # restart and invalidate the operator's capability policy.
        with self._pool.connection() as conn:
            existing = conn.execute(
                "SELECT 1 FROM agent_definitions LIMIT 1"
            ).fetchone()
        if existing is not None:
            return
        for definition, revision in default_agent_profiles(self.bootstrap_model):
            self.save_agent_revision(definition, revision)

    def save_agent_revision(
        self, definition: AgentDefinition, revision: AgentRevision
    ) -> None:
        if definition.agent_id != revision.agent_id:
            raise ValueError("Agent definition/revision identity mismatch")
        with self._pool.connection() as conn, conn.transaction():
            for requirement in revision.extension_requirements:
                release = conn.execute(
                    """SELECT build_digest FROM extension_releases
                       WHERE extension_id=%s AND version=%s AND status='active'""",
                    (requirement.extension_id, requirement.version),
                ).fetchone()
                if release is None or str(release["build_digest"]) != requirement.build_digest:
                    raise ValueError(
                        "Agent revision requires an unavailable Extension release: "
                        f"{requirement.extension_id}@{requirement.version}"
                    )
            existing = conn.execute(
                "SELECT * FROM agent_revisions WHERE revision_id=%s FOR UPDATE",
                (revision.revision_id,),
            ).fetchone()
            if existing is not None and existing["status"] == "published":
                if self._agent_revision(existing).definition_dict() != revision.definition_dict():
                    raise ValueError("published Agent revisions are immutable")
                return
            if definition.is_default:
                conn.execute("UPDATE agent_definitions SET is_default=FALSE WHERE is_default")
            conn.execute(
                """INSERT INTO agent_definitions
                       (agent_id,name,description,role,status,is_default,current_revision_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(agent_id) DO UPDATE SET name=excluded.name,
                       description=excluded.description,role=excluded.role,
                       status=excluded.status,is_default=excluded.is_default,
                       updated_at=clock_timestamp()""",
                (
                    definition.agent_id,
                    definition.name,
                    definition.description,
                    definition.role,
                    definition.status,
                    definition.is_default,
                    definition.current_revision_id,
                ),
            )
            conn.execute(
                """INSERT INTO agent_revisions
                       (revision_id,agent_id,version,status,persona,instructions,model_policy,
                        planning_policy,capability_policy,memory_policy,output_policy,
                        monitor_policy,extension_requirements,created_by,
                        published_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           CASE WHEN %s='published' THEN clock_timestamp() ELSE NULL END)
                   ON CONFLICT(revision_id) DO UPDATE SET
                       persona=excluded.persona,instructions=excluded.instructions,
                       model_policy=excluded.model_policy,
                       planning_policy=excluded.planning_policy,
                       capability_policy=excluded.capability_policy,
                       memory_policy=excluded.memory_policy,output_policy=excluded.output_policy,
                       monitor_policy=excluded.monitor_policy,
                       extension_requirements=excluded.extension_requirements,
                       created_by=excluded.created_by
                   WHERE agent_revisions.status='draft'""",
                (
                    revision.revision_id,
                    revision.agent_id,
                    revision.version,
                    revision.status,
                    Jsonb(revision.persona),
                    revision.instructions,
                    Jsonb(revision.model_policy),
                    Jsonb(revision.planning_policy),
                    Jsonb(revision.capability_policy),
                    Jsonb(revision.memory_policy),
                    Jsonb(revision.output_policy),
                    Jsonb(revision.monitor_policy),
                    Jsonb([item.to_dict() for item in revision.extension_requirements]),
                    revision.created_by,
                    revision.status,
                ),
            )
            if revision.status == "published":
                conn.execute(
                    """UPDATE agent_definitions SET current_revision_id=%s,
                           updated_at=clock_timestamp() WHERE agent_id=%s""",
                    (revision.revision_id, revision.agent_id),
                )

    def publish_agent_revision(
        self,
        agent_id: str,
        revision_id: str,
        *,
        actor_id: str = "system",
        activation_mode: str = "automatic",
        timeout_seconds: int = 300,
        auto_rollback: bool = True,
        require_healthy_workers: bool = True,
    ) -> AgentProfile:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT status FROM agent_revisions WHERE revision_id=%s AND agent_id=%s
                   FOR UPDATE""",
                (revision_id, agent_id),
            ).fetchone()
            if row is None or row["status"] == "retired":
                raise ValueError("Agent revision not found or not publishable")
            conn.execute(
                """UPDATE agent_revisions SET status='published',
                       published_at=COALESCE(published_at,clock_timestamp())
                   WHERE revision_id=%s AND agent_id=%s""",
                (revision_id, agent_id),
            )
            conn.execute(
                """INSERT INTO configuration_events
                       (aggregate_type,aggregate_id,revision_id,event_type,actor_id)
                   VALUES ('agent',%s,%s,'publish.requested',%s)""",
                (agent_id, revision_id, actor_id),
            )
            self._create_configuration_rollout(
                conn,
                aggregate_type="agent",
                aggregate_id=agent_id,
                revision_id=revision_id,
                actor_id=actor_id,
                activation_mode=activation_mode,
                timeout_seconds=timeout_seconds,
                auto_rollback=auto_rollback,
                require_healthy_workers=require_healthy_workers,
            )
            self._notify(conn, f"config:agent:{agent_id}")
        definition = self.get_agent_definition(agent_id)
        revision = self.get_agent_revision(revision_id)
        assert definition is not None and revision is not None
        return AgentProfile(definition=definition, revision=revision)

    def get_agent_revision(self, revision_id: str) -> AgentRevision | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM agent_revisions WHERE revision_id=%s", (revision_id,)
            ).fetchone()
        return self._agent_revision(row) if row else None

    def get_agent_definition(self, agent_id: str) -> AgentDefinition | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM agent_definitions WHERE agent_id=%s", (agent_id,)
            ).fetchone()
        return self._agent_definition(row) if row else None

    def list_agent_definitions(self, *, active_only: bool = False) -> list[AgentDefinition]:
        where = "WHERE status='active'" if active_only else ""
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM agent_definitions {where} ORDER BY is_default DESC,agent_id"
            ).fetchall()
        return [self._agent_definition(row) for row in rows]

    def list_agent_revisions(self, agent_id: str) -> list[AgentRevision]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM agent_revisions WHERE agent_id=%s
                   ORDER BY version DESC""",
                (agent_id,),
            ).fetchall()
        return [self._agent_revision(row) for row in rows]

    def get_agent_profile(self, agent_id: str | None = None) -> AgentProfile | None:
        with self._pool.connection() as conn:
            if not agent_id or agent_id == "default":
                definition = conn.execute(
                    """SELECT * FROM agent_definitions WHERE status='active'
                       ORDER BY is_default DESC,created_at LIMIT 1"""
                ).fetchone()
            else:
                definition = conn.execute(
                    "SELECT * FROM agent_definitions WHERE agent_id=%s", (agent_id,)
                ).fetchone()
            if definition is None or not definition["current_revision_id"]:
                return None
            revision = conn.execute(
                """SELECT * FROM agent_revisions WHERE revision_id=%s
                   AND status='published'""",
                (definition["current_revision_id"],),
            ).fetchone()
        return self._agent_profile(definition, revision) if revision else None

    def list_agent_profiles(self, *, active_only: bool = True) -> list[AgentProfile]:
        where = "WHERE status='active'" if active_only else ""
        with self._pool.connection() as conn:
            definitions = conn.execute(
                f"SELECT * FROM agent_definitions {where} ORDER BY is_default DESC,agent_id"
            ).fetchall()
            profiles = []
            for definition in definitions:
                revision = conn.execute(
                    "SELECT * FROM agent_revisions WHERE revision_id=%s",
                    (definition["current_revision_id"],),
                ).fetchone()
                if revision is not None:
                    profiles.append(self._agent_profile(definition, revision))
        return profiles

    def list_published_agent_profiles(self) -> list[AgentProfile]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT d.agent_id AS d_agent_id,d.name,d.description,d.role,
                          d.status AS d_status,d.is_default,d.current_revision_id,
                          d.created_at AS d_created_at,d.updated_at AS d_updated_at,r.*
                   FROM agent_definitions d JOIN agent_revisions r
                     ON r.agent_id=d.agent_id
                   WHERE r.status='published' ORDER BY d.agent_id,r.version"""
            ).fetchall()
        profiles = []
        for row in rows:
            definition = {
                "agent_id": row["d_agent_id"],
                "name": row["name"],
                "description": row["description"],
                "role": row["role"],
                "status": row["d_status"],
                "is_default": row["is_default"],
                "current_revision_id": row["current_revision_id"],
                "created_at": row["d_created_at"],
                "updated_at": row["d_updated_at"],
            }
            profiles.append(self._agent_profile(definition, row))
        return profiles

    def create_run_execution_snapshot(
        self, run_id: str, agent_id: str, *, revision_id: str | None = None
    ) -> AgentExecutionSnapshot:
        existing = self.get_run_execution_snapshot(run_id)
        if existing is not None:
            return existing
        if revision_id:
            revision = self.get_agent_revision(revision_id)
            if revision is None or revision.agent_id != agent_id:
                raise ValueError("pinned Agent revision does not match agent_id")
            if revision.status not in {"draft", "published", "retired"}:
                raise ValueError("pinned Agent revision is not executable")
            resolved_agent_id = revision.agent_id
        else:
            profile = self.get_agent_profile(agent_id)
            if profile is None:
                raise ValueError(f"active published Agent not found: {agent_id}")
            revision = profile.revision
            resolved_agent_id = profile.definition.agent_id
        bindings = tuple(self.list_agent_skill_bindings(revision.revision_id))
        prompt_bindings = tuple(
            self.list_active_prompt_bindings(
                target_type="agent",
                target_id=resolved_agent_id,
                target_revision_id=revision.revision_id,
            )
        )
        capability_policy = resolve_capability_policy(
            revision.capability_policy,
            self.list_capability_definitions(),
        )
        value = {
            "run_id": run_id,
            "agent_id": resolved_agent_id,
            "agent_revision_id": revision.revision_id,
            "model_policy": revision.model_policy,
            "planning_policy": revision.planning_policy,
            "capability_policy": capability_policy,
            "memory_policy": revision.memory_policy,
            "output_policy": revision.output_policy,
            "monitor_policy": revision.monitor_policy,
            "extension_requirements": [
                item.to_dict() for item in revision.extension_requirements
            ],
            "skill_bindings": list(bindings),
            "prompt_bindings": list(prompt_bindings),
        }
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO run_execution_snapshots
                       (run_id,agent_id,agent_revision_id,snapshot)
                   VALUES (%s,%s,%s,%s) ON CONFLICT(run_id) DO NOTHING
                   RETURNING created_at""",
                (
                    run_id,
                    value["agent_id"],
                    value["agent_revision_id"],
                    Jsonb(value),
                ),
            ).fetchone()
            if row is not None:
                from joyhousebot.storage.postgres_store import _iso

                value["created_at"] = _iso(row["created_at"])
        return self.get_run_execution_snapshot(run_id) or self._execution_snapshot(value)

    def get_run_execution_snapshot(self, run_id: str) -> AgentExecutionSnapshot | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT snapshot,created_at FROM run_execution_snapshots
                   WHERE run_id=%s""",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        from joyhousebot.storage.postgres_store import _iso

        value = dict(row["snapshot"])
        value["created_at"] = _iso(row["created_at"])
        return self._execution_snapshot(value)

    @staticmethod
    def _execution_snapshot(value: dict[str, Any]) -> AgentExecutionSnapshot:
        return AgentExecutionSnapshot(
            run_id=value["run_id"],
            agent_id=value["agent_id"],
            agent_revision_id=value["agent_revision_id"],
            model_policy=dict(value["model_policy"]),
            planning_policy=dict(value["planning_policy"]),
            capability_policy=dict(value["capability_policy"]),
            memory_policy=dict(value["memory_policy"]),
            output_policy=dict(value["output_policy"]),
            monitor_policy=dict(value.get("monitor_policy") or {}),
            extension_requirements=tuple(
                ExtensionReleaseRequirement.from_dict(dict(item))
                for item in value.get("extension_requirements") or ()
            ),
            skill_bindings=tuple(value.get("skill_bindings") or ()),
            prompt_bindings=tuple(value.get("prompt_bindings") or ()),
            created_at=value.get("created_at"),
        )

    @staticmethod
    def _agent_revision(row: Any) -> AgentRevision:
        from joyhousebot.storage.postgres_store import _iso

        return AgentRevision.from_dict(
            {
                "revision_id": row["revision_id"],
                "agent_id": row["agent_id"],
                "version": row["version"],
                "status": row["status"],
                "persona": dict(row["persona"]),
                "instructions": row["instructions"],
                "model_policy": dict(row["model_policy"]),
                "planning_policy": dict(row["planning_policy"]),
                "capability_policy": dict(row["capability_policy"]),
                "memory_policy": dict(row["memory_policy"]),
                "output_policy": dict(row["output_policy"]),
                "monitor_policy": dict(row["monitor_policy"] or {}),
                "extension_requirements": list(row["extension_requirements"] or ()),
                "created_by": row["created_by"],
                "created_at": _iso(row["created_at"]),
                "published_at": _iso(row["published_at"]),
            }
        )

    @classmethod
    def _agent_profile(cls, definition: Any, revision: Any) -> AgentProfile:
        return AgentProfile(
            definition=cls._agent_definition(definition),
            revision=cls._agent_revision(revision),
        )

    @staticmethod
    def _agent_definition(definition: Any) -> AgentDefinition:
        from joyhousebot.storage.postgres_store import _iso

        return AgentDefinition(
            agent_id=str(definition["agent_id"]),
            name=str(definition["name"]),
            description=str(definition["description"] or ""),
            role=str(definition["role"]),
            status=str(definition["status"]),
            is_default=bool(definition["is_default"]),
            current_revision_id=definition["current_revision_id"],
            created_at=_iso(definition["created_at"]),
            updated_at=_iso(definition["updated_at"]),
        )
