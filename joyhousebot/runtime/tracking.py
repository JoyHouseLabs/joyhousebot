"""Request identity propagation and append-only persistent trace events."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from joyhousebot.storage.contracts import TraceStorePort

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "token",
}
_CREDENTIAL_VALUE_PATTERNS = (
    re.compile(r"Bearer\s+\S+"),
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"api[_-]?key[\"'\s:=]+\S+", re.IGNORECASE),
)
_logger = logging.getLogger(__name__)


def redact_sensitive_text(value: str) -> str:
    """Mask credential-shaped substrings embedded in an otherwise safe string."""
    for pattern in _CREDENTIAL_VALUE_PATTERNS:
        value = pattern.sub("***REDACTED***", value)
    return value


@dataclass(frozen=True, slots=True)
class RequestTrackingContext:
    tracker_id: str
    request_id: str
    parent_request_id: str | None = None
    user_id: str | None = None
    run_id: str | None = None
    store: TraceStorePort | None = None


def new_request_id(prefix: str = "req") -> str:
    return f"{prefix}_{uuid4().hex}"


def normalize_request_id(value: Any, *, prefix: str = "req") -> str:
    candidate = str(value or "").strip()
    return candidate if _ID_PATTERN.fullmatch(candidate) else new_request_id(prefix)


def ensure_tracking_ids(
    *, request_id: Any = None, tracker_id: Any = None, request_prefix: str = "req"
) -> tuple[str, str]:
    request = normalize_request_id(request_id, prefix=request_prefix)
    tracker_candidate = str(tracker_id or "").strip()
    tracker = tracker_candidate if _ID_PATTERN.fullmatch(tracker_candidate) else request
    return request, tracker


def get_request_tracking() -> RequestTrackingContext | None:
    """Project the active immutable RunContext into outbound tracking."""
    from joyhousebot.runtime.context import get_current_run_context

    run = get_current_run_context()
    if run is None or not run.request_id or not run.tracker_id:
        return None
    return RequestTrackingContext(
        tracker_id=run.tracker_id,
        request_id=run.request_id,
        parent_request_id=run.parent_request_id,
        user_id=run.user_id,
        run_id=run.run_id,
        store=run.trace_store,
    )


def safe_trace_data(value: Any, *, max_bytes: int = 32_768) -> dict[str, Any]:
    """Redact secrets and cap persisted diagnostic payload size."""

    def clean(item: Any, depth: int = 0) -> Any:
        if depth >= 6:
            return "<max-depth>"
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            for key, nested in item.items():
                name = str(key)
                result[name] = (
                    "<redacted>" if name.lower() in _SENSITIVE_KEYS else clean(nested, depth + 1)
                )
            return result
        if isinstance(item, (list, tuple)):
            return [clean(nested, depth + 1) for nested in item[:100]]
        if item is None or isinstance(item, (str, int, float, bool)):
            if isinstance(item, str):
                item = redact_sensitive_text(item)
                if len(item) > 4096:
                    return item[:4096] + "…"
            return item
        return str(item)

    cleaned = clean(value if isinstance(value, dict) else {"value": value})
    encoded = json.dumps(cleaned, ensure_ascii=False, default=str).encode("utf-8")
    if len(encoded) <= max_bytes:
        return cleaned
    return {
        "truncated": True,
        "size_bytes": len(encoded),
        "preview": encoded[: max(0, max_bytes - 128)].decode("utf-8", errors="ignore"),
    }


def append_trace_event(
    *,
    store: TraceStorePort | None = None,
    tracker_id: str | None = None,
    request_id: str | None = None,
    parent_request_id: str | None = None,
    user_id: str | None = None,
    run_id: str | None = None,
    transport: str,
    direction: str,
    operation: str,
    stage: str,
    status: str | None = None,
    data: Any = None,
) -> Any:
    context = get_request_tracking()
    target_store = store or (context.store if context else None)
    if target_store is None or not hasattr(target_store, "append_request_trace_event"):
        return None
    resolved_request, resolved_tracker = ensure_tracking_ids(
        request_id=request_id or (context.request_id if context else None),
        tracker_id=tracker_id or (context.tracker_id if context else None),
    )
    return target_store.append_request_trace_event(
        tracker_id=resolved_tracker,
        request_id=resolved_request,
        parent_request_id=parent_request_id or (context.parent_request_id if context else None),
        user_id=user_id or (context.user_id if context else None),
        run_id=run_id or (context.run_id if context else None),
        transport=transport,
        direction=direction,
        operation=operation,
        stage=stage,
        status=status,
        data=safe_trace_data(data or {}),
    )


async def append_trace_event_async(**kwargs: Any) -> Any:
    try:
        return await asyncio.to_thread(append_trace_event, **kwargs)
    except Exception:
        # Observability must never turn a healthy user request into a failure.
        _logger.exception("failed to persist request trace event")
        return None
