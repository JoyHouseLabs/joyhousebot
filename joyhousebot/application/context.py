"""Authenticated request identity shared by every application service."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from joyhousebot.utils.permissions import permission_granted


class PrincipalKind(StrEnum):
    """Non-overlapping authority classes accepted by Runtime boundaries."""

    OWNER = "owner"
    INSTALLATION = "installation"
    HOST = "host"
    OPERATOR = "operator"


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    user_id: str | None
    kind: PrincipalKind = PrincipalKind.OWNER
    role: str = "user"
    permissions: tuple[str, ...] = ()
    token_scopes: tuple[str, ...] = ("*",)
    token_type: str = "session"
    app_client_id: str | None = None
    app_grant_id: str | None = None
    app_installation_id: str | None = None
    owner_client_id: str | None = None
    owner_delegation_id: str | None = None
    # The authenticated administrator behind a user-scoped request.  This is
    # intentionally separate from ``user_id``: application services scope
    # personal data by ``user_id``, while audit records keep using ``subject``
    # and can expose the human operator through this field.
    actor_user_id: str | None = None

    def can(self, permission: str) -> bool:
        # Shared grant semantics (exact + "namespace.*" + "*") live in
        # joyhousebot.utils.permissions; "operator" short-circuits as before.
        if self.role == "operator":
            return True
        return any(permission_granted(grant, permission) for grant in self.permissions)

    def allows_scope(self, scope: str) -> bool:
        """Return whether the credential is allowed to call an API operation."""
        return any(permission_granted(grant, scope) for grant in self.token_scopes)

    @property
    def is_public_actor(self) -> bool:
        return self.kind in {PrincipalKind.OWNER, PrincipalKind.INSTALLATION}


@dataclass(frozen=True, slots=True)
class RequestContext:
    principal: Principal
    request_id: str
    idempotency_key: str | None = None
    tracker_id: str | None = None
    traceparent: str | None = None
    tracestate: str | None = None

    @property
    def user_id(self) -> str:
        if not self.principal.user_id:
            raise PermissionError("a user-scoped principal is required")
        return self.principal.user_id
