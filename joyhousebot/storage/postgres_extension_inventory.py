"""PostgreSQL desired state for deployment-discovered extensions."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from joyhousebot.storage.json_codec import Jsonb


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    return json.loads(value) if isinstance(value, str) else value


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else (str(value) if value else None)


class PostgresExtensionInventoryStoreMixin:
    """Available/installed/allowed/desired extension state projections."""

    def sync_extension_inventory(
        self,
        candidates: list[dict[str, Any]],
        *,
        allowed_ids: set[str],
        initially_active_ids: set[str],
    ) -> list[dict[str, Any]]:
        allowed = {str(item).strip() for item in allowed_ids if str(item).strip()}
        initial = {
            str(item).strip() for item in initially_active_ids if str(item).strip()
        }
        by_id = {
            str(item["extension_id"]): dict(item)
            for item in candidates
            if str(item.get("extension_id") or "").strip()
        }
        for extension_id in sorted(allowed | initial):
            by_id.setdefault(
                extension_id,
                {
                    "extension_id": extension_id,
                    "name": extension_id,
                    "description": "",
                    "source_version": "",
                    "extension_types": [],
                    "distribution_name": "",
                    "distribution_version": "",
                    "source_location": "",
                    "source_digest": "",
                    "source_available": False,
                    "installed": False,
                    "metadata": {},
                },
            )
        with self._pool.connection() as conn, conn.transaction():
            active_rows = conn.execute(
                "SELECT DISTINCT plugin_id FROM plugin_releases WHERE status='active'"
            ).fetchall()
            active = {str(row["plugin_id"]) for row in active_rows}
            conn.execute(
                """UPDATE extension_inventory
                   SET source_available=FALSE,installed=FALSE,deployment_allowed=FALSE,
                       observed_at=clock_timestamp(),updated_at=clock_timestamp()"""
            )
            for extension_id in sorted(by_id):
                item = by_id[extension_id]
                conn.execute(
                    """INSERT INTO extension_inventory
                           (extension_id,name,description,source_version,extension_types,
                            distribution_name,distribution_version,source_location,
                            source_digest,source_available,installed,deployment_allowed,
                            desired_active,metadata)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(extension_id) DO UPDATE SET
                           name=EXCLUDED.name,description=EXCLUDED.description,
                           source_version=EXCLUDED.source_version,
                           extension_types=EXCLUDED.extension_types,
                           distribution_name=EXCLUDED.distribution_name,
                           distribution_version=EXCLUDED.distribution_version,
                           source_location=EXCLUDED.source_location,
                           source_digest=EXCLUDED.source_digest,
                           source_available=EXCLUDED.source_available,
                           installed=EXCLUDED.installed,
                           deployment_allowed=EXCLUDED.deployment_allowed,
                           metadata=EXCLUDED.metadata,
                           observed_at=clock_timestamp(),updated_at=clock_timestamp()""",
                    (
                        extension_id,
                        str(item.get("name") or extension_id),
                        str(item.get("description") or ""),
                        str(item.get("source_version") or ""),
                        Jsonb(list(item.get("extension_types") or ())),
                        str(item.get("distribution_name") or ""),
                        str(item.get("distribution_version") or ""),
                        str(item.get("source_location") or ""),
                        str(item.get("source_digest") or ""),
                        bool(item.get("source_available")),
                        bool(item.get("installed")),
                        extension_id in allowed,
                        extension_id in initial or extension_id in active,
                        Jsonb(dict(item.get("metadata") or {})),
                    ),
                )
        return self.list_extension_inventory()

    def list_extension_inventory(self) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT inventory.*,
                          release.version AS active_version,
                          release.build_digest AS active_build_digest
                   FROM extension_inventory inventory
                   LEFT JOIN plugin_releases release
                     ON release.plugin_id=inventory.extension_id AND release.status='active'
                   ORDER BY inventory.extension_id"""
            ).fetchall()
        return [self._inventory_dict(row) for row in rows]

    def get_extension_inventory(self, extension_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT inventory.*,
                          release.version AS active_version,
                          release.build_digest AS active_build_digest
                   FROM extension_inventory inventory
                   LEFT JOIN plugin_releases release
                     ON release.plugin_id=inventory.extension_id AND release.status='active'
                   WHERE inventory.extension_id=%s""",
                (extension_id,),
            ).fetchone()
        return self._inventory_dict(row) if row else None

    def set_extension_desired_active(
        self, extension_id: str, desired_active: bool, *, actor_id: str
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "SELECT * FROM extension_inventory WHERE extension_id=%s FOR UPDATE",
                (extension_id,),
            ).fetchone()
            if row is None:
                raise ValueError("extension is not present in the deployment catalog")
            if desired_active:
                if not bool(row["deployment_allowed"]):
                    raise ValueError("extension is outside the deployment allowlist")
                if not bool(row["installed"]):
                    raise ValueError("extension package is not installed")
                metadata = dict(_json(row["metadata"], {}))
                if bool(metadata.get("source_conflict")):
                    raise ValueError("extension has conflicting catalog sources")
            conn.execute(
                """UPDATE extension_inventory SET desired_active=%s,
                       desired_changed_by=%s,desired_changed_at=clock_timestamp(),
                       updated_at=clock_timestamp() WHERE extension_id=%s""",
                (bool(desired_active), actor_id, extension_id),
            )
            release = conn.execute(
                """SELECT version FROM plugin_releases WHERE plugin_id=%s
                   ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'staged' THEN 1 ELSE 2 END,
                            updated_at DESC LIMIT 1""",
                (extension_id,),
            ).fetchone()
            revision = str(release["version"]) if release else "catalog"
            conn.execute(
                """INSERT INTO configuration_events
                       (aggregate_type,aggregate_id,revision_id,event_type,actor_id)
                   VALUES ('plugin',%s,%s,%s,%s)""",
                (
                    extension_id,
                    revision,
                    "activation.desired" if desired_active else "deactivation.completed",
                    actor_id,
                ),
            )
            self._notify(conn, f"config:plugin:{extension_id}")
        value = self.get_extension_inventory(extension_id)
        assert value is not None
        return value

    def is_plugin_execution_enabled(self, plugin_id: str) -> bool:
        """Fail closed for catalogued extensions; ignore untracked core plugins."""
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT deployment_allowed,desired_active FROM extension_inventory
                   WHERE extension_id=%s""",
                (plugin_id,),
            ).fetchone()
        if row is None:
            return True
        return bool(row["deployment_allowed"]) and bool(row["desired_active"])

    @staticmethod
    def _inventory_dict(row: Any) -> dict[str, Any]:
        return {
            "extension_id": str(row["extension_id"]),
            "name": str(row["name"]),
            "description": str(row["description"]),
            "source_version": str(row["source_version"]),
            "extension_types": list(_json(row["extension_types"], [])),
            "distribution_name": str(row["distribution_name"]),
            "distribution_version": str(row["distribution_version"]),
            "source_location": str(row["source_location"]),
            "source_digest": str(row["source_digest"]),
            "source_available": bool(row["source_available"]),
            "installed": bool(row["installed"]),
            "deployment_allowed": bool(row["deployment_allowed"]),
            "desired_active": bool(row["desired_active"]),
            "metadata": dict(_json(row["metadata"], {})),
            "desired_changed_by": row["desired_changed_by"],
            "desired_changed_at": _iso(row["desired_changed_at"]),
            "active_version": str(row["active_version"]) if row["active_version"] else None,
            "active_build_digest": (
                str(row["active_build_digest"]) if row["active_build_digest"] else None
            ),
            "created_at": _iso(row["created_at"]),
            "observed_at": _iso(row["observed_at"]),
            "updated_at": _iso(row["updated_at"]),
        }


__all__ = ["PostgresExtensionInventoryStoreMixin"]
