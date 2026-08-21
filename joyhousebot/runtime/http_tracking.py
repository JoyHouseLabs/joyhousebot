"""HTTP clients that propagate and persist the current request tracking context."""

from __future__ import annotations

from typing import Any

import httpx

from joyhousebot.runtime.tracking import (
    append_trace_event_async,
    get_request_tracking,
    new_request_id,
)

_EXTENSION_KEY = "joyhousebot.request_tracking"


def _operation(request: httpx.Request) -> str:
    return f"{request.method} {request.url.host or ''}{request.url.path}"


def _prepare_request(
    request: httpx.Request, *, propagate_headers: bool = True
) -> dict[str, Any] | None:
    context = get_request_tracking()
    request_id = new_request_id("http")
    tracker_id = context.tracker_id if context else request_id
    state = {
        "store": context.store if context else None,
        "tracker_id": tracker_id,
        "request_id": request_id,
        "parent_request_id": context.request_id if context else None,
        "user_id": context.user_id if context else None,
        "run_id": context.run_id if context else None,
    }
    request.extensions[_EXTENSION_KEY] = state
    if propagate_headers:
        request.headers["X-Tracker-ID"] = tracker_id
        request.headers["X-Request-ID"] = request_id
    return state


def _event_kwargs(
    request: httpx.Request,
    *,
    direction: str,
    stage: str,
    status: str,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    state = request.extensions.get(_EXTENSION_KEY)
    if not isinstance(state, dict):
        return None
    return {
        **state,
        "transport": "http-client",
        "direction": direction,
        "operation": _operation(request),
        "stage": stage,
        "status": status,
        "data": data,
    }


async def _async_request_hook(request: httpx.Request, *, propagate_headers: bool = True) -> None:
    state = _prepare_request(request, propagate_headers=propagate_headers)
    if state is None:
        return
    kwargs = _event_kwargs(
        request,
        direction="outbound",
        stage="request",
        status="sent",
        data={"method": request.method, "host": request.url.host, "path": request.url.path},
    )
    if kwargs:
        await append_trace_event_async(**kwargs)


async def _async_response_hook(response: httpx.Response) -> None:
    kwargs = _event_kwargs(
        response.request,
        direction="inbound",
        stage="response",
        status=str(response.status_code),
        data={"status_code": response.status_code},
    )
    if kwargs:
        await append_trace_event_async(**kwargs)


class TrackedAsyncClient(httpx.AsyncClient):
    """Drop-in AsyncClient with per-request tracking propagation.

    propagate_headers controls whether X-Tracker-ID / X-Request-ID headers are
    attached to outgoing requests. Keep the default (True) for internal /
    platform endpoints (e.g. LLM provider base URLs); pass False for requests
    to arbitrary user-supplied third-party URLs so internal tracking IDs are
    not leaked (trace events are still recorded locally either way).
    """

    def __init__(self, *args: Any, propagate_headers: bool = True, **kwargs: Any) -> None:
        hooks = dict(kwargs.pop("event_hooks", {}) or {})

        async def _request_hook(request: httpx.Request) -> None:
            await _async_request_hook(request, propagate_headers=propagate_headers)

        hooks["request"] = [_request_hook, *list(hooks.get("request") or [])]
        hooks["response"] = [_async_response_hook, *list(hooks.get("response") or [])]
        super().__init__(*args, event_hooks=hooks, **kwargs)

    async def send(self, request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
        try:
            return await super().send(request, *args, **kwargs)
        except BaseException as exc:
            event = _event_kwargs(
                request,
                direction="inbound",
                stage="error",
                status="failed",
                data={"error_type": type(exc).__name__, "message": str(exc)},
            )
            if event:
                await append_trace_event_async(**event)
            raise
