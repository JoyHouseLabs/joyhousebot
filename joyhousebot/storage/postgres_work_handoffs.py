"""Durable, version-pinned handoffs from Works to independent Apps."""

from __future__ import annotations

from typing import Any

from joyhousebot.storage.json_codec import Jsonb


class PostgresWorkHandoffStoreMixin:
    def migrate_work_handoffs(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS work_handoffs (
            handoff_id TEXT PRIMARY KEY,
            work_id TEXT NOT NULL REFERENCES works(work_id) ON DELETE CASCADE,
            work_version INTEGER NOT NULL,
            owner_user_id TEXT NOT NULL,
            installation_id TEXT NOT NULL,
            app_id TEXT NOT NULL,
            app_version TEXT NOT NULL,
            consumer_id TEXT NOT NULL,
            purpose TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'authorized',
            idempotency_key TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            accepted_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            cancelled_at TIMESTAMPTZ,
            cancelled_by TEXT,
            UNIQUE(owner_user_id,idempotency_key),
            FOREIGN KEY(work_id,work_version) REFERENCES work_versions(work_id,version),
            CHECK (status IN ('authorized','accepted','executing','verified','failed','cancelled'))
        );
        CREATE INDEX IF NOT EXISTS ix_work_handoffs_work
            ON work_handoffs(work_id,created_at DESC);
        CREATE INDEX IF NOT EXISTS ix_work_handoffs_installation
            ON work_handoffs(installation_id,status,created_at DESC);
        CREATE TABLE IF NOT EXISTS work_handoff_receipts (
            receipt_id TEXT PRIMARY KEY,
            handoff_id TEXT NOT NULL REFERENCES work_handoffs(handoff_id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            external_reference TEXT NOT NULL DEFAULT '',
            run_id TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(handoff_id,idempotency_key),
            CHECK (status IN ('accepted','executing','verified','failed'))
        );
        CREATE INDEX IF NOT EXISTS ix_work_handoff_receipts_handoff
            ON work_handoff_receipts(handoff_id,created_at DESC);
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="work_handoffs",
                version=1,
                ddl=ddl,
                description="version-pinned Work handoffs and App result receipts",
            )
            classification_ddl = """
            ALTER TABLE work_handoffs
                ADD COLUMN IF NOT EXISTS data_classification TEXT NOT NULL DEFAULT 'internal';
            """
            classification_recorded = self._migration_is_recorded(
                conn,
                name="work_handoffs",
                version=2,
                ddl=classification_ddl,
                description="freeze Work data classification at handoff authorization",
            )
            if not classification_recorded:
                conn.execute(classification_ddl)
            self._record_migration(
                conn,
                name="work_handoffs",
                version=2,
                ddl=classification_ddl,
                description="freeze Work data classification at handoff authorization",
            )

    @staticmethod
    def _handoff(row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        return {
            "handoff_id": str(row["handoff_id"]),
            "work_id": str(row["work_id"]),
            "work_version": int(row["work_version"]),
            "owner_user_id": str(row["owner_user_id"]),
            "installation_id": str(row["installation_id"]),
            "app_id": str(row["app_id"]),
            "app_version": str(row["app_version"]),
            "consumer_id": str(row["consumer_id"]),
            "purpose": str(row["purpose"]),
            "data_classification": str(row["data_classification"]),
            "status": str(row["status"]),
            "content_sha256": str(row["content_sha256"]),
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
            "accepted_at": _iso(row["accepted_at"]),
            "completed_at": _iso(row["completed_at"]),
            "cancelled_at": _iso(row["cancelled_at"]),
            "cancelled_by": row["cancelled_by"],
        }

    @staticmethod
    def _receipt(row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        return {
            "receipt_id": str(row["receipt_id"]),
            "handoff_id": str(row["handoff_id"]),
            "status": str(row["status"]),
            "external_reference": str(row["external_reference"]),
            "run_id": str(row["run_id"]) or None,
            "summary": str(row["summary"]),
            "details": dict(row["details"] or {}),
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
        }

    def create_work_handoff(self, *, value: dict[str, Any]) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            existing = conn.execute(
                """SELECT * FROM work_handoffs WHERE owner_user_id=%s
                   AND idempotency_key=%s FOR UPDATE""",
                (value["owner_user_id"], value["idempotency_key"]),
            ).fetchone()
            if existing is not None:
                expected = (
                    value["work_id"],
                    int(value["work_version"]),
                    value["installation_id"],
                    value["consumer_id"],
                    value["purpose"],
                )
                actual = (
                    str(existing["work_id"]),
                    int(existing["work_version"]),
                    str(existing["installation_id"]),
                    str(existing["consumer_id"]),
                    str(existing["purpose"]),
                )
                if actual != expected:
                    raise ValueError("work handoff idempotency key conflicts with another request")
                return self._handoff(existing)
            version = conn.execute(
                """SELECT work.owner_user_id,work.status,work.data_classification,
                          version.content_sha256
                   FROM works work JOIN work_versions version
                     ON version.work_id=work.work_id AND version.version=%s
                   WHERE work.work_id=%s FOR UPDATE""",
                (value["work_version"], value["work_id"]),
            ).fetchone()
            if (
                version is None
                or str(version["owner_user_id"]) != value["owner_user_id"]
                or str(version["status"]) == "archived"
            ):
                raise ValueError("active owner Work version not found")
            row = conn.execute(
                """INSERT INTO work_handoffs
                       (handoff_id,work_id,work_version,owner_user_id,installation_id,
                        app_id,app_version,consumer_id,purpose,idempotency_key,
                        content_sha256,data_classification,created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    value["handoff_id"],
                    value["work_id"],
                    value["work_version"],
                    value["owner_user_id"],
                    value["installation_id"],
                    value["app_id"],
                    value["app_version"],
                    value["consumer_id"],
                    value["purpose"],
                    value["idempotency_key"],
                    version["content_sha256"],
                    version["data_classification"],
                    value["created_by"],
                ),
            ).fetchone()
            self._work_audit(
                conn,
                audit_id=value["audit_id"],
                work_id=value["work_id"],
                version=int(value["work_version"]),
                event_type="handoff.authorized",
                actor_id=value["created_by"],
                data={
                    "handoff_id": value["handoff_id"],
                    "installation_id": value["installation_id"],
                    "app_id": value["app_id"],
                    "consumer_id": value["consumer_id"],
                    "purpose": value["purpose"],
                },
            )
            assert row is not None
            return self._handoff(row)

    def list_work_handoffs(
        self, work_id: str, *, expected_user_id: str
    ) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT handoff.* FROM work_handoffs handoff JOIN works work
                     ON work.work_id=handoff.work_id
                   WHERE handoff.work_id=%s AND work.owner_user_id=%s
                   ORDER BY handoff.created_at DESC""",
                (work_id, expected_user_id),
            ).fetchall()
        return [self._handoff(row) for row in rows]

    def read_work_handoff_input(
        self,
        handoff_id: str,
        *,
        expected_user_id: str,
        installation_id: str | None,
        actor_id: str,
        audit_id: str,
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.transaction():
            handoff = conn.execute(
                """SELECT * FROM work_handoffs WHERE handoff_id=%s AND owner_user_id=%s
                   FOR UPDATE""",
                (handoff_id, expected_user_id),
            ).fetchone()
            if handoff is None or str(handoff["status"]) not in {
                "authorized",
                "accepted",
                "executing",
            }:
                return None
            if installation_id and str(handoff["installation_id"]) != installation_id:
                return None
            work = conn.execute(
                "SELECT * FROM works WHERE work_id=%s",
                (handoff["work_id"],),
            ).fetchone()
            if work is None:
                return None
            version = conn.execute(
                """SELECT * FROM work_versions WHERE work_id=%s AND version=%s""",
                (handoff["work_id"], handoff["work_version"]),
            ).fetchone()
            if version is None:
                return None
            self._work_audit(
                conn,
                audit_id=audit_id,
                work_id=str(handoff["work_id"]),
                version=int(handoff["work_version"]),
                event_type="handoff.input_accessed",
                actor_id=actor_id,
                data={"handoff_id": handoff_id, "installation_id": handoff["installation_id"]},
            )
            return {
                "handoff": self._handoff(handoff),
                "work": {
                    "work_id": str(work["work_id"]),
                    "title": str(work["title"]),
                    "description": str(work["description"]),
                    "data_classification": str(handoff["data_classification"]),
                    "version": self._version(version, include_content=True),
                },
            }

    def add_work_handoff_receipt(self, *, value: dict[str, Any]) -> dict[str, Any]:
        transitions = {
            "authorized": {"accepted", "executing", "failed"},
            "accepted": {"executing", "verified", "failed"},
            "executing": {"executing", "verified", "failed"},
        }
        with self._pool.connection() as conn, conn.transaction():
            handoff = conn.execute(
                """SELECT * FROM work_handoffs WHERE handoff_id=%s AND owner_user_id=%s
                   FOR UPDATE""",
                (value["handoff_id"], value["owner_user_id"]),
            ).fetchone()
            if handoff is None:
                return None
            installation_id = value.get("installation_id")
            if installation_id and str(handoff["installation_id"]) != installation_id:
                return None
            existing = conn.execute(
                """SELECT * FROM work_handoff_receipts WHERE handoff_id=%s
                   AND idempotency_key=%s""",
                (value["handoff_id"], value["idempotency_key"]),
            ).fetchone()
            if existing is not None:
                return {"handoff": self._handoff(handoff), "receipt": self._receipt(existing)}
            status = str(value["status"])
            if status not in transitions.get(str(handoff["status"]), set()):
                raise ValueError("work handoff cannot transition to this receipt status")
            receipt = conn.execute(
                """INSERT INTO work_handoff_receipts
                       (receipt_id,handoff_id,status,idempotency_key,external_reference,
                        run_id,summary,details,created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    value["receipt_id"],
                    value["handoff_id"],
                    status,
                    value["idempotency_key"],
                    value.get("external_reference", ""),
                    value.get("run_id", ""),
                    value.get("summary", ""),
                    Jsonb(value.get("details") or {}),
                    value["created_by"],
                ),
            ).fetchone()
            handoff = conn.execute(
                """UPDATE work_handoffs SET status=%s,updated_at=clock_timestamp(),
                       accepted_at=CASE WHEN %s IN ('accepted','executing','verified','failed')
                           THEN COALESCE(accepted_at,clock_timestamp()) ELSE accepted_at END,
                       completed_at=CASE WHEN %s IN ('verified','failed')
                           THEN clock_timestamp() ELSE completed_at END
                   WHERE handoff_id=%s RETURNING *""",
                (status, status, status, value["handoff_id"]),
            ).fetchone()
            self._work_audit(
                conn,
                audit_id=value["audit_id"],
                work_id=str(handoff["work_id"]),
                version=int(handoff["work_version"]),
                event_type=f"handoff.{status}",
                actor_id=value["created_by"],
                data={
                    "handoff_id": value["handoff_id"],
                    "receipt_id": value["receipt_id"],
                    "external_reference": value.get("external_reference", ""),
                    "run_id": value.get("run_id", ""),
                },
            )
            assert handoff is not None and receipt is not None
            return {"handoff": self._handoff(handoff), "receipt": self._receipt(receipt)}

    def cancel_work_handoff(
        self, handoff_id: str, *, expected_user_id: str, actor_id: str, audit_id: str
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.transaction():
            handoff = conn.execute(
                """UPDATE work_handoffs SET status='cancelled',cancelled_at=clock_timestamp(),
                       cancelled_by=%s,updated_at=clock_timestamp()
                   WHERE handoff_id=%s AND owner_user_id=%s
                     AND status IN ('authorized','accepted','executing') RETURNING *""",
                (actor_id, handoff_id, expected_user_id),
            ).fetchone()
            if handoff is None:
                return None
            self._work_audit(
                conn,
                audit_id=audit_id,
                work_id=str(handoff["work_id"]),
                version=int(handoff["work_version"]),
                event_type="handoff.cancelled",
                actor_id=actor_id,
                data={"handoff_id": handoff_id, "installation_id": handoff["installation_id"]},
            )
            return self._handoff(handoff)

    def list_work_handoff_receipts(
        self,
        handoff_id: str,
        *,
        expected_user_id: str,
        installation_id: str | None,
    ) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            clauses = ["receipt.handoff_id=%s", "handoff.owner_user_id=%s"]
            values: list[Any] = [handoff_id, expected_user_id]
            if installation_id:
                clauses.append("handoff.installation_id=%s")
                values.append(installation_id)
            rows = conn.execute(
                """SELECT receipt.* FROM work_handoff_receipts receipt
                     JOIN work_handoffs handoff ON handoff.handoff_id=receipt.handoff_id
                   WHERE """
                + " AND ".join(clauses)
                + " ORDER BY receipt.created_at DESC",
                values,
            ).fetchall()
        return [self._receipt(row) for row in rows]
