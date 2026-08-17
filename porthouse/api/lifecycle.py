"""Lifecycle metadata for every documented HTTP operation."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute


class ApiLifecycle(StrEnum):
    STABLE = "stable"
    EXPERIMENTAL = "experimental"
    EXTENSION_ONLY = "extension-only"
    INCUBATING = "incubating"


TAG_LIFECYCLE: dict[str, ApiLifecycle] = {
    "system": ApiLifecycle.STABLE,
    "administrator-auth": ApiLifecycle.STABLE,
    "platform-admin": ApiLifecycle.STABLE,
    "platform-catalog": ApiLifecycle.STABLE,
    "skills": ApiLifecycle.STABLE,
    "runs": ApiLifecycle.STABLE,
    "sessions": ApiLifecycle.STABLE,
    "schedules": ApiLifecycle.STABLE,
    "action-items": ApiLifecycle.STABLE,
    "knowledge": ApiLifecycle.STABLE,
    "memory": ApiLifecycle.STABLE,
    "apps": ApiLifecycle.EXPERIMENTAL,
    "app-auth": ApiLifecycle.EXPERIMENTAL,
    "app-packs": ApiLifecycle.EXPERIMENTAL,
    "works": ApiLifecycle.EXPERIMENTAL,
    "workflows": ApiLifecycle.EXPERIMENTAL,
    "automation": ApiLifecycle.EXPERIMENTAL,
    "input-assets": ApiLifecycle.EXPERIMENTAL,
    "artifact-uploads": ApiLifecycle.EXPERIMENTAL,
    "run-events": ApiLifecycle.EXPERIMENTAL,
    "device-hosts": ApiLifecycle.EXTENSION_ONLY,
    "host-tools": ApiLifecycle.EXTENSION_ONLY,
    "host-model-grants": ApiLifecycle.EXTENSION_ONLY,
    "plugin-control-plane": ApiLifecycle.EXTENSION_ONLY,
    "remote-capability-connections": ApiLifecycle.EXTENSION_ONLY,
    "agent-teams": ApiLifecycle.INCUBATING,
    "evaluations": ApiLifecycle.INCUBATING,
    "experiments": ApiLifecycle.INCUBATING,
    "model-provider-control-plane": ApiLifecycle.INCUBATING,
    "embedding-profile-control-plane": ApiLifecycle.INCUBATING,
    "prompts": ApiLifecycle.INCUBATING,
    "scenario-studio": ApiLifecycle.INCUBATING,
}


def _api_routes(routes: Iterable[Any]) -> Iterable[APIRoute]:
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _api_routes(included.routes)


def annotate_api_lifecycle(app: FastAPI) -> None:
    """Expose lifecycle state in OpenAPI and reject unclassified endpoints."""
    for route in _api_routes(app.routes):
        if not route.include_in_schema:
            continue
        states = {TAG_LIFECYCLE[tag] for tag in route.tags if tag in TAG_LIFECYCLE}
        if len(states) != 1:
            raise RuntimeError(
                f"API route {route.path} must have exactly one lifecycle-classified tag"
            )
        lifecycle = states.pop()
        route.openapi_extra = {
            **(route.openapi_extra or {}),
            "x-porthouse-lifecycle": lifecycle.value,
        }


__all__ = ["ApiLifecycle", "TAG_LIFECYCLE", "annotate_api_lifecycle"]
