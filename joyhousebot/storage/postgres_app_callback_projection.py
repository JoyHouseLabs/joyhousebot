"""Transactional App callback projection driven by terminal Runs."""

from __future__ import annotations

import json
from typing import Any

from joyhousebot.storage.json_codec import Jsonb


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value or {})


def project_app_run_terminal(
    connection: Any,
    *,
    run_id: str,
    user_id: str,
    status: str,
    options: dict[str, Any] | str | None,
    error: dict[str, Any] | None,
) -> list[str]:
    metadata = _object(_object(options).get("metadata"))
    app = _object(metadata.get("app"))
    installation_id = str(app.get("installation_id") or "")
    if not installation_id:
        return []
    event_type = f"run.{status}"
    callbacks = connection.execute(
        """SELECT * FROM app_callbacks
           WHERE installation_id=%s AND user_id=%s AND enabled AND events ? %s""",
        (installation_id, user_id, event_type),
    ).fetchall()
    event_ids: list[str] = []
    for callback in callbacks:
        event_id = f"appcb:{callback['callback_id']}:{run_id}:{event_type}"
        payload = {
            "schema_version": 1,
            "event_id": event_id,
            "event_type": event_type,
            "run": {
                "run_id": run_id,
                "status": status,
                "location": f"/v2/runs/{run_id}",
                "error_code": str((error or {}).get("code") or "") or None,
            },
            "app": {
                "installation_id": installation_id,
                "app_id": app.get("app_id"),
                "version": app.get("version"),
                "entrypoint_id": app.get("entrypoint_id"),
            },
        }
        inserted = connection.execute(
            """INSERT INTO app_callback_outbox
                   (event_id,callback_id,installation_id,user_id,run_id,event_type,
                    payload,max_attempts)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(event_id) DO NOTHING RETURNING event_id""",
            (
                event_id,
                callback["callback_id"],
                installation_id,
                user_id,
                run_id,
                event_type,
                Jsonb(payload),
                callback["max_attempts"],
            ),
        ).fetchone()
        if inserted is not None:
            event_ids.append(event_id)
    return event_ids


__all__ = ["project_app_run_terminal"]
