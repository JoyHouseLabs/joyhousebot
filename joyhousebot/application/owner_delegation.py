"""Use cases for first-party products acting with explicit Owner authority."""

from __future__ import annotations

import asyncio
from typing import Any

from joyhousebot.application.context import RequestContext
from joyhousebot.application.errors import AuthorizationError, NotFoundError, ValidationError
from joyhousebot.domain.owner_delegation import verify_owner_assertion


class OwnerDelegationService:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def create_client(self, **values: Any) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(self.store.create_owner_client, **values)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    async def list_clients(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.store.list_owner_clients)

    async def rotate_client_key(
        self,
        client_id: str,
        *,
        public_key_pem: str,
        algorithm: str,
        actor_id: str,
    ) -> dict[str, Any]:
        try:
            result = await asyncio.to_thread(
                self.store.rotate_owner_client_key,
                client_id,
                public_key_pem=public_key_pem,
                algorithm=algorithm,
                actor_id=actor_id,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if result is None:
            raise NotFoundError("active Owner client not found")
        return result

    async def update_client(
        self,
        client_id: str,
        *,
        name: str,
        issuer: str,
        public_key_pem: str,
        algorithm: str,
        allowed_scopes: list[str],
        actor_id: str,
    ) -> dict[str, Any]:
        try:
            result = await asyncio.to_thread(
                self.store.update_owner_client,
                client_id,
                name=name,
                issuer=issuer,
                public_key_pem=public_key_pem,
                algorithm=algorithm,
                allowed_scopes=allowed_scopes,
                actor_id=actor_id,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if result is None:
            raise NotFoundError("active Owner client not found")
        return result

    async def revoke_client(self, client_id: str, *, actor_id: str) -> None:
        if not await asyncio.to_thread(
            self.store.revoke_owner_client, client_id, actor_id=actor_id
        ):
            raise NotFoundError("Owner client not found or already revoked")

    async def exchange(
        self,
        *,
        client_id: str,
        subject_token: str,
        scopes: list[str],
        ttl_seconds: int,
        refresh_ttl_seconds: int,
    ) -> dict[str, Any] | None:
        client = await asyncio.to_thread(
            self.store.get_owner_client_for_exchange, client_id
        )
        if client is None:
            return None
        try:
            claims = verify_owner_assertion(
                subject_token,
                public_key_pem=client["public_key_pem"],
                algorithm=client["algorithm"],
                issuer=client["issuer"],
            )
            result = await asyncio.to_thread(
                self.store.issue_owner_delegated_token,
                client_id=client_id,
                user_id=claims["user_id"],
                assertion_jti=claims["jti"],
                assertion_expires_at=claims["expires_at"],
                requested_scopes=scopes,
                ttl_seconds=ttl_seconds,
                refresh_ttl_seconds=refresh_ttl_seconds,
            )
        except ValueError:
            return None
        return self._token_response(result)

    async def refresh(
        self,
        *,
        client_id: str,
        refresh_token: str,
        ttl_seconds: int,
        refresh_ttl_seconds: int,
    ) -> dict[str, Any] | None:
        result = await asyncio.to_thread(
            self.store.refresh_owner_delegated_token,
            client_id=client_id,
            refresh_token=refresh_token,
            ttl_seconds=ttl_seconds,
            refresh_ttl_seconds=refresh_ttl_seconds,
        )
        return self._token_response(result)

    async def revoke(self, context: RequestContext) -> None:
        client_id = context.principal.owner_client_id
        if not client_id or context.principal.app_installation_id:
            raise AuthorizationError("Owner delegation credential required")
        if not await asyncio.to_thread(
            self.store.revoke_owner_delegation,
            client_id=client_id,
            user_id=context.user_id,
            actor_id=context.principal.subject,
        ):
            raise NotFoundError("active Owner delegation not found")

    @staticmethod
    def _token_response(result: Any) -> dict[str, Any] | None:
        if result is None:
            return None
        record, access, refresh = result
        return {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "bearer",
            "expires_at": record["expires_at"],
            "refresh_expires_at": record["refresh_expires_at"],
            "scopes": record["scopes"],
        }


__all__ = ["OwnerDelegationService"]
