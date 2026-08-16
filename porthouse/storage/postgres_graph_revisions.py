"""Append-only PostgreSQL persistence for immutable Graph revisions."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from porthouse.storage.graph_revision_records import (
    GraphEdgeRecord,
    GraphNodeRecord,
    GraphRevisionRecord,
)
from porthouse.storage.json_codec import Jsonb


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return sha256(encoded).hexdigest()


class PostgresGraphRevisionStoreMixin:
    def migrate_graph_revisions(self) -> None:
        ddl = """
        ALTER TABLE runtime_runs ADD COLUMN IF NOT EXISTS graph_revision_id TEXT;

        CREATE TABLE IF NOT EXISTS graph_revisions (
            revision_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            revision_number INTEGER NOT NULL,
            parent_revision_id TEXT REFERENCES graph_revisions(revision_id),
            source TEXT NOT NULL,
            spec_hash TEXT NOT NULL,
            settings JSONB NOT NULL,
            status TEXT NOT NULL DEFAULT 'frozen',
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(run_id, revision_number),
            UNIQUE(run_id, spec_hash)
        );
        CREATE INDEX IF NOT EXISTS ix_graph_revisions_owner_run
            ON graph_revisions(user_id, run_id, revision_number DESC);

        CREATE TABLE IF NOT EXISTS graph_revision_nodes (
            revision_id TEXT NOT NULL REFERENCES graph_revisions(revision_id) ON DELETE CASCADE,
            node_id TEXT NOT NULL,
            node_type TEXT NOT NULL,
            position INTEGER NOT NULL,
            definition JSONB NOT NULL,
            definition_hash TEXT NOT NULL,
            PRIMARY KEY(revision_id, node_id)
        );
        CREATE TABLE IF NOT EXISTS graph_revision_edges (
            revision_id TEXT NOT NULL REFERENCES graph_revisions(revision_id) ON DELETE CASCADE,
            edge_id TEXT NOT NULL,
            source_node_id TEXT NOT NULL,
            target_node_id TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            condition JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY(revision_id, edge_id),
            FOREIGN KEY(revision_id, source_node_id)
                REFERENCES graph_revision_nodes(revision_id, node_id) ON DELETE CASCADE,
            FOREIGN KEY(revision_id, target_node_id)
                REFERENCES graph_revision_nodes(revision_id, node_id) ON DELETE CASCADE
        );

        CREATE OR REPLACE FUNCTION reject_graph_revision_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Graph revisions are immutable';
        END;
        $$;
        DROP TRIGGER IF EXISTS trg_graph_revisions_immutable ON graph_revisions;
        CREATE TRIGGER trg_graph_revisions_immutable BEFORE UPDATE ON graph_revisions
            FOR EACH ROW EXECUTE FUNCTION reject_graph_revision_mutation();
        DROP TRIGGER IF EXISTS trg_graph_revision_nodes_immutable ON graph_revision_nodes;
        CREATE TRIGGER trg_graph_revision_nodes_immutable BEFORE UPDATE ON graph_revision_nodes
            FOR EACH ROW EXECUTE FUNCTION reject_graph_revision_mutation();
        DROP TRIGGER IF EXISTS trg_graph_revision_edges_immutable ON graph_revision_edges;
        CREATE TRIGGER trg_graph_revision_edges_immutable BEFORE UPDATE ON graph_revision_edges
            FOR EACH ROW EXECUTE FUNCTION reject_graph_revision_mutation();
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341929,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="graph_revisions",
                version=1,
                ddl=ddl,
                description="immutable versioned Graph node and edge snapshots",
            )

    def _insert_graph_revision(
        self,
        conn: Any,
        *,
        run_id: str,
        user_id: str,
        revision: dict[str, Any],
        created_by: str,
    ) -> str:
        revision_id = str(revision["revision_id"])
        snapshot = {
            "schema_version": int(revision.get("schema_version") or 1),
            "revision_number": int(revision["revision_number"]),
            "settings": revision["settings"],
            "nodes": revision["nodes"],
            "edges": revision["edges"],
        }
        if _hash(snapshot) != str(revision["spec_hash"]):
            raise RuntimeError(f"Graph revision spec hash mismatch: {revision_id}")
        row = conn.execute(
            """INSERT INTO graph_revisions
                   (revision_id,run_id,user_id,revision_number,parent_revision_id,
                    source,spec_hash,settings,created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(revision_id) DO NOTHING RETURNING *""",
            (
                revision_id,
                run_id,
                user_id,
                int(revision["revision_number"]),
                revision.get("parent_revision_id"),
                revision["source"],
                revision["spec_hash"],
                Jsonb(revision["settings"]),
                created_by,
            ),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM graph_revisions WHERE revision_id=%s", (revision_id,)
            ).fetchone()
        if (
            row is None
            or str(row["run_id"]) != run_id
            or str(row["user_id"]) != user_id
            or str(row["spec_hash"]) != revision["spec_hash"]
        ):
            raise RuntimeError(f"Graph revision identity conflict: {revision_id}")
        with conn.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO graph_revision_nodes
                       (revision_id,node_id,node_type,position,definition,definition_hash)
                   VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                [
                    (
                        revision_id,
                        node["node_id"],
                        node["node_type"],
                        position,
                        Jsonb(node),
                        _hash(node),
                    )
                    for position, node in enumerate(revision["nodes"])
                ],
            )
        if revision["edges"]:
            with conn.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO graph_revision_edges
                           (revision_id,edge_id,source_node_id,target_node_id,edge_type,condition)
                       VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    [
                        (
                            revision_id,
                            edge["edge_id"],
                            edge["source_node_id"],
                            edge["target_node_id"],
                            edge["edge_type"],
                            Jsonb(edge.get("condition") or {}),
                        )
                        for edge in revision["edges"]
                    ],
                )
        counts = conn.execute(
            """SELECT
                 (SELECT count(*) FROM graph_revision_nodes WHERE revision_id=%s) AS nodes,
                 (SELECT count(*) FROM graph_revision_edges WHERE revision_id=%s) AS edges""",
            (revision_id, revision_id),
        ).fetchone()
        if int(counts["nodes"]) != len(revision["nodes"]) or int(counts["edges"]) != len(
            revision["edges"]
        ):
            raise RuntimeError(f"Graph revision snapshot conflict: {revision_id}")
        conn.execute(
            "UPDATE runtime_runs SET graph_revision_id=%s WHERE run_id=%s",
            (revision_id, run_id),
        )
        return revision_id

    @staticmethod
    def _freeze_graph_revision_from_rows(
        run_id: str,
        *,
        goal: str,
        options: dict[str, Any],
        tasks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        nodes = []
        for item in tasks:
            payload = dict(item.get("payload") or {})
            node_id = str(payload.get("spec_id") or item["task_id"]).rsplit(":", 1)[-1]
            dependencies = [
                str(value).rsplit(":", 1)[-1] for value in item.get("dependencies") or []
            ]
            nodes.append(
                {
                    "node_id": node_id,
                    "node_type": str(payload.get("node_type") or "agent"),
                    "name": item.get("name") or node_id,
                    "agent_id": item.get("agent_id") or options.get("agent_id") or "default",
                    "dependencies": dependencies,
                    **payload,
                }
            )
        edges = [
            {
                "edge_id": f"{source}->{node['node_id']}",
                "source_node_id": source,
                "target_node_id": node["node_id"],
                "edge_type": "dependency",
                "condition": {},
            }
            for node in nodes
            for source in node.get("dependencies") or []
        ]
        settings = {
            key: options.get(key)
            for key in (
                "goal",
                "agent_id",
                "max_concurrent",
                "fail_fast",
                "failure_policy",
                "aggregate",
                "aggregation_policy",
                "metadata",
            )
        }
        settings["goal"] = settings.get("goal") or goal
        snapshot = {
            "schema_version": 1,
            "revision_number": 1,
            "settings": settings,
            "nodes": nodes,
            "edges": edges,
        }
        spec_hash = _hash(snapshot)
        revision_identity = f"{run_id}:{spec_hash}".encode("utf-8")
        return {
            "schema_version": 1,
            "revision_id": f"graphrev_{sha256(revision_identity).hexdigest()}",
            "revision_number": 1,
            "parent_revision_id": None,
            "source": "storage_submission",
            "spec_hash": spec_hash,
            "settings": settings,
            "nodes": nodes,
            "edges": edges,
        }

    def list_graph_revisions(
        self, run_id: str, *, expected_user_id: str
    ) -> list[GraphRevisionRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM graph_revisions
                   WHERE run_id=%s AND user_id=%s ORDER BY revision_number""",
                (run_id, expected_user_id),
            ).fetchall()
            return [self._graph_revision(conn, row) for row in rows]

    def get_graph_revision(
        self, revision_id: str, *, expected_user_id: str
    ) -> GraphRevisionRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM graph_revisions WHERE revision_id=%s AND user_id=%s",
                (revision_id, expected_user_id),
            ).fetchone()
            return self._graph_revision(conn, row) if row else None

    def _graph_revision(self, conn: Any, row: dict[str, Any]) -> GraphRevisionRecord:
        from porthouse.storage.postgres_store import _iso, _json

        node_rows = conn.execute(
            """SELECT * FROM graph_revision_nodes WHERE revision_id=%s
               ORDER BY position,node_id""",
            (row["revision_id"],),
        ).fetchall()
        edge_rows = conn.execute(
            """SELECT * FROM graph_revision_edges WHERE revision_id=%s
               ORDER BY edge_id""",
            (row["revision_id"],),
        ).fetchall()
        return GraphRevisionRecord(
            revision_id=str(row["revision_id"]),
            run_id=str(row["run_id"]),
            user_id=str(row["user_id"]),
            revision_number=int(row["revision_number"]),
            parent_revision_id=row["parent_revision_id"],
            source=str(row["source"]),
            spec_hash=str(row["spec_hash"]),
            settings=dict(_json(row["settings"], {})),
            status=str(row["status"]),
            created_by=str(row["created_by"]),
            created_at=_iso(row["created_at"]) or "",
            nodes=[
                GraphNodeRecord(
                    revision_id=str(item["revision_id"]),
                    node_id=str(item["node_id"]),
                    node_type=str(item["node_type"]),
                    position=int(item["position"]),
                    definition=dict(_json(item["definition"], {})),
                    definition_hash=str(item["definition_hash"]),
                )
                for item in node_rows
            ],
            edges=[
                GraphEdgeRecord(
                    revision_id=str(item["revision_id"]),
                    edge_id=str(item["edge_id"]),
                    source_node_id=str(item["source_node_id"]),
                    target_node_id=str(item["target_node_id"]),
                    edge_type=str(item["edge_type"]),
                    condition=dict(_json(item["condition"], {})),
                )
                for item in edge_rows
            ],
        )
