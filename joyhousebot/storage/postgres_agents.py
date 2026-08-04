"""PostgreSQL persistence for immutable platform Agent revisions."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from joyhousebot.domain.agents import (
    AgentDefinition,
    AgentExecutionSnapshot,
    AgentProfile,
    AgentRevision,
)


class PostgresAgentStoreMixin:
    def migrate_agents(self) -> None:
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
            conn.execute(ddl)
            # Pre-release data correction: built-in revisions are seeded
            # defaults, so migrate their previous Claude values in place.
            # User-created revisions remain immutable and untouched.
            conn.execute(
                """UPDATE agent_revisions
                   SET model_policy = model_policy || jsonb_build_object(
                       'primary', 'openrouter/deepseek/deepseek-v4-flash',
                       'max_tokens', 4096,
                       'reasoning_effort', 'none',
                       'thinking_budget_tokens', 0,
                       'capture_reasoning', false
                   )
                   WHERE revision_id IN ('joy:v1','main-coordinator:v1')
                     AND model_policy->>'primary' IN (
                         'anthropic/claude-opus-4-5', 'anthropic/claude-opus-4.5'
                     )"""
            )
        self._seed_default_agents()

    def _seed_default_agents(self) -> None:
        from joyhousebot.bootstrap.default_agents import default_agent_profiles

        for definition, revision in default_agent_profiles():
            if self.get_agent_revision(revision.revision_id) is None:
                self.save_agent_revision(definition, revision)

    def save_agent_revision(
        self, definition: AgentDefinition, revision: AgentRevision
    ) -> None:
        if definition.agent_id != revision.agent_id:
            raise ValueError("Agent definition/revision identity mismatch")
        with self._pool.connection() as conn, conn.transaction():
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
                        planning_policy,capability_policy,memory_policy,output_policy,created_by,
                        published_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           CASE WHEN %s='published' THEN clock_timestamp() ELSE NULL END)
                   ON CONFLICT(revision_id) DO UPDATE SET
                       persona=excluded.persona,instructions=excluded.instructions,
                       model_policy=excluded.model_policy,
                       planning_policy=excluded.planning_policy,
                       capability_policy=excluded.capability_policy,
                       memory_policy=excluded.memory_policy,output_policy=excluded.output_policy,
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
        self, agent_id: str, revision_id: str, *, actor_id: str = "system"
    ) -> AgentProfile:
        rollout_id = f"rollout_{uuid4().hex}"
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
            targets = conn.execute(
                """SELECT worker_id FROM runtime_workers
                   WHERE status='online'
                     AND last_heartbeat > clock_timestamp()-interval '2 minutes'
                     AND capabilities @> '{"agent": true}'::jsonb
                   ORDER BY worker_id"""
            ).fetchall()
            target_count = len(targets)
            conn.execute(
                """INSERT INTO configuration_rollouts
                       (rollout_id,aggregate_type,aggregate_id,revision_id,status,
                        created_by,target_worker_count,completed_at)
                   VALUES (%s,'agent',%s,%s,%s,%s,%s,
                           CASE WHEN %s=0 THEN clock_timestamp() ELSE NULL END)""",
                (
                    rollout_id,
                    agent_id,
                    revision_id,
                    "completed" if target_count == 0 else "rolling_out",
                    actor_id,
                    target_count,
                    target_count,
                ),
            )
            if targets:
                with conn.cursor() as cursor:
                    cursor.executemany(
                        """INSERT INTO configuration_rollout_targets(rollout_id,worker_id)
                           VALUES (%s,%s)""",
                        [(rollout_id, row["worker_id"]) for row in targets],
                    )
            else:
                conn.execute(
                    """UPDATE agent_definitions SET current_revision_id=%s,
                           updated_at=clock_timestamp() WHERE agent_id=%s""",
                    (revision_id, agent_id),
                )
                conn.execute(
                    """INSERT INTO configuration_events
                           (aggregate_type,aggregate_id,revision_id,event_type,actor_id)
                       VALUES ('agent',%s,%s,'activated',%s)""",
                    (agent_id, revision_id, actor_id),
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
        self, run_id: str, agent_id: str
    ) -> AgentExecutionSnapshot:
        existing = self.get_run_execution_snapshot(run_id)
        if existing is not None:
            return existing
        profile = self.get_agent_profile(agent_id)
        if profile is None:
            raise ValueError(f"active published Agent not found: {agent_id}")
        bindings = tuple(self.list_agent_skill_bindings(profile.revision.revision_id))
        value = {
            "run_id": run_id,
            "agent_id": profile.definition.agent_id,
            "agent_revision_id": profile.revision.revision_id,
            "model_policy": profile.revision.model_policy,
            "planning_policy": profile.revision.planning_policy,
            "capability_policy": profile.revision.capability_policy,
            "memory_policy": profile.revision.memory_policy,
            "output_policy": profile.revision.output_policy,
            "skill_bindings": list(bindings),
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
            skill_bindings=tuple(value.get("skill_bindings") or ()),
            created_at=value.get("created_at"),
        )

    def bind_agent_skill(
        self,
        *,
        agent_revision_id: str,
        skill_id: str,
        skill_version: str,
        activation_mode: str = "coordinator_selected",
        priority: int = 100,
        configuration: dict[str, Any] | None = None,
    ) -> None:
        if activation_mode not in {"always", "coordinator_selected", "scenario_required"}:
            raise ValueError("invalid Skill activation mode")
        with self._pool.connection() as conn, conn.transaction():
            revision = conn.execute(
                "SELECT status FROM agent_revisions WHERE revision_id=%s", (agent_revision_id,)
            ).fetchone()
            capability = conn.execute(
                """SELECT 1 FROM capability_versions v JOIN capability_definitions d
                     ON d.capability_id=v.capability_id
                   WHERE v.capability_id=%s AND v.version=%s AND v.status='published'
                     AND d.kind='skill'""",
                (skill_id, skill_version),
            ).fetchone()
            if revision is None or capability is None:
                raise ValueError("Agent revision or published Skill version not found")
            if revision["status"] != "draft":
                raise ValueError("Skill bindings can only modify draft Agent revisions")
            conn.execute(
                """INSERT INTO agent_skill_bindings
                       (agent_revision_id,skill_id,skill_version,activation_mode,priority,
                        configuration) VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(agent_revision_id,skill_id,skill_version) DO UPDATE SET
                       activation_mode=excluded.activation_mode,priority=excluded.priority,
                       configuration=excluded.configuration""",
                (
                    agent_revision_id,
                    skill_id,
                    skill_version,
                    activation_mode,
                    priority,
                    Jsonb(configuration or {}),
                ),
            )

    def list_agent_skill_bindings(self, agent_revision_id: str) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM agent_skill_bindings WHERE agent_revision_id=%s
                   ORDER BY priority,skill_id""",
                (agent_revision_id,),
            ).fetchall()
        return [
            {
                "agent_revision_id": row["agent_revision_id"],
                "skill_id": row["skill_id"],
                "skill_version": row["skill_version"],
                "activation_mode": row["activation_mode"],
                "priority": int(row["priority"]),
                "configuration": dict(row["configuration"]),
            }
            for row in rows
        ]

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
