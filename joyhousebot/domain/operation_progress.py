"""Validation limits for durable external-operation progress observations."""

from __future__ import annotations

import json
import re
from typing import Iterable

from joyhousebot.contracts import OperationProgressEvent

MAX_OPERATION_EVENTS_PER_BATCH = 100
MAX_OPERATION_EVENT_PAYLOAD_BYTES = 32_768
MAX_OPERATION_EVENT_BATCH_BYTES = 262_144
MAX_OPERATION_EVENTS_RETAINED = 10_000

_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


def validated_operation_events(
    values: Iterable[OperationProgressEvent],
) -> tuple[OperationProgressEvent, ...]:
    """Return a frozen batch after enforcing protocol and storage bounds."""
    events = tuple(values)
    if len(events) > MAX_OPERATION_EVENTS_PER_BATCH:
        raise ValueError(
            f"operation event batch exceeds {MAX_OPERATION_EVENTS_PER_BATCH} events"
        )
    seen_ids: set[str] = set()
    seen_sequences: set[int] = set()
    batch_bytes = 0
    for event in events:
        if not _EVENT_TYPE.fullmatch(event.event_type):
            raise ValueError("operation progress event_type is invalid")
        if event.event_id in seen_ids or event.sequence in seen_sequences:
            raise ValueError("operation event batch contains duplicate identity")
        seen_ids.add(event.event_id)
        seen_sequences.add(event.sequence)
        raw = json.dumps(
            event.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(raw) > MAX_OPERATION_EVENT_PAYLOAD_BYTES:
            raise ValueError(
                f"operation event payload exceeds {MAX_OPERATION_EVENT_PAYLOAD_BYTES} bytes"
            )
        batch_bytes += len(raw) + len(event.summary.encode("utf-8"))
    if batch_bytes > MAX_OPERATION_EVENT_BATCH_BYTES:
        raise ValueError(
            f"operation event batch exceeds {MAX_OPERATION_EVENT_BATCH_BYTES} bytes"
        )
    return events
