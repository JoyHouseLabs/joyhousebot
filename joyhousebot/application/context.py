"""Authenticated request identity shared by every application service."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    user_id: str | None
    role: str = "user"
    permissions: tuple[str, ...] = ()

    def can(self, permission: str) -> bool:
        return self.role == "operator" or "*" in self.permissions or permission in self.permissions


@dataclass(frozen=True, slots=True)
class RequestContext:
    principal: Principal
    request_id: str
    idempotency_key: str | None = None

    @property
    def user_id(self) -> str:
        if not self.principal.user_id:
            raise PermissionError("a user-scoped principal is required")
        return self.principal.user_id
