"""Leased PostgreSQL update subscriptions for remotely acquired Apps."""

from __future__ import annotations

from typing import Any

from porthouse.storage.json_codec import Jsonb


class PostgresAppUpdateStoreMixin:
    def save_app_update_subscription(
        self,
        *,
        subscription_id: str,
        user_id: str,
        installation_id: str,
        registry_id: str,
        publisher_id: str,
        app_id: str,
        channel: str,
        version_constraint: str,
        policy: str,
        allow_security_patch_download: bool,
        allow_auto_stage: bool,
        allow_auto_activate: bool,
    ) -> dict[str, Any]:
        if allow_auto_activate or policy == "activate_safe":
            raise ValueError(
                "activate_safe is disabled until Eval and rollout ACK gates are configured"
            )
        with self._pool.connection() as conn, conn.transaction():
            installation = conn.execute(
                """SELECT 1 FROM app_installations
                   WHERE installation_id=%s AND user_id=%s AND app_id=%s
                   AND status<>'uninstalled'""",
                (installation_id, user_id, app_id),
            ).fetchone()
            if installation is None:
                raise ValueError("active App installation not found for update subscription")
            conn.execute(
                """INSERT INTO app_update_subscriptions
                       (subscription_id,user_id,installation_id,registry_id,publisher_id,app_id,
                        channel,version_constraint,policy,allow_security_patch_download,
                        allow_auto_stage,allow_auto_activate)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(user_id,installation_id) DO UPDATE SET
                     registry_id=EXCLUDED.registry_id,publisher_id=EXCLUDED.publisher_id,
                     channel=EXCLUDED.channel,version_constraint=EXCLUDED.version_constraint,
                     policy=EXCLUDED.policy,
                     allow_security_patch_download=EXCLUDED.allow_security_patch_download,
                     allow_auto_stage=EXCLUDED.allow_auto_stage,
                     allow_auto_activate=EXCLUDED.allow_auto_activate,
                     status='active',next_check_at=clock_timestamp(),updated_at=clock_timestamp()""",
                (
                    subscription_id,
                    user_id,
                    installation_id,
                    registry_id,
                    publisher_id,
                    app_id,
                    channel,
                    version_constraint,
                    policy,
                    allow_security_patch_download,
                    allow_auto_stage,
                    allow_auto_activate,
                ),
            )
        values = self.list_app_update_subscriptions(user_id=user_id)
        return next(item for item in values if item["installation_id"] == installation_id)

    def claim_app_update_subscription(
        self, *, worker_id: str, lease_seconds: int = 120
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT s.subscription_id FROM app_update_subscriptions s
                   WHERE s.status='active' AND s.next_check_at<=clock_timestamp()
                     AND (s.lease_expires_at IS NULL OR s.lease_expires_at<clock_timestamp())
                   ORDER BY s.next_check_at,s.created_at
                   FOR UPDATE OF s SKIP LOCKED LIMIT 1"""
            ).fetchone()
            if row is None:
                return None
            claimed = conn.execute(
                """UPDATE app_update_subscriptions SET lease_owner=%s,
                     lease_expires_at=clock_timestamp()+(%s * interval '1 second'),
                     lease_version=lease_version+1,updated_at=clock_timestamp()
                   WHERE subscription_id=%s RETURNING subscription_id""",
                (worker_id, max(30, lease_seconds), row["subscription_id"]),
            ).fetchone()
        return self.get_app_update_subscription(str(claimed["subscription_id"]))

    def get_app_update_subscription(
        self, subscription_id: str
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                self._APP_UPDATE_SELECT + " WHERE s.subscription_id=%s",
                (subscription_id,),
            ).fetchone()
        return self._app_update_subscription_dict(row) if row else None

    def finish_app_update_subscription_check(
        self,
        subscription_id: str,
        *,
        worker_id: str,
        lease_version: int,
        cursor: str,
        snapshot_version: int,
        latest_release: dict[str, Any],
        error: dict[str, Any] | None,
        event_type: str,
        details: dict[str, Any],
        next_check_seconds: int = 300,
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE app_update_subscriptions SET cursor=%s,
                     last_snapshot_version=GREATEST(last_snapshot_version,%s),
                     latest_release=%s,last_error=%s,last_checked_at=clock_timestamp(),
                     next_check_at=clock_timestamp()+(%s * interval '1 second'),
                     lease_owner=NULL,lease_expires_at=NULL,updated_at=clock_timestamp()
                   WHERE subscription_id=%s AND lease_owner=%s AND lease_version=%s
                   RETURNING user_id""",
                (
                    cursor,
                    max(0, snapshot_version),
                    Jsonb(latest_release),
                    Jsonb(error) if error else None,
                    max(30, next_check_seconds),
                    subscription_id,
                    worker_id,
                    lease_version,
                ),
            ).fetchone()
            if row is None:
                raise ValueError("Update Subscription lease was fenced")
            conn.execute(
                """INSERT INTO app_update_subscription_events
                       (subscription_id,user_id,event_type,details)
                   VALUES (%s,%s,%s,%s)""",
                (subscription_id, row["user_id"], event_type, Jsonb(details)),
            )
        value = self.get_app_update_subscription(subscription_id)
        assert value is not None
        return value

    def list_app_update_subscriptions(self, *, user_id: str) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                self._APP_UPDATE_SELECT
                + " WHERE s.user_id=%s AND s.status<>'removed' ORDER BY s.updated_at DESC",
                (user_id,),
            ).fetchall()
        return [self._app_update_subscription_dict(row) for row in rows]

    _APP_UPDATE_SELECT = """SELECT s.*,i.current_version,i.status AS installation_status,
              i.configuration,i.granted_permissions,r.bundle_digest AS current_bundle_digest,
              r.origin_ref->>'release_id' AS current_release_id,
              entitlement.payload->>'offer_id' AS entitlement_offer_id,
              entitlement.payload AS entitlement_payload,
              entitlement.envelope AS entitlement_envelope
       FROM app_update_subscriptions s
       JOIN app_installations i ON i.installation_id=s.installation_id
       LEFT JOIN app_releases r ON r.app_id=i.app_id AND r.version=i.current_version
       LEFT JOIN LATERAL (
           SELECT e.payload,e.envelope FROM app_market_entitlements e
           WHERE e.user_id=s.user_id AND e.registry_id=s.registry_id
             AND e.publisher_id=s.publisher_id AND e.app_id=s.app_id
           ORDER BY e.updated_at DESC LIMIT 1
       ) entitlement ON TRUE"""

    @staticmethod
    def _app_update_subscription_dict(row: Any) -> dict[str, Any]:
        return {
            "subscription_id": str(row["subscription_id"]),
            "user_id": str(row["user_id"]),
            "installation_id": str(row["installation_id"]),
            "registry_id": str(row["registry_id"]),
            "publisher_id": str(row["publisher_id"]),
            "app_id": str(row["app_id"]),
            "channel": str(row["channel"]),
            "version_constraint": str(row["version_constraint"]),
            "policy": str(row["policy"]),
            "allow_security_patch_download": bool(row["allow_security_patch_download"]),
            "allow_auto_stage": bool(row["allow_auto_stage"]),
            "allow_auto_activate": bool(row["allow_auto_activate"]),
            "cursor": str(row["cursor"]),
            "last_snapshot_version": int(row["last_snapshot_version"]),
            "status": str(row["status"]),
            "current_version": str(row.get("current_version") or ""),
            "current_bundle_digest": str(row.get("current_bundle_digest") or ""),
            "current_release_id": str(row.get("current_release_id") or ""),
            "entitlement_offer_id": str(row.get("entitlement_offer_id") or ""),
            "entitlement": (
                {
                    "payload": dict(row.get("entitlement_payload") or {}),
                    "envelope": dict(row.get("entitlement_envelope") or {}),
                }
                if row.get("entitlement_payload") and row.get("entitlement_envelope")
                else {}
            ),
            "installation_status": str(row.get("installation_status") or ""),
            "configuration": dict(row.get("configuration") or {}),
            "granted_permissions": list(row.get("granted_permissions") or []),
            "latest_release": dict(row["latest_release"] or {}),
            "last_error": dict(row["last_error"] or {}) if row["last_error"] else None,
            "last_checked_at": (
                str(row["last_checked_at"]) if row["last_checked_at"] else None
            ),
            "updated_at": str(row["updated_at"]),
            "lease_owner": str(row["lease_owner"] or ""),
            "lease_version": int(row["lease_version"]),
        }
