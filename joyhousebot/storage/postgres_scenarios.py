"""PostgreSQL persistence for versioned business scenarios."""

from __future__ import annotations

from typing import Any

from joyhousebot.domain.scenarios import ScenarioVersion
from joyhousebot.domain.scenarios.models import ScenarioVersion as ScenarioModel
from joyhousebot.storage.json_codec import Jsonb


class PostgresScenarioStoreMixin:
    def migrate_scenarios(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS scenario_definitions (
            scenario_id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS scenario_versions (
            scenario_id TEXT NOT NULL REFERENCES scenario_definitions(scenario_id)
                ON DELETE CASCADE,
            version INTEGER NOT NULL,status TEXT NOT NULL,planning_mode TEXT NOT NULL,
            execution_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
            routing_rules JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),published_at TIMESTAMPTZ,
            PRIMARY KEY(scenario_id,version)
        );
        CREATE TABLE IF NOT EXISTS scenario_fields (
            scenario_id TEXT NOT NULL,version INTEGER NOT NULL,position INTEGER NOT NULL,
            field_name TEXT NOT NULL,definition JSONB NOT NULL,
            PRIMARY KEY(scenario_id,version,field_name),
            FOREIGN KEY(scenario_id,version) REFERENCES scenario_versions(scenario_id,version)
                ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS scenario_clarification_nodes (
            scenario_id TEXT NOT NULL,version INTEGER NOT NULL,node_id TEXT NOT NULL,
            kind TEXT NOT NULL,question TEXT NOT NULL,field_names JSONB NOT NULL,
            configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY(scenario_id,version,node_id),
            FOREIGN KEY(scenario_id,version) REFERENCES scenario_versions(scenario_id,version)
                ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS scenario_clarification_edges (
            scenario_id TEXT NOT NULL,version INTEGER NOT NULL,
            source_node_id TEXT NOT NULL,target_node_id TEXT NOT NULL,
            condition_expr TEXT NOT NULL,priority INTEGER NOT NULL,
            PRIMARY KEY(scenario_id,version,source_node_id,target_node_id,priority),
            FOREIGN KEY(scenario_id,version) REFERENCES scenario_versions(scenario_id,version)
                ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS scenario_capabilities (
            scenario_id TEXT NOT NULL,version INTEGER NOT NULL,
            capability_id TEXT NOT NULL,capability_version TEXT NOT NULL,
            capability_kind TEXT NOT NULL,plugin_id TEXT NOT NULL,
            plugin_version TEXT NOT NULL,plugin_build_digest TEXT NOT NULL,
            position INTEGER NOT NULL,
            PRIMARY KEY(scenario_id,version,capability_id,capability_version,
                plugin_id,plugin_version),
            FOREIGN KEY(scenario_id,version) REFERENCES scenario_versions(scenario_id,version)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS ix_scenario_versions_published
            ON scenario_versions(scenario_id,published_at DESC) WHERE status='published';
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341909,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="scenarios",
                version=1,
                ddl=ddl,
                description="scenario definitions, versions, and capability bindings",
            )
            governance_ddl = """
            ALTER TABLE scenario_definitions
                ADD COLUMN IF NOT EXISTS current_version INTEGER;
            UPDATE scenario_definitions d SET current_version = selected.version
            FROM (
                SELECT DISTINCT ON (scenario_id) scenario_id,version
                FROM scenario_versions WHERE status='published'
                ORDER BY scenario_id,published_at DESC NULLS LAST,version DESC
            ) selected
            WHERE selected.scenario_id=d.scenario_id AND d.current_version IS NULL;
            """
            conn.execute(governance_ddl)
            self._record_migration(
                conn,
                name="scenarios",
                version=2,
                ddl=governance_ddl,
                description="explicit active scenario version for staged rollout and rollback",
            )
            skills_ddl = """
            CREATE TABLE IF NOT EXISTS scenario_skills (
                scenario_id TEXT NOT NULL,version INTEGER NOT NULL,
                skill_id TEXT NOT NULL,skill_version TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,position INTEGER NOT NULL,
                PRIMARY KEY(scenario_id,version,skill_id,skill_version,content_sha256),
                FOREIGN KEY(scenario_id,version)
                    REFERENCES scenario_versions(scenario_id,version) ON DELETE CASCADE
            );
            """
            conn.execute(skills_ddl)
            self._record_migration(
                conn,
                name="scenarios",
                version=3,
                ddl=skills_ddl,
                description="separate immutable Skill references from executable capabilities",
            )

    def save_scenario_version(
        self, scenario: ScenarioVersion, *, status: str = "draft", actor_id: str = "system"
    ) -> None:
        if status not in {"draft", "published", "retired"}:
            raise ValueError("invalid scenario version status")
        with self._pool.connection() as conn, conn.transaction():
            existing = conn.execute(
                "SELECT status FROM scenario_versions WHERE scenario_id=%s AND version=%s",
                (scenario.scenario_id, scenario.version),
            ).fetchone()
            if existing and existing["status"] == "published":
                current = self._load_scenario(conn, scenario.scenario_id, scenario.version)
                if current.definition_dict() != scenario.definition_dict():
                    raise ValueError("published scenario versions are immutable")
                return
            conn.execute(
                """INSERT INTO scenario_definitions(scenario_id,name,description)
                   VALUES (%s,%s,%s) ON CONFLICT(scenario_id) DO UPDATE SET
                       name=excluded.name,description=excluded.description,
                       updated_at=clock_timestamp()""",
                (scenario.scenario_id, scenario.name, scenario.description),
            )
            conn.execute(
                """INSERT INTO scenario_versions
                       (scenario_id,version,status,planning_mode,execution_policy,
                        routing_rules,published_at)
                   VALUES (%s,%s,%s,%s,%s,%s,CASE WHEN %s='published'
                       THEN clock_timestamp() ELSE NULL END)
                   ON CONFLICT(scenario_id,version) DO UPDATE SET
                       status=excluded.status,planning_mode=excluded.planning_mode,
                       execution_policy=excluded.execution_policy,
                       routing_rules=excluded.routing_rules""",
                (scenario.scenario_id, scenario.version, status, scenario.planning_mode,
                 Jsonb(scenario.execution_policy), Jsonb(list(scenario.routing_rules)), status),
            )
            for table in (
                "scenario_fields", "scenario_clarification_nodes",
                "scenario_clarification_edges", "scenario_capabilities", "scenario_skills",
            ):
                conn.execute(
                    f"DELETE FROM {table} WHERE scenario_id=%s AND version=%s",
                    (scenario.scenario_id, scenario.version),
                )
            with conn.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO scenario_fields
                           (scenario_id,version,position,field_name,definition)
                       VALUES (%s,%s,%s,%s,%s)""",
                    [(scenario.scenario_id, scenario.version, index, item.name,
                      Jsonb(item.to_dict())) for index, item in enumerate(scenario.fields)],
                )
                cursor.executemany(
                    """INSERT INTO scenario_clarification_nodes
                           (scenario_id,version,node_id,kind,question,field_names,configuration)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    [(scenario.scenario_id, scenario.version, item.node_id, item.kind,
                      item.question, Jsonb(list(item.field_names)), Jsonb(item.configuration))
                     for item in scenario.nodes],
                )
                cursor.executemany(
                    """INSERT INTO scenario_clarification_edges
                           (scenario_id,version,source_node_id,target_node_id,
                            condition_expr,priority) VALUES (%s,%s,%s,%s,%s,%s)""",
                    [(scenario.scenario_id, scenario.version, item.source_node_id,
                      item.target_node_id, item.condition, item.priority)
                     for item in scenario.edges],
                )
                cursor.executemany(
                    """INSERT INTO scenario_capabilities
                           (scenario_id,version,capability_id,capability_version,
                            capability_kind,plugin_id,plugin_version,plugin_build_digest,position)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [(scenario.scenario_id, scenario.version, item.capability_id,
                      item.version, item.kind.value, item.plugin_id,
                      item.plugin_version, item.plugin_build_digest, index)
                     for index, item in enumerate(scenario.allowed_capabilities)],
                )
                cursor.executemany(
                    """INSERT INTO scenario_skills
                           (scenario_id,version,skill_id,skill_version,
                            content_sha256,position)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    [
                        (
                            scenario.scenario_id,
                            scenario.version,
                            item.skill_id,
                            item.version,
                            item.content_sha256,
                            index,
                        )
                        for index, item in enumerate(scenario.required_skills)
                    ],
                )
            conn.execute(
                """INSERT INTO configuration_events
                       (aggregate_type,aggregate_id,revision_id,event_type,actor_id)
                   VALUES ('scenario',%s,%s,'draft.saved',%s)""",
                (scenario.scenario_id, str(scenario.version), actor_id),
            )
            if status == "published":
                conn.execute(
                    """UPDATE scenario_definitions SET current_version=%s,
                           updated_at=clock_timestamp() WHERE scenario_id=%s""",
                    (scenario.version, scenario.scenario_id),
                )

    def publish_scenario(
        self, scenario_id: str, version: int, *, actor_id: str = "system"
    ) -> None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE scenario_versions SET status='published',
                       published_at=clock_timestamp() WHERE scenario_id=%s AND version=%s
                       AND status='draft' RETURNING scenario_id""",
                (scenario_id, version),
            ).fetchone()
            if row is None:
                current = conn.execute(
                    "SELECT status FROM scenario_versions WHERE scenario_id=%s AND version=%s",
                    (scenario_id, version),
                ).fetchone()
                if current is None or current["status"] != "published":
                    raise ValueError("scenario version not found or not publishable")
            conn.execute(
                """INSERT INTO configuration_events
                       (aggregate_type,aggregate_id,revision_id,event_type,actor_id)
                   VALUES ('scenario',%s,%s,'published',%s)""",
                (scenario_id, str(version), actor_id),
            )
            conn.execute(
                """UPDATE scenario_definitions SET current_version=%s,
                       updated_at=clock_timestamp() WHERE scenario_id=%s""",
                (version, scenario_id),
            )
            self._notify(conn, f"config:scenario:{scenario_id}")

    def stage_scenario_release(
        self,
        scenario_id: str,
        version: int,
        *,
        actor_id: str = "system",
        activation_mode: str = "automatic",
        timeout_seconds: int = 300,
        auto_rollback: bool = True,
        require_healthy_workers: bool = True,
    ) -> None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT status FROM scenario_versions
                   WHERE scenario_id=%s AND version=%s FOR UPDATE""",
                (scenario_id, version),
            ).fetchone()
            if row is None or row["status"] not in {"draft", "published"}:
                raise ValueError("scenario version not found or not publishable")
            conn.execute(
                """INSERT INTO configuration_events
                       (aggregate_type,aggregate_id,revision_id,event_type,actor_id)
                   VALUES ('scenario',%s,%s,'publish.requested',%s)""",
                (scenario_id, str(version), actor_id),
            )
            self._create_configuration_rollout(
                conn,
                aggregate_type="scenario",
                aggregate_id=scenario_id,
                revision_id=str(version),
                actor_id=actor_id,
                activation_mode=activation_mode,
                timeout_seconds=timeout_seconds,
                auto_rollback=auto_rollback,
                require_healthy_workers=require_healthy_workers,
            )
            self._notify(conn, f"config:scenario:{scenario_id}")

    def get_scenario_version(
        self, scenario_id: str, version: int | None = None
    ) -> ScenarioVersion | None:
        with self._pool.connection() as conn:
            if version is None:
                row = conn.execute(
                    """SELECT current_version AS version FROM scenario_definitions
                       WHERE scenario_id=%s AND current_version IS NOT NULL""",
                    (scenario_id,),
                ).fetchone()
                if row is None:
                    return None
                version = int(row["version"])
            exists = conn.execute(
                "SELECT 1 AS present FROM scenario_versions WHERE scenario_id=%s AND version=%s",
                (scenario_id, version),
            ).fetchone()
            return self._load_scenario(conn, scenario_id, version) if exists else None

    def list_scenario_versions(self, *, published_only: bool = True) -> list[ScenarioVersion]:
        with self._pool.connection() as conn:
            if published_only:
                rows = conn.execute(
                    """SELECT scenario_id,current_version AS version
                       FROM scenario_definitions WHERE current_version IS NOT NULL
                       ORDER BY scenario_id"""
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT scenario_id,version FROM scenario_versions
                       ORDER BY scenario_id,version DESC"""
                ).fetchall()
            return [self._load_scenario(conn, str(row["scenario_id"]), int(row["version"]))
                    for row in rows]

    @staticmethod
    def _load_scenario(conn: Any, scenario_id: str, version: int) -> ScenarioVersion:
        base = conn.execute(
            """SELECT d.name,d.description,v.* FROM scenario_versions v
               JOIN scenario_definitions d ON d.scenario_id=v.scenario_id
               WHERE v.scenario_id=%s AND v.version=%s""",
            (scenario_id, version),
        ).fetchone()
        fields = conn.execute(
            "SELECT definition FROM scenario_fields WHERE scenario_id=%s AND version=%s ORDER BY position",
            (scenario_id, version),
        ).fetchall()
        nodes = conn.execute(
            """SELECT node_id,kind,question,field_names,configuration
               FROM scenario_clarification_nodes WHERE scenario_id=%s AND version=%s
               ORDER BY node_id""", (scenario_id, version),
        ).fetchall()
        edges = conn.execute(
            """SELECT source_node_id,target_node_id,condition_expr,priority
               FROM scenario_clarification_edges WHERE scenario_id=%s AND version=%s
               ORDER BY priority""", (scenario_id, version),
        ).fetchall()
        capabilities = conn.execute(
            """SELECT capability_id,capability_version,capability_kind,plugin_id,
                      plugin_version,plugin_build_digest FROM scenario_capabilities
               WHERE scenario_id=%s AND version=%s ORDER BY position""",
            (scenario_id, version),
        ).fetchall()
        skills = conn.execute(
            """SELECT skill_id,skill_version,content_sha256 FROM scenario_skills
               WHERE scenario_id=%s AND version=%s ORDER BY position""",
            (scenario_id, version),
        ).fetchall()
        return ScenarioModel.from_dict({
            "scenario_id": scenario_id, "version": version, "name": base["name"],
            "description": base["description"],
            "fields": [dict(row["definition"]) for row in fields],
            "nodes": [{"node_id": row["node_id"], "kind": row["kind"],
                       "question": row["question"], "field_names": list(row["field_names"]),
                       "configuration": dict(row["configuration"])} for row in nodes],
            "edges": [{"source_node_id": row["source_node_id"],
                       "target_node_id": row["target_node_id"],
                       "condition": row["condition_expr"], "priority": row["priority"]}
                      for row in edges],
            "allowed_capabilities": [
                {
                    "capability_id": row["capability_id"],
                    "version": row["capability_version"],
                    "kind": row["capability_kind"],
                    "plugin_id": row["plugin_id"],
                    "plugin_version": row["plugin_version"],
                    "plugin_build_digest": row["plugin_build_digest"],
                }
                for row in capabilities
            ],
            "required_skills": [
                {
                    "skill_id": row["skill_id"],
                    "version": row["skill_version"],
                    "content_sha256": row["content_sha256"],
                }
                for row in skills
            ],
            "planning_mode": base["planning_mode"],
            "execution_policy": dict(base["execution_policy"]),
            "routing_rules": list(base["routing_rules"]),
            "status": base["status"],
            "published_at": base["published_at"],
        })
