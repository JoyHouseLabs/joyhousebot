"""Use cases for independent App clients and owner-approved delegation."""

from __future__ import annotations

import asyncio
from typing import Any

from joyhousebot.application.context import RequestContext
from joyhousebot.application.errors import AuthorizationError, NotFoundError, ValidationError


class AppDelegationService:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def create_client(
        self,
        *,
        app_id: str,
        name: str,
        allowed_scopes: list[str],
        actor_id: str,
    ) -> dict[str, Any]:
        try:
            record, secret = await asyncio.to_thread(
                self.store.create_app_client,
                app_id=app_id,
                name=name,
                allowed_scopes=allowed_scopes,
                actor_id=actor_id,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return {**record, "client_secret": secret}

    async def list_clients(self, *, app_id: str | None = None) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.store.list_app_clients, app_id=app_id)

    async def rotate_client_secret(
        self, client_id: str, *, actor_id: str
    ) -> dict[str, Any]:
        result = await asyncio.to_thread(
            self.store.rotate_app_client_secret,
            client_id,
            actor_id=actor_id,
        )
        if result is None:
            raise NotFoundError("active App client not found")
        record, secret = result
        return {**record, "client_secret": secret}

    async def revoke_client(self, client_id: str, *, actor_id: str) -> None:
        if not await asyncio.to_thread(
            self.store.revoke_app_client, client_id, actor_id=actor_id
        ):
            raise NotFoundError("App client not found or already revoked")

    async def authorize(
        self,
        context: RequestContext,
        installation_id: str,
        *,
        client_id: str,
        scopes: list[str],
        expires_at: str,
    ) -> dict[str, Any]:
        if context.principal.app_client_id:
            raise AuthorizationError("delegated App credentials cannot authorize another grant")
        try:
            return await asyncio.to_thread(
                self.store.create_app_delegation_grant,
                client_id=client_id,
                installation_id=installation_id,
                user_id=context.user_id,
                scopes=scopes,
                expires_at=expires_at,
                actor_id=context.principal.subject,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    async def list_grants(
        self, context: RequestContext, installation_id: str
    ) -> list[dict[str, Any]]:
        if context.principal.app_client_id:
            raise AuthorizationError("delegated App credentials cannot inspect owner grants")
        return await asyncio.to_thread(
            self.store.list_app_delegation_grants,
            installation_id=installation_id,
            user_id=context.user_id,
        )

    async def revoke_grant(self, context: RequestContext, grant_id: str) -> None:
        if context.principal.app_client_id:
            raise AuthorizationError("delegated App credentials cannot revoke owner grants")
        if not await asyncio.to_thread(
            self.store.revoke_app_delegation_grant,
            grant_id,
            user_id=context.user_id,
            actor_id=context.principal.subject,
        ):
            raise NotFoundError("App delegation grant not found or already revoked")

    async def exchange(
        self,
        *,
        client_id: str,
        client_secret: str,
        grant_id: str,
        scopes: list[str],
        ttl_seconds: int,
    ) -> dict[str, Any] | None:
        try:
            result = await asyncio.to_thread(
                self.store.issue_app_delegated_token,
                client_id=client_id,
                client_secret=client_secret,
                grant_id=grant_id,
                requested_scopes=scopes,
                ttl_seconds=ttl_seconds,
            )
        except ValueError:
            return None
        if result is None:
            return None
        record, token = result
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_at": record["expires_at"],
            "scopes": record["scopes"],
            "installation_id": record["app_installation_id"],
        }


__all__ = ["AppDelegationService"]
