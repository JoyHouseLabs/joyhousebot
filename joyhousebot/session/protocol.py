"""Session persistence contract consumed by the shared Agent engine."""

from __future__ import annotations

from typing import Protocol

from joyhousebot.session.models import Session


class SessionStore(Protocol):
    def get_or_create(self, key: str) -> Session: ...

    def save(self, session: Session) -> None: ...

    def invalidate(self, key: str) -> None: ...

    def delete(self, key: str) -> bool: ...

    def list_sessions(self) -> list[dict]: ...
