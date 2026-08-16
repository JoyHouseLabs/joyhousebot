"""Owner-managed App callbacks and durable signed delivery."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from porthouse.application.context import RequestContext
from porthouse.application.errors import AuthorizationError, NotFoundError, ValidationError
from porthouse.domain.app_callbacks import (
    callback_body,
    callback_signature,
    resolve_callback_secret,
)
from porthouse.utils.ssrf import SsrfProtectedTransport


class AppCallbackService:
    def __init__(self, store: Any) -> None:
        self.store = store

    @staticmethod
    def _require_owner(context: RequestContext) -> None:
        if context.principal.app_client_id:
            raise AuthorizationError("delegated App credentials cannot manage callbacks")

    async def register(
        self,
        context: RequestContext,
        installation_id: str,
        configuration: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_owner(context)
        try:
            return await asyncio.to_thread(
                self.store.save_app_callback,
                installation_id=installation_id,
                user_id=context.user_id,
                configuration=configuration,
                actor_id=context.principal.subject,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    async def list(
        self, context: RequestContext, installation_id: str
    ) -> list[dict[str, Any]]:
        self._require_owner(context)
        installation = await asyncio.to_thread(
            self.store.get_app_installation,
            installation_id,
            expected_user_id=context.user_id,
        )
        if installation is None:
            raise NotFoundError("App installation not found")
        return await asyncio.to_thread(
            self.store.list_app_callbacks,
            installation_id=installation_id,
            user_id=context.user_id,
        )

    async def revoke(
        self,
        context: RequestContext,
        installation_id: str,
        callback_id: str,
    ) -> None:
        self._require_owner(context)
        revoked = await asyncio.to_thread(
            self.store.revoke_app_callback,
            callback_id,
            installation_id=installation_id,
            user_id=context.user_id,
            actor_id=context.principal.subject,
        )
        if not revoked:
            raise NotFoundError("App callback not found or already revoked")

    async def list_run_deliveries(
        self, context: RequestContext, run_id: str
    ) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(
            self.store.list_run_app_callback_deliveries,
            run_id,
            user_id=context.user_id,
        )
        # The env reference is deployment configuration, not Run result data.
        # Keep it out of the public delivery view even for the owner.
        return [
            {key: value for key, value in row.items() if key != "secret_ref"}
            for row in rows
        ]

    async def replay(
        self,
        context: RequestContext,
        run_id: str,
        event_id: str,
    ) -> dict[str, Any]:
        self._require_owner(context)
        if not context.idempotency_key:
            raise ValidationError("callback replay requires an Idempotency-Key header")
        try:
            row = await asyncio.to_thread(
                self.store.replay_app_callback_delivery,
                event_id,
                run_id=run_id,
                user_id=context.user_id,
                actor_id=context.principal.subject,
                request_key=context.idempotency_key,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if row is None:
            raise NotFoundError("App callback delivery not found")
        return {key: value for key, value in row.items() if key != "secret_ref"}


class AppCallbackDispatcher:
    """Claim one outbox row, deliver it once, and persist the fenced outcome."""

    def __init__(
        self,
        store: Any,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.store = store
        self._client = client
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 60.0))

    async def process_next(self, *, worker_id: str) -> bool:
        delivery = await asyncio.to_thread(
            self.store.claim_app_callback_delivery,
            worker_id=worker_id,
            lease_seconds=max(30, int(self.timeout_seconds) + 15),
        )
        if delivery is None:
            return False
        body = callback_body(delivery["payload"])
        timestamp = str(int(time.time()))
        try:
            secret = resolve_callback_secret(delivery["secret_ref"])
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Porthouse-App-Callback/1",
                "Idempotency-Key": delivery["event_id"],
                "X-Porthouse-Event-ID": delivery["event_id"],
                "X-Porthouse-Event-Type": delivery["event_type"],
                "X-Porthouse-Timestamp": timestamp,
                "X-Porthouse-Signature": callback_signature(
                    secret,
                    timestamp=timestamp,
                    body=body,
                ),
            }
            response = await self._post(
                delivery["endpoint"],
                content=body,
                headers=headers,
            )
            if 200 <= response.status_code < 300:
                completed = await asyncio.to_thread(
                    self.store.complete_app_callback_delivery,
                    delivery["event_id"],
                    worker_id=worker_id,
                    lease_version=int(delivery["lease_version"]),
                    response_status=response.status_code,
                )
                if not completed:
                    raise RuntimeError("App callback completion was fenced")
                return True
            await asyncio.to_thread(
                self.store.fail_app_callback_delivery,
                delivery["event_id"],
                worker_id=worker_id,
                lease_version=int(delivery["lease_version"]),
                response_status=response.status_code,
                error=f"callback returned HTTP {response.status_code}",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await asyncio.to_thread(
                self.store.fail_app_callback_delivery,
                delivery["event_id"],
                worker_id=worker_id,
                lease_version=int(delivery["lease_version"]),
                error=f"{type(exc).__name__}: {str(exc)[:900]}",
            )
        return True

    async def _post(
        self, endpoint: str, *, content: bytes, headers: dict[str, str]
    ) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(endpoint, content=content, headers=headers)
        async with httpx.AsyncClient(
            transport=SsrfProtectedTransport(),
            timeout=httpx.Timeout(self.timeout_seconds),
            follow_redirects=False,
        ) as client:
            return await client.post(endpoint, content=content, headers=headers)


__all__ = ["AppCallbackDispatcher", "AppCallbackService"]
