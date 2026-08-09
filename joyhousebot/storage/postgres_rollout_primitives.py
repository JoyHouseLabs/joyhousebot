"""Activation and event primitives shared by configuration rollouts."""

from __future__ import annotations

from typing import Any

from joyhousebot.storage.platform_records import ConfigurationRolloutRecord


class PostgresRolloutPrimitiveStoreMixin:
    def _finish_parent_rollback(
        self,
        conn: Any,
        rollout: Any,
        *,
        succeeded: bool,
        actor_id: str,
    ) -> None:
        """Project a rollback preheat result onto the original release rollout."""
        parent_id = rollout["rollback_of_rollout_id"]
        if parent_id is None:
            return
        parent = conn.execute(
            "SELECT * FROM configuration_rollouts WHERE rollout_id=%s FOR UPDATE",
            (str(parent_id),),
        ).fetchone()
        if parent is None or str(parent["status"]) != "rollback_pending":
            return
        parent_status = "rolled_back" if succeeded else "completed"
        conn.execute(
            """UPDATE configuration_rollouts SET status=%s,
                   updated_at=clock_timestamp(),
                   completed_at=CASE WHEN %s='rolled_back'
                       THEN clock_timestamp() ELSE completed_at END
               WHERE rollout_id=%s""",
            (parent_status, parent_status, str(parent_id)),
        )
        self._append_configuration_event_from_rollout(
            conn,
            parent,
            "rollback.completed" if succeeded else "rollback.failed",
            actor_id,
            revision_id=str(rollout["revision_id"]),
        )

    @staticmethod
    def _append_configuration_event(
        conn: Any,
        aggregate_type: str,
        aggregate_id: str,
        revision_id: str,
        event_type: str,
        actor_id: str,
    ) -> None:
        conn.execute(
            """INSERT INTO configuration_events
                   (aggregate_type,aggregate_id,revision_id,event_type,actor_id)
               VALUES (%s,%s,%s,%s,%s)""",
            (aggregate_type, aggregate_id, revision_id, event_type, actor_id),
        )

    def _append_configuration_event_from_rollout(
        self,
        conn: Any,
        rollout: Any,
        event_type: str,
        actor_id: str,
        *,
        revision_id: str | None = None,
    ) -> None:
        self._append_configuration_event(
            conn,
            str(rollout["aggregate_type"]),
            str(rollout["aggregate_id"]),
            revision_id or str(rollout["revision_id"]),
            event_type,
            actor_id,
        )

    @staticmethod
    def _current_configuration_revision(
        conn: Any, aggregate_type: str, aggregate_id: str
    ) -> str | None:
        if aggregate_type == "agent":
            row = conn.execute(
                "SELECT current_revision_id AS revision FROM agent_definitions WHERE agent_id=%s",
                (aggregate_id,),
            ).fetchone()
        elif aggregate_type == "capability":
            row = conn.execute(
                "SELECT current_version AS revision FROM capability_definitions WHERE capability_id=%s",
                (aggregate_id,),
            ).fetchone()
        elif aggregate_type == "scenario":
            row = conn.execute(
                "SELECT current_version::text AS revision FROM scenario_definitions WHERE scenario_id=%s",
                (aggregate_id,),
            ).fetchone()
        elif aggregate_type == "plugin":
            row = conn.execute(
                """SELECT version AS revision FROM plugin_releases
                   WHERE plugin_id=%s AND status='active'""",
                (aggregate_id,),
            ).fetchone()
        else:
            raise ValueError("unsupported configuration rollout type")
        return str(row["revision"]) if row and row["revision"] is not None else None

    @staticmethod
    def _activate_configuration_revision(
        conn: Any, aggregate_type: str, aggregate_id: str, revision_id: str
    ) -> None:
        if aggregate_type == "agent":
            changed = conn.execute(
                """UPDATE agent_definitions SET current_revision_id=%s,
                       updated_at=clock_timestamp() WHERE agent_id=%s""",
                (revision_id, aggregate_id),
            ).rowcount
        elif aggregate_type == "capability":
            changed = conn.execute(
                """UPDATE capability_versions SET status='published',
                       published_at=COALESCE(published_at,clock_timestamp())
                   WHERE capability_id=%s AND version=%s""",
                (aggregate_id, revision_id),
            ).rowcount
            if changed:
                conn.execute(
                    """UPDATE capability_definitions SET current_version=%s,
                           updated_at=clock_timestamp() WHERE capability_id=%s""",
                    (revision_id, aggregate_id),
                )
        elif aggregate_type == "scenario":
            changed = conn.execute(
                """UPDATE scenario_versions SET status='published',
                       published_at=COALESCE(published_at,clock_timestamp())
                   WHERE scenario_id=%s AND version=%s""",
                (aggregate_id, int(revision_id)),
            ).rowcount
            if changed:
                conn.execute(
                    """UPDATE scenario_definitions SET current_version=%s,
                           updated_at=clock_timestamp() WHERE scenario_id=%s""",
                    (int(revision_id), aggregate_id),
                )
        elif aggregate_type == "plugin":
            target = conn.execute(
                "SELECT 1 FROM plugin_releases WHERE plugin_id=%s AND version=%s",
                (aggregate_id, revision_id),
            ).fetchone()
            if target is None:
                changed = 0
            else:
                conn.execute(
                    """UPDATE plugin_releases SET status='retired',
                           updated_at=clock_timestamp()
                       WHERE plugin_id=%s AND status='active' AND version<>%s""",
                    (aggregate_id, revision_id),
                )
                changed = conn.execute(
                    """UPDATE plugin_releases SET status='active',
                           updated_at=clock_timestamp()
                       WHERE plugin_id=%s AND version=%s""",
                    (aggregate_id, revision_id),
                ).rowcount
        else:
            raise ValueError("unsupported configuration rollout type")
        if changed != 1:
            raise ValueError("configuration revision not found during activation")

    def _notify_configuration(self, conn: Any, rollout: Any) -> None:
        self._notify(
            conn,
            f"config:{rollout['aggregate_type']}:{rollout['aggregate_id']}",
        )

    @staticmethod
    def _configuration_rollout(row: dict[str, Any]) -> ConfigurationRolloutRecord:
        from joyhousebot.storage.postgres_store import _iso

        return ConfigurationRolloutRecord(
            rollout_id=str(row["rollout_id"]),
            aggregate_type=str(row["aggregate_type"]),
            aggregate_id=str(row["aggregate_id"]),
            revision_id=str(row["revision_id"]),
            status=str(row["status"]),
            created_by=str(row["created_by"]),
            target_worker_count=int(row["target_worker_count"]),
            acknowledged_worker_count=int(row["acknowledged_worker_count"]),
            failed_worker_count=int(row["failed_worker_count"]),
            previous_revision_id=(
                str(row["previous_revision_id"])
                if row["previous_revision_id"] is not None
                else None
            ),
            activation_mode=str(row["activation_mode"]),
            timeout_seconds=int(row["timeout_seconds"]),
            deadline_at=_iso(row["deadline_at"]),
            auto_rollback=bool(row["auto_rollback"]),
            retry_of_rollout_id=(
                str(row["retry_of_rollout_id"])
                if row["retry_of_rollout_id"] is not None
                else None
            ),
            approved_by=str(row["approved_by"]) if row["approved_by"] else None,
            approved_at=_iso(row["approved_at"]),
            cancelled_by=str(row["cancelled_by"]) if row["cancelled_by"] else None,
            cancelled_at=_iso(row["cancelled_at"]),
            rollback_revision_id=(
                str(row["rollback_revision_id"])
                if row["rollback_revision_id"] is not None
                else None
            ),
            rollback_of_rollout_id=(
                str(row["rollback_of_rollout_id"])
                if row["rollback_of_rollout_id"] is not None
                else None
            ),
            created_at=_iso(row["created_at"]) or "",
            updated_at=_iso(row["updated_at"]) or "",
            completed_at=_iso(row["completed_at"]),
        )
