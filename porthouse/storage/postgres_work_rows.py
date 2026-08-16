"""Small row and content helpers for the PostgreSQL Work repository."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any


def content_hash(content: Any, uri: str | None) -> str:
    value = content if content is not None else {"uri": uri or ""}
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def share_row(row: Any) -> dict[str, Any]:
    from porthouse.storage.postgres_store import _iso

    return {
        "share_id": str(row["share_id"]),
        "work_id": str(row["work_id"]),
        "version": int(row["version"]) if row["version"] else None,
        "permission": str(row["permission"]),
        "status": str(row["status"]),
        "created_by": str(row["created_by"]),
        "created_at": _iso(row["created_at"]),
        "expires_at": _iso(row["expires_at"]),
        "revoked_at": _iso(row["revoked_at"]),
        "revoked_by": str(row["revoked_by"]) if row["revoked_by"] else None,
    }
