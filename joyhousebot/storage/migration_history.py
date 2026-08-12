"""Immutable PostgreSQL migration history helpers."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

HISTORY_DDL = """CREATE TABLE IF NOT EXISTS schema_migration_history (
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (name, version)
)"""


def migration_checksum(ddl: str) -> str:
    """Return the stable checksum of one migration's DDL script."""
    return sha256(ddl.encode("utf-8")).hexdigest()


def record_migration(
    conn: Any,
    *,
    name: str,
    version: int,
    ddl: str,
    description: str = "",
) -> None:
    """Record an applied migration and fail closed when its checksum drifts."""
    conn.execute(HISTORY_DDL)
    checksum = migration_checksum(ddl)
    row = conn.execute(
        "SELECT checksum FROM schema_migration_history WHERE name=%s AND version=%s",
        (name, version),
    ).fetchone()
    if row is None:
        conn.execute(
            """INSERT INTO schema_migration_history(name,version,checksum,description)
               VALUES (%s,%s,%s,%s)""",
            (name, version, checksum, description),
        )
        return
    if str(row["checksum"]) == checksum:
        return
    raise RuntimeError(
        "schema migration "
        f"{name}@{version} checksum changed "
        f"({str(row['checksum'])[:12]} -> {checksum[:12]}): "
        "recorded migrations are immutable; add a new migration version"
    )


def migration_is_recorded(
    conn: Any,
    *,
    name: str,
    version: int,
    ddl: str,
    description: str = "",
) -> bool:
    """Return whether an immutable migration exists, validating its checksum."""
    conn.execute(HISTORY_DDL)
    row = conn.execute(
        "SELECT 1 AS recorded FROM schema_migration_history WHERE name=%s AND version=%s",
        (name, version),
    ).fetchone()
    if row is None:
        return False
    record_migration(
        conn,
        name=name,
        version=version,
        ddl=ddl,
        description=description,
    )
    return True


__all__ = ["migration_checksum", "migration_is_recorded", "record_migration"]
