"""Durable App callback registrations and delivery outbox."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from joyhousebot.domain.app_callbacks import normalize_app_callback
from joyhousebot.storage.json_codec import Jsonb


class PostgresAppCallbackStoreMixin:
    def migrate_app_callbacks(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS app_callbacks (
            callback_id TEXT PRIMARY KEY,
            installation_id TEXT NOT NULL REFERENCES app_installations(installation_id),
            user_id TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            secret_ref TEXT NOT NULL,
            events JSONB NOT NULL,
            max_attempts INTEGER NOT NULL DEFAULT 8,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            revoked_at TIMESTAMPTZ,
            UNIQUE(installation_id,endpoint),
            CHECK (max_attempts BETWEEN 1 AND 20)
        );
        CREATE INDEX IF NOT EXISTS ix_app_callbacks_installation
            ON app_callbacks(user_id,installation_id,enabled);
        CREATE TABLE IF NOT EXISTS app_callback_events (
            sequence BIGSERIAL PRIMARY KEY,
            callback_id TEXT NOT NULL,
            installation_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_app_callback_events_callback
            ON app_callback_events(callback_id,sequence DESC);
        CREATE TABLE IF NOT EXISTS app_callback_outbox (
            event_id TEXT PRIMARY KEY,
            callback_id TEXT NOT NULL REFERENCES app_callbacks(callback_id),
            installation_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload JSONB NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempt INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL,
            available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            lease_owner TEXT,
            lease_expires_at TIMESTAMPTZ,
            lease_version BIGINT NOT NULL DEFAULT 0,
            response_status INTEGER,
            last_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            delivered_at TIMESTAMPTZ,
            UNIQUE(callback_id,run_id,event_type),
            CHECK (status IN ('pending','sending','sent','dead'))
        );
        CREATE INDEX IF NOT EXISTS ix_app_callback_outbox_claim
            ON app_callback_outbox(status,available_at,created_at)
            WHERE status IN ('pending','sending');
        CREATE INDEX IF NOT EXISTS ix_app_callback_outbox_run
            ON app_callback_outbox(run_id,created_at DESC);
        CREATE TABLE IF NOT EXISTS app_callback_delivery_events (
            sequence BIGSERIAL PRIMARY KEY,
            event_id TEXT NOT NULL,
            callback_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            worker_id TEXT NOT NULL,
            response_status INTEGER,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_app_callback_delivery_events
            ON app_callback_delivery_events(event_id,sequence DESC);
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="app_callbacks",
                version=1,
                ddl=ddl,
                description="signed App completion callback outbox and delivery audit",
            )
            replay_ddl = """
            ALTER TABLE app_callback_outbox
                ADD COLUMN IF NOT EXISTS replay_of_event_id TEXT;
            ALTER TABLE app_callback_outbox
                ADD COLUMN IF NOT EXISTS replay_sequence INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE app_callback_outbox
                ADD COLUMN IF NOT EXISTS replay_request_key TEXT;
            ALTER TABLE app_callback_outbox DROP CONSTRAINT IF EXISTS
                app_callback_outbox_callback_id_run_id_event_type_key;
            CREATE UNIQUE INDEX IF NOT EXISTS uq_app_callback_delivery_identity
                ON app_callback_outbox(callback_id,run_id,event_type,replay_sequence);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_app_callback_replay_request
                ON app_callback_outbox(callback_id,replay_request_key)
                WHERE replay_request_key IS NOT NULL;
            """
            conn.execute(replay_ddl)
            self._record_migration(
                conn,
                name="app_callbacks",
                version=2,
                ddl=replay_ddl,
                description="immutable idempotent manual callback replay deliveries",
            )

    def save_app_callback(
        self,
        *,
        installation_id: str,
        user_id: str,
        configuration: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        normalized = normalize_app_callback(configuration)
        callback_id = f"appcb_{uuid4().hex}"
        with self._pool.connection() as conn, conn.transaction():
            installation = conn.execute(
                """SELECT status FROM app_installations
                   WHERE installation_id=%s AND user_id=%s""",
                (installation_id, user_id),
            ).fetchone()
            if installation is None:
                raise ValueError("App installation not found")
            if str(installation["status"]) != "active":
                raise ValueError("App installation must be active before adding a callback")
            row = conn.execute(
                """INSERT INTO app_callbacks
                       (callback_id,installation_id,user_id,endpoint,secret_ref,events,
                        max_attempts,created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(installation_id,endpoint) DO UPDATE SET
                     secret_ref=EXCLUDED.secret_ref,events=EXCLUDED.events,
                     max_attempts=EXCLUDED.max_attempts,enabled=TRUE,revoked_at=NULL,
                     updated_at=clock_timestamp()
                   RETURNING *""",
                (
                    callback_id,
                    installation_id,
                    user_id,
                    normalized["endpoint"],
                    normalized["secret_ref"],
                    Jsonb(normalized["events"]),
                    normalized["max_attempts"],
                    actor_id,
                ),
            ).fetchone()
            conn.execute(
                """INSERT INTO app_callback_events
                       (callback_id,installation_id,user_id,actor_id,event_type,data)
                   VALUES (%s,%s,%s,%s,'callback.registered',%s)""",
                (
                    row["callback_id"],
                    installation_id,
                    user_id,
                    actor_id,
                    Jsonb(
                        {
                            "endpoint": normalized["endpoint"],
                            "events": normalized["events"],
                            "max_attempts": normalized["max_attempts"],
                        }
                    ),
                ),
            )
        return self._app_callback_dict(row)

    def list_app_callbacks(
        self, *, installation_id: str, user_id: str
    ) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM app_callbacks
                   WHERE installation_id=%s AND user_id=%s ORDER BY created_at DESC""",
                (installation_id, user_id),
            ).fetchall()
        return [self._app_callback_dict(row) for row in rows]

    def revoke_app_callback(
        self,
        callback_id: str,
        *,
        installation_id: str,
        user_id: str,
        actor_id: str,
    ) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE app_callbacks SET enabled=FALSE,revoked_at=clock_timestamp(),
                          updated_at=clock_timestamp()
                   WHERE callback_id=%s AND installation_id=%s AND user_id=%s AND enabled
                   RETURNING callback_id,endpoint""",
                (callback_id, installation_id, user_id),
            ).fetchone()
            if row is not None:
                conn.execute(
                    """UPDATE app_callback_outbox SET status='dead',
                              last_error='callback revoked',lease_owner=NULL,
                              lease_expires_at=NULL,updated_at=clock_timestamp()
                       WHERE callback_id=%s AND status IN ('pending','sending')""",
                    (callback_id,),
                )
                conn.execute(
                    """INSERT INTO app_callback_events
                           (callback_id,installation_id,user_id,actor_id,event_type,data)
                       VALUES (%s,%s,%s,%s,'callback.revoked',%s)""",
                    (
                        callback_id,
                        installation_id,
                        user_id,
                        actor_id,
                        Jsonb({"endpoint": row["endpoint"]}),
                    ),
                )
        return row is not None

    def claim_app_callback_delivery(
        self, *, worker_id: str, lease_seconds: int = 60
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """WITH candidate AS (
                     SELECT o.event_id FROM app_callback_outbox o
                     JOIN app_callbacks c USING(callback_id)
                     WHERE c.enabled
                       AND o.status IN ('pending','sending')
                       AND o.available_at<=clock_timestamp()
                       AND (o.status='pending' OR o.lease_expires_at<clock_timestamp())
                     ORDER BY o.available_at,o.created_at
                     FOR UPDATE OF o SKIP LOCKED LIMIT 1
                   )
                   UPDATE app_callback_outbox o SET status='sending',attempt=o.attempt+1,
                     lease_owner=%s,lease_expires_at=clock_timestamp()+(%s || ' seconds')::interval,
                     lease_version=o.lease_version+1,updated_at=clock_timestamp()
                   FROM candidate c,app_callbacks callback
                   WHERE o.event_id=c.event_id AND callback.callback_id=o.callback_id
                   RETURNING o.*,callback.endpoint,callback.secret_ref""",
                (worker_id, max(10, min(int(lease_seconds), 600))),
            ).fetchone()
        return self._app_delivery_dict(row) if row else None

    def complete_app_callback_delivery(
        self,
        event_id: str,
        *,
        worker_id: str,
        lease_version: int,
        response_status: int,
    ) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE app_callback_outbox SET status='sent',response_status=%s,
                          delivered_at=clock_timestamp(),lease_owner=NULL,
                          lease_expires_at=NULL,updated_at=clock_timestamp()
                   WHERE event_id=%s AND status='sending' AND lease_owner=%s
                     AND lease_version=%s RETURNING callback_id,run_id,attempt""",
                (response_status, event_id, worker_id, lease_version),
            ).fetchone()
            if row is not None:
                self._append_delivery_event(
                    conn,
                    event_id=event_id,
                    callback_id=row["callback_id"],
                    run_id=row["run_id"],
                    status="sent",
                    attempt=int(row["attempt"]),
                    worker_id=worker_id,
                    response_status=response_status,
                )
        return row is not None

    def fail_app_callback_delivery(
        self,
        event_id: str,
        *,
        worker_id: str,
        lease_version: int,
        error: str,
        response_status: int | None = None,
    ) -> str | None:
        with self._pool.connection() as conn, conn.transaction():
            current = conn.execute(
                """SELECT * FROM app_callback_outbox
                   WHERE event_id=%s AND status='sending' AND lease_owner=%s
                     AND lease_version=%s FOR UPDATE""",
                (event_id, worker_id, lease_version),
            ).fetchone()
            if current is None:
                return None
            terminal = int(current["attempt"]) >= int(current["max_attempts"])
            status = "dead" if terminal else "pending"
            backoff = min(3600, 30 * (2 ** max(0, int(current["attempt"]) - 1)))
            row = conn.execute(
                """UPDATE app_callback_outbox SET status=%s,last_error=%s,
                          response_status=%s,lease_owner=NULL,lease_expires_at=NULL,
                          available_at=CASE WHEN %s='dead' THEN available_at
                            ELSE clock_timestamp()+(%s || ' seconds')::interval END,
                          updated_at=clock_timestamp()
                   WHERE event_id=%s RETURNING callback_id,run_id,attempt""",
                (
                    status,
                    str(error)[:1000],
                    response_status,
                    status,
                    backoff,
                    event_id,
                ),
            ).fetchone()
            self._append_delivery_event(
                conn,
                event_id=event_id,
                callback_id=row["callback_id"],
                run_id=row["run_id"],
                status=status,
                attempt=int(row["attempt"]),
                worker_id=worker_id,
                response_status=response_status,
                error=str(error)[:1000],
            )
        return status

    def list_run_app_callback_deliveries(
        self, run_id: str, *, user_id: str
    ) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT o.*,c.endpoint,c.secret_ref FROM app_callback_outbox o
                   JOIN app_callbacks c USING(callback_id)
                   WHERE o.run_id=%s AND o.user_id=%s ORDER BY o.created_at DESC""",
                (run_id, user_id),
            ).fetchall()
        return [self._app_delivery_dict(row) for row in rows]

    def replay_app_callback_delivery(
        self,
        event_id: str,
        *,
        run_id: str,
        user_id: str,
        actor_id: str,
        request_key: str,
    ) -> dict[str, Any] | None:
        """Create a new immutable delivery; never reset the original outbox row."""
        if not str(request_key).strip():
            raise ValueError("callback replay requires an idempotency key")
        with self._pool.connection() as conn, conn.transaction():
            source = conn.execute(
                """SELECT o.*,c.enabled,c.endpoint,c.secret_ref
                   FROM app_callback_outbox o JOIN app_callbacks c USING(callback_id)
                   WHERE o.event_id=%s AND o.run_id=%s AND o.user_id=%s FOR UPDATE OF o""",
                (event_id, run_id, user_id),
            ).fetchone()
            if source is None:
                return None
            if not source["enabled"]:
                raise ValueError("revoked App callback cannot be replayed")
            if str(source["status"]) not in {"sent", "dead"}:
                raise ValueError("only sent or dead App callback deliveries can be replayed")
            existing = conn.execute(
                """SELECT o.*,c.endpoint,c.secret_ref
                   FROM app_callback_outbox o JOIN app_callbacks c USING(callback_id)
                   WHERE o.callback_id=%s AND o.replay_request_key=%s""",
                (source["callback_id"], str(request_key).strip()),
            ).fetchone()
            if existing is not None:
                return self._app_delivery_dict(existing)
            root_event_id = str(source["replay_of_event_id"] or source["event_id"])
            next_sequence = int(
                conn.execute(
                    """SELECT COALESCE(MAX(replay_sequence),0)+1 AS value
                       FROM app_callback_outbox
                       WHERE callback_id=%s AND run_id=%s AND event_type=%s""",
                    (source["callback_id"], run_id, source["event_type"]),
                ).fetchone()["value"]
            )
            replay_event_id = f"{root_event_id}:replay:{next_sequence}"
            payload = dict(source["payload"])
            payload["event_id"] = replay_event_id
            payload["delivery"] = {
                "replay_of_event_id": root_event_id,
                "replay_sequence": next_sequence,
            }
            row = conn.execute(
                """INSERT INTO app_callback_outbox
                       (event_id,callback_id,installation_id,user_id,run_id,event_type,
                        payload,max_attempts,replay_of_event_id,replay_sequence,
                        replay_request_key)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING *""",
                (
                    replay_event_id,
                    source["callback_id"],
                    source["installation_id"],
                    user_id,
                    run_id,
                    source["event_type"],
                    Jsonb(payload),
                    source["max_attempts"],
                    root_event_id,
                    next_sequence,
                    str(request_key).strip(),
                ),
            ).fetchone()
            conn.execute(
                """INSERT INTO app_callback_events
                       (callback_id,installation_id,user_id,actor_id,event_type,data)
                   VALUES (%s,%s,%s,%s,'delivery.replayed',%s)""",
                (
                    source["callback_id"],
                    source["installation_id"],
                    user_id,
                    actor_id,
                    Jsonb(
                        {
                            "source_event_id": event_id,
                            "root_event_id": root_event_id,
                            "replay_event_id": replay_event_id,
                            "request_key": str(request_key).strip(),
                        }
                    ),
                ),
            )
            result = dict(row)
            result["endpoint"] = source["endpoint"]
            result["secret_ref"] = source["secret_ref"]
        return self._app_delivery_dict(result)

    @staticmethod
    def _append_delivery_event(
        conn: Any,
        *,
        event_id: str,
        callback_id: str,
        run_id: str,
        status: str,
        attempt: int,
        worker_id: str,
        response_status: int | None,
        error: str | None = None,
    ) -> None:
        conn.execute(
            """INSERT INTO app_callback_delivery_events
                   (event_id,callback_id,run_id,status,attempt,worker_id,response_status,error)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                event_id,
                callback_id,
                run_id,
                status,
                attempt,
                worker_id,
                response_status,
                error,
            ),
        )

    @staticmethod
    def _app_callback_dict(row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        return {
            "callback_id": str(row["callback_id"]),
            "installation_id": str(row["installation_id"]),
            "user_id": str(row["user_id"]),
            "endpoint": str(row["endpoint"]),
            "secret_ref": str(row["secret_ref"]),
            "events": [str(item) for item in row["events"]],
            "max_attempts": int(row["max_attempts"]),
            "enabled": bool(row["enabled"]),
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
            "revoked_at": _iso(row["revoked_at"]),
        }

    @staticmethod
    def _app_delivery_dict(row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        return {
            "event_id": str(row["event_id"]),
            "callback_id": str(row["callback_id"]),
            "installation_id": str(row["installation_id"]),
            "user_id": str(row["user_id"]),
            "run_id": str(row["run_id"]),
            "event_type": str(row["event_type"]),
            "payload": dict(row["payload"]),
            "status": str(row["status"]),
            "attempt": int(row["attempt"]),
            "max_attempts": int(row["max_attempts"]),
            "available_at": _iso(row["available_at"]),
            "lease_owner": row["lease_owner"],
            "lease_version": int(row["lease_version"]),
            "response_status": row["response_status"],
            "last_error": row["last_error"],
            "replay_of_event_id": row.get("replay_of_event_id"),
            "replay_sequence": int(row.get("replay_sequence") or 0),
            "endpoint": str(row.get("endpoint") or ""),
            "secret_ref": str(row.get("secret_ref") or ""),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
            "delivered_at": _iso(row["delivered_at"]),
        }


__all__ = ["PostgresAppCallbackStoreMixin"]
