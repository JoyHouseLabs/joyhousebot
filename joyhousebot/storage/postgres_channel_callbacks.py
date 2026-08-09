"""Transactional Channel delivery projections driven by terminal Runs."""

from __future__ import annotations

import json
from typing import Any

from joyhousebot.storage.json_codec import Jsonb

_DB_NOW_MS = "(EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::bigint"


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value or {})


def project_channel_run_terminal(
    connection: Any,
    *,
    run_id: str,
    user_id: str,
    status: str,
    options: dict[str, Any] | str | None,
    result: dict[str, Any] | None,
    error: dict[str, Any] | None,
) -> str | None:
    """Insert exactly one reply in the same transaction as the Run terminal state.

    Channel ingress freezes its delivery target into private Run metadata. If a
    Run does not originate from a Channel, this projection is a no-op. A Channel
    Run fails its terminal transaction when the durable outbox schema is absent;
    silently committing a result that can no longer be delivered is unsafe.
    """
    option_data = _object(options)
    metadata = _object(option_data.get("metadata"))
    delivery = _object(metadata.get("_runtime_channel_delivery"))
    if not delivery:
        return None
    if status == "cancelled" and not bool(delivery.get("deliver_cancelled")):
        return None

    table = connection.execute(
        "SELECT to_regclass('channel_outbox') AS table_name"
    ).fetchone()
    if not table or not table["table_name"]:
        raise RuntimeError("channel outbox is unavailable for a Channel Run")

    channel = str(delivery.get("channel") or "").strip()
    chat_id = str(delivery.get("chat_id") or "").strip()
    if not channel or not chat_id:
        raise RuntimeError("frozen Channel delivery target is incomplete")

    result_data = dict(result or {})
    error_data = dict(error or {})
    if status == "completed":
        content = str(result_data.get("content") or "")
    else:
        content = str(
            delivery.get("failure_message")
            or "Sorry, I couldn't complete that request. Please try again."
        )
    outbound_id = f"run-reply:{run_id}"
    outbound_metadata = {
        **_object(delivery.get("metadata")),
        "user_id": user_id,
        "run_id": run_id,
        "run_status": status,
        "request_id": delivery.get("request_id") or option_data.get("request_id"),
        "tracker_id": delivery.get("tracker_id") or option_data.get("tracker_id"),
    }
    if error_data:
        # Keep delivery metadata safe and bounded; the full error remains on the Run.
        outbound_metadata["error_code"] = error_data.get("code")
    connection.execute(
        f"""INSERT INTO channel_outbox
               (outbound_id,user_id,channel,chat_id,content,reply_to,media,metadata,
                request_id,tracker_id,status,attempt,available_at_ms,lease_version,
                created_at_ms,updated_at_ms)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',0,
                   {_DB_NOW_MS},0,{_DB_NOW_MS},{_DB_NOW_MS})
           ON CONFLICT(outbound_id) DO NOTHING""",
        (
            outbound_id,
            user_id,
            channel,
            chat_id,
            content,
            delivery.get("reply_to"),
            Jsonb(list(delivery.get("media") or [])),
            Jsonb(outbound_metadata),
            outbound_metadata.get("request_id"),
            outbound_metadata.get("tracker_id"),
        ),
    )
    return outbound_id
