"""Business-neutral read-model contracts contributed by plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RunProjectionQueries(Protocol):
    """Read-only evidence queries scoped to one already-authorized Run.

    The framework binds ``run_id`` and ``user_id`` when it builds the
    projection context, so a provider can only ever read evidence for the Run
    it was invoked for — never another user's data and never a write path.
    Providers that need their own durable schema must own a private
    repository instead of reaching into core storage.
    """

    def list_events(self, *, after_sequence: int = 0, limit: int = 1000) -> list[Any]:
        """List runtime events for the authorized Run."""
        ...

    def list_artifacts(self) -> list[dict[str, Any]]:
        """List artifact descriptors for the authorized Run."""
        ...

    def list_invocations(self) -> list[Any]:
        """List capability invocation records for the authorized Run."""
        ...

    def get_scenario_state(self) -> Any | None:
        """Return the scenario/clarification state for the authorized Run."""
        ...


class ScopedRunProjectionQueries:
    """Framework-built adapter binding a store to one authorized Run.

    The wrapped store is deliberately private: providers typed against
    :class:`RunProjectionQueries` only see the run-scoped read methods.
    """

    __slots__ = ("_store", "_run_id", "_user_id")

    def __init__(self, store: Any, *, run_id: str, user_id: str) -> None:
        self._store = store
        self._run_id = run_id
        self._user_id = user_id

    def list_events(self, *, after_sequence: int = 0, limit: int = 1000) -> list[Any]:
        return self._store.list_runtime_events(
            self._run_id,
            after_sequence=after_sequence,
            limit=limit,
            user_id=self._user_id,
        )

    def list_artifacts(self) -> list[dict[str, Any]]:
        return self._store.list_runtime_artifacts(self._run_id)

    def list_invocations(self) -> list[Any]:
        return self._store.list_capability_invocations(
            self._run_id, expected_user_id=self._user_id
        )

    def get_scenario_state(self) -> Any | None:
        return self._store.get_run_scenario_state(
            self._run_id, expected_user_id=self._user_id
        )


@dataclass(frozen=True, slots=True)
class ProjectionContext:
    """Runtime evidence made available to one plugin projection provider.

    The framework owns authorization and record loading.  Providers receive
    opaque records and may build a business read model without importing the
    HTTP layer or reaching into another user's data.  ``queries`` exposes
    only run-scoped read queries; it is never the core store itself.
    """

    run: Any
    artifacts: tuple[dict[str, Any], ...] = ()
    events: tuple[Any, ...] = ()
    invocations: tuple[Any, ...] = ()
    scenario_state: Any | None = None
    queries: RunProjectionQueries | None = None
    user_id: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ProjectionProvider(Protocol):
    """A versioned, plugin-owned UI/read API projection."""

    view_id: str
    schema_version: int

    def build(self, context: ProjectionContext) -> dict[str, Any]:
        """Build a JSON-serializable projection from authorized evidence."""


__all__ = [
    "ProjectionContext",
    "ProjectionProvider",
    "RunProjectionQueries",
    "ScopedRunProjectionQueries",
]
