"""PostgreSQL control plane for distributable App Packs."""

from __future__ import annotations

from typing import Any

from joyhousebot.storage.json_codec import Jsonb


class PostgresAppPackStoreMixin:
    def migrate_app_packs(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS app_definitions (
            app_id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',
            publisher TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'active',
            current_version TEXT,created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CHECK (status IN ('active','disabled','archived'))
        );
        CREATE TABLE IF NOT EXISTS app_releases (
            app_id TEXT NOT NULL REFERENCES app_definitions(app_id) ON DELETE CASCADE,
            version TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'draft',
            manifest JSONB NOT NULL,manifest_sha256 TEXT NOT NULL,
            validation_report JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_by TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),published_at TIMESTAMPTZ,
            PRIMARY KEY(app_id,version),
            CHECK (status IN ('draft','published','retired'))
        );
        CREATE INDEX IF NOT EXISTS ix_app_releases_status
            ON app_releases(app_id,status,updated_at DESC);
        CREATE TABLE IF NOT EXISTS app_installations (
            installation_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,
            app_id TEXT NOT NULL REFERENCES app_definitions(app_id),
            current_version TEXT NOT NULL,previous_version TEXT,
            status TEXT NOT NULL DEFAULT 'installed',configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
            granted_permissions JSONB NOT NULL DEFAULT '[]'::jsonb,
            dependency_lock JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_by TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),activated_at TIMESTAMPTZ,
            UNIQUE(user_id,app_id),
            CHECK (status IN ('installed','active','disabled','failed','uninstalled')),
            FOREIGN KEY(app_id,current_version) REFERENCES app_releases(app_id,version)
        );
        CREATE INDEX IF NOT EXISTS ix_app_installations_owner
            ON app_installations(user_id,status,updated_at DESC);
        CREATE TABLE IF NOT EXISTS app_installation_events (
            event_id BIGSERIAL PRIMARY KEY,installation_id TEXT NOT NULL,
            user_id TEXT NOT NULL,app_id TEXT NOT NULL,app_version TEXT NOT NULL,
            event_type TEXT NOT NULL,actor_id TEXT NOT NULL,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_app_installation_events
            ON app_installation_events(installation_id,event_id DESC);
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="app_packs",
                version=1,
                ddl=ddl,
                description="versioned App Pack catalog, dependency locks, and lifecycle",
            )

    def save_app_release(
        self,
        manifest: dict[str, Any],
        *,
        manifest_sha256: str,
        actor_id: str,
    ) -> dict[str, Any]:
        app_id = str(manifest["app_id"])
        version = str(manifest["version"])
        with self._pool.connection() as conn, conn.transaction():
            existing = conn.execute(
                "SELECT status FROM app_releases WHERE app_id=%s AND version=%s FOR UPDATE",
                (app_id, version),
            ).fetchone()
            if existing is not None and str(existing["status"]) != "draft":
                raise ValueError("published App Pack releases are immutable")
            conn.execute(
                """INSERT INTO app_definitions(app_id,name,description,publisher)
                   VALUES (%s,%s,%s,%s) ON CONFLICT(app_id) DO UPDATE SET
                     name=EXCLUDED.name,description=EXCLUDED.description,
                     publisher=EXCLUDED.publisher,updated_at=clock_timestamp()""",
                (
                    app_id,
                    str(manifest["name"]),
                    str(manifest["description"]),
                    str(manifest["publisher"]),
                ),
            )
            conn.execute(
                """INSERT INTO app_releases
                       (app_id,version,status,manifest,manifest_sha256,created_by)
                   VALUES (%s,%s,'draft',%s,%s,%s)
                   ON CONFLICT(app_id,version) DO UPDATE SET
                     manifest=EXCLUDED.manifest,manifest_sha256=EXCLUDED.manifest_sha256,
                     validation_report='{}'::jsonb,updated_at=clock_timestamp()""",
                (app_id, version, Jsonb(manifest), manifest_sha256, actor_id),
            )
        value = self.get_app_release(app_id, version)
        assert value is not None
        return value

    def record_app_validation(
        self, app_id: str, version: str, report: dict[str, Any]
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE app_releases SET validation_report=%s,
                       updated_at=clock_timestamp()
                   WHERE app_id=%s AND version=%s AND status='draft'
                   RETURNING app_id""",
                (Jsonb(report), app_id, version),
            ).fetchone()
            if row is None:
                raise ValueError("draft App Pack release not found")
        value = self.get_app_release(app_id, version)
        assert value is not None
        return value

    def publish_app_release(
        self, app_id: str, version: str, *, actor_id: str
    ) -> dict[str, Any]:
        already_published = False
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT status,validation_report FROM app_releases
                   WHERE app_id=%s AND version=%s FOR UPDATE""",
                (app_id, version),
            ).fetchone()
            if row is None:
                raise ValueError("App Pack release not found")
            if str(row["status"]) == "published":
                already_published = True
            elif str(row["status"]) != "draft":
                raise ValueError("App Pack release is not publishable")
            elif not bool(dict(row["validation_report"] or {}).get("valid")):
                raise ValueError("App Pack release must pass dependency validation")
            if not already_published:
                conn.execute(
                    """UPDATE app_releases SET status='published',published_at=clock_timestamp(),
                           updated_at=clock_timestamp() WHERE app_id=%s AND version=%s""",
                    (app_id, version),
                )
                conn.execute(
                    """UPDATE app_definitions SET current_version=%s,
                           updated_at=clock_timestamp() WHERE app_id=%s""",
                    (version, app_id),
                )
        value = self.get_app_release(app_id, version)
        assert value is not None
        return value

    def list_app_releases(self, app_id: str | None = None) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            if app_id:
                rows = conn.execute(
                    """SELECT d.name,d.description,d.publisher,d.status AS definition_status,
                              r.* FROM app_releases r JOIN app_definitions d USING(app_id)
                       WHERE r.app_id=%s ORDER BY r.updated_at DESC""",
                    (app_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT DISTINCT ON (r.app_id)
                              d.name,d.description,d.publisher,d.status AS definition_status,r.*
                       FROM app_releases r JOIN app_definitions d USING(app_id)
                       ORDER BY r.app_id,
                         CASE r.status WHEN 'published' THEN 0 WHEN 'draft' THEN 1 ELSE 2 END,
                         r.updated_at DESC"""
                ).fetchall()
        return [self._app_release_dict(row) for row in rows]

    def get_app_release(
        self, app_id: str, version: str | None = None
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            if version is None:
                row = conn.execute(
                    """SELECT d.name,d.description,d.publisher,d.status AS definition_status,r.*
                       FROM app_definitions d JOIN app_releases r
                         ON r.app_id=d.app_id AND r.version=d.current_version
                       WHERE d.app_id=%s""",
                    (app_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT d.name,d.description,d.publisher,d.status AS definition_status,r.*
                       FROM app_releases r JOIN app_definitions d USING(app_id)
                       WHERE r.app_id=%s AND r.version=%s""",
                    (app_id, version),
                ).fetchone()
        return self._app_release_dict(row) if row else None

    def install_app_pack(
        self,
        *,
        installation_id: str,
        user_id: str,
        app_id: str,
        version: str,
        configuration: dict[str, Any],
        granted_permissions: list[str],
        dependency_lock: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            release = conn.execute(
                """SELECT status FROM app_releases WHERE app_id=%s AND version=%s""",
                (app_id, version),
            ).fetchone()
            if release is None or str(release["status"]) != "published":
                raise ValueError("published App Pack release not found")
            current = conn.execute(
                """SELECT * FROM app_installations WHERE user_id=%s AND app_id=%s
                   FOR UPDATE""",
                (user_id, app_id),
            ).fetchone()
            if current is None:
                conn.execute(
                    """INSERT INTO app_installations
                           (installation_id,user_id,app_id,current_version,status,configuration,
                            granted_permissions,dependency_lock,created_by)
                       VALUES (%s,%s,%s,%s,'installed',%s,%s,%s,%s)""",
                    (
                        installation_id,
                        user_id,
                        app_id,
                        version,
                        Jsonb(configuration),
                        Jsonb(granted_permissions),
                        Jsonb(dependency_lock),
                        actor_id,
                    ),
                )
                event_type = "installed"
            else:
                installation_id = str(current["installation_id"])
                old_version = str(current["current_version"])
                conn.execute(
                    """UPDATE app_installations SET current_version=%s,previous_version=%s,
                           status='installed',configuration=%s,granted_permissions=%s,
                           dependency_lock=%s,updated_at=clock_timestamp()
                       WHERE installation_id=%s""",
                    (
                        version,
                        old_version if old_version != version else current["previous_version"],
                        Jsonb(configuration),
                        Jsonb(granted_permissions),
                        Jsonb(dependency_lock),
                        installation_id,
                    ),
                )
                event_type = "upgraded" if old_version != version else "reconfigured"
            self._append_app_event(
                conn,
                installation_id=installation_id,
                user_id=user_id,
                app_id=app_id,
                version=version,
                event_type=event_type,
                actor_id=actor_id,
            )
        value = self.get_app_installation(installation_id, expected_user_id=user_id)
        assert value is not None
        return value

    def transition_app_installation(
        self,
        installation_id: str,
        *,
        user_id: str,
        action: str,
        actor_id: str,
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT * FROM app_installations
                   WHERE installation_id=%s AND user_id=%s FOR UPDATE""",
                (installation_id, user_id),
            ).fetchone()
            if row is None:
                raise ValueError("App Pack installation not found")
            status = str(row["status"])
            version = str(row["current_version"])
            if action == "activate":
                if status not in {"installed", "disabled"}:
                    raise ValueError("only installed or disabled Apps can be activated")
                next_status = "active"
                conn.execute(
                    """UPDATE app_installations SET status='active',
                           activated_at=clock_timestamp(),updated_at=clock_timestamp()
                       WHERE installation_id=%s""",
                    (installation_id,),
                )
            elif action == "disable":
                if status != "active":
                    raise ValueError("only active Apps can be disabled")
                next_status = "disabled"
                conn.execute(
                    """UPDATE app_installations SET status='disabled',
                           updated_at=clock_timestamp() WHERE installation_id=%s""",
                    (installation_id,),
                )
            elif action == "uninstall":
                if status == "uninstalled":
                    raise ValueError("App Pack is already uninstalled")
                next_status = "uninstalled"
                conn.execute(
                    """UPDATE app_installations SET status='uninstalled',
                           updated_at=clock_timestamp() WHERE installation_id=%s""",
                    (installation_id,),
                )
            elif action == "rollback":
                previous = str(row["previous_version"] or "")
                if not previous:
                    raise ValueError("App Pack installation has no previous version")
                release = conn.execute(
                    """SELECT status,validation_report FROM app_releases
                       WHERE app_id=%s AND version=%s""",
                    (row["app_id"], previous),
                ).fetchone()
                if release is None or str(release["status"]) != "published":
                    raise ValueError("previous App Pack release is unavailable")
                conn.execute(
                    """UPDATE app_installations SET current_version=%s,previous_version=%s,
                           status='disabled',dependency_lock=%s,updated_at=clock_timestamp()
                       WHERE installation_id=%s""",
                    (
                        previous,
                        version,
                        Jsonb(dict(release["validation_report"] or {}).get("dependency_lock") or {}),
                        installation_id,
                    ),
                )
                version = previous
                next_status = "disabled"
            else:
                raise ValueError("unsupported App Pack lifecycle action")
            self._append_app_event(
                conn,
                installation_id=installation_id,
                user_id=user_id,
                app_id=str(row["app_id"]),
                version=version,
                event_type=action,
                actor_id=actor_id,
                details={"status": next_status},
            )
        value = self.get_app_installation(installation_id, expected_user_id=user_id)
        assert value is not None
        return value

    def get_app_installation(
        self, installation_id: str, *, expected_user_id: str
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT i.*,d.name,d.description,r.manifest,r.manifest_sha256,
                          r.bundle_digest
                   FROM app_installations i JOIN app_definitions d USING(app_id)
                   JOIN app_releases r ON r.app_id=i.app_id AND r.version=i.current_version
                   WHERE i.installation_id=%s AND i.user_id=%s""",
                (installation_id, expected_user_id),
            ).fetchone()
        return self._app_installation_dict(row) if row else None

    def list_app_installations(self, *, user_id: str) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT i.*,d.name,d.description,r.manifest,r.manifest_sha256,
                          r.bundle_digest
                   FROM app_installations i JOIN app_definitions d USING(app_id)
                   JOIN app_releases r ON r.app_id=i.app_id AND r.version=i.current_version
                   WHERE i.user_id=%s ORDER BY i.updated_at DESC""",
                (user_id,),
            ).fetchall()
        return [self._app_installation_dict(row) for row in rows]

    def list_app_installation_events(
        self, installation_id: str, *, user_id: str
    ) -> list[dict[str, Any]]:
        from joyhousebot.storage.postgres_store import _iso

        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM app_installation_events
                   WHERE installation_id=%s AND user_id=%s ORDER BY event_id DESC""",
                (installation_id, user_id),
            ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "installation_id": str(row["installation_id"]),
                "app_id": str(row["app_id"]),
                "version": str(row["app_version"]),
                "event_type": str(row["event_type"]),
                "actor_id": str(row["actor_id"]),
                "details": dict(row["details"]),
                "created_at": _iso(row["created_at"]),
            }
            for row in rows
        ]

    @staticmethod
    def _append_app_event(
        conn: Any,
        *,
        installation_id: str,
        user_id: str,
        app_id: str,
        version: str,
        event_type: str,
        actor_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """INSERT INTO app_installation_events
                   (installation_id,user_id,app_id,app_version,event_type,actor_id,details)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (
                installation_id,
                user_id,
                app_id,
                version,
                event_type,
                actor_id,
                Jsonb(details or {}),
            ),
        )

    @staticmethod
    def _app_release_dict(row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        return {
            "app_id": str(row["app_id"]),
            "version": str(row["version"]),
            "name": str(row["name"]),
            "description": str(row["description"]),
            "publisher": str(row["publisher"]),
            "definition_status": str(row["definition_status"]),
            "status": str(row["status"]),
            "manifest": dict(row["manifest"]),
            "manifest_sha256": str(row["manifest_sha256"]),
            "origin_ref": dict(row.get("origin_ref") or {}),
            "bundle_digest": str(row.get("bundle_digest") or ""),
            "validation_report": dict(row["validation_report"]),
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
            "published_at": _iso(row["published_at"]),
        }

    @staticmethod
    def _app_installation_dict(row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        return {
            "installation_id": str(row["installation_id"]),
            "user_id": str(row["user_id"]),
            "app_id": str(row["app_id"]),
            "version": str(row["current_version"]),
            "previous_version": str(row["previous_version"]) if row["previous_version"] else None,
            "name": str(row["name"]),
            "description": str(row["description"]),
            "status": str(row["status"]),
            "configuration": dict(row["configuration"]),
            "granted_permissions": list(row["granted_permissions"]),
            "dependency_lock": dict(row["dependency_lock"]),
            "manifest": dict(row["manifest"]),
            "manifest_sha256": str(row["manifest_sha256"]),
            "bundle_digest": str(row.get("bundle_digest") or ""),
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
            "activated_at": _iso(row["activated_at"]),
        }
