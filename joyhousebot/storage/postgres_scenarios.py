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
            # This product has not been released.  A legacy table stored only
            # capability ids, which is incapable of replaying an approved
            # scenario after a plugin release changes.  Replace it rather
            # than carrying an ambiguous compatibility path indefinitely.
            legacy = conn.execute(
                """SELECT 1 FROM information_schema.tables t
                   WHERE t.table_schema=current_schema() AND t.table_name='scenario_capabilities'
                     AND NOT EXISTS (
                        SELECT 1 FROM information_schema.columns c
                        WHERE c.table_schema=current_schema()
                          AND c.table_name='scenario_capabilities'
                          AND c.column_name='capability_version'
                     )"""
            ).fetchone()
            if legacy:
                conn.execute("DROP TABLE scenario_capabilities")
            conn.execute(ddl)

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
                "scenario_clarification_edges", "scenario_capabilities",
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
            conn.execute(
                """INSERT INTO configuration_events
                       (aggregate_type,aggregate_id,revision_id,event_type,actor_id)
                   VALUES ('scenario',%s,%s,'draft.saved',%s)""",
                (scenario.scenario_id, str(scenario.version), actor_id),
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
            self._notify(conn, f"config:scenario:{scenario_id}")

    def get_scenario_version(
        self, scenario_id: str, version: int | None = None
    ) -> ScenarioVersion | None:
        with self._pool.connection() as conn:
            if version is None:
                row = conn.execute(
                    """SELECT version FROM scenario_versions WHERE scenario_id=%s
                       AND status='published' ORDER BY published_at DESC,version DESC LIMIT 1""",
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
        where = "WHERE status='published'" if published_only else ""
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT scenario_id,version FROM scenario_versions {where} "
                "ORDER BY scenario_id,version DESC"
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
            "planning_mode": base["planning_mode"],
            "execution_policy": dict(base["execution_policy"]),
            "routing_rules": list(base["routing_rules"]),
            "status": base["status"],
            "published_at": base["published_at"],
        })
