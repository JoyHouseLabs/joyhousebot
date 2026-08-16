"""Issue and govern short-lived model grants for untrusted Device Hosts."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from porthouse.application.context import RequestContext
from porthouse.application.errors import ConflictError, NotFoundError, ValidationError


def model_grant_token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ModelGrantService:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def issue(
        self,
        context: RequestContext,
        delivery_id: str,
        **values: Any,
    ) -> tuple[Any, str]:
        delivery = await asyncio.to_thread(
            self.store.get_device_operation_delivery,
            delivery_id,
            expected_user_id=context.user_id,
        )
        if delivery is None:
            raise NotFoundError("Device delivery not found")
        if delivery.status != "claimed":
            raise ConflictError("Model grants require an actively claimed Device delivery")
        token = "jhm_" + secrets.token_urlsafe(48)
        record = await asyncio.to_thread(
            self.store.create_host_model_grant,
            grant_id=f"model_grant_{uuid4().hex}",
            token_fingerprint=model_grant_token_fingerprint(token),
            user_id=context.user_id,
            delivery_id=delivery_id,
            provider_id=values["provider_id"],
            provider_revision_id=values["provider_revision_id"],
            model_id=values["model_id"],
            token_budget=values["token_budget"],
            cost_budget_micros=values["cost_budget_micros"],
            max_concurrent=values["max_concurrent"],
            expires_at=datetime.now(UTC) + timedelta(seconds=values["expires_in_seconds"]),
        )
        return record, token

    async def list(
        self,
        context: RequestContext,
        *,
        delivery_id: str | None,
        limit: int,
    ) -> list[Any]:
        return await asyncio.to_thread(
            self.store.list_host_model_grants,
            user_id=context.user_id,
            delivery_id=delivery_id,
            limit=limit,
        )

    async def issue_for_device(
        self,
        device: Any,
        delivery_id: str,
        *,
        claim_session_id: str,
        claim_version: int,
    ) -> tuple[Any, str]:
        delivery = await asyncio.to_thread(
            self.store.get_device_operation_delivery,
            delivery_id,
            expected_user_id=device.user_id,
            expected_device_id=device.device_id,
        )
        if delivery is None:
            raise NotFoundError("Device delivery not found")
        if (
            delivery.status != "claimed"
            or delivery.claim_session_id != claim_session_id
            or delivery.claim_version != claim_version
        ):
            raise ConflictError("Device delivery claim is stale or not owned by this device")
        authorization = dict(delivery.request.get("authorization") or {})
        policy = authorization.get("model_access")
        if not isinstance(policy, dict):
            raise ValidationError("Device delivery has no frozen model access policy")
        required = {
            "provider_id",
            "provider_revision_id",
            "model_id",
            "token_budget",
            "cost_budget_micros",
            "max_concurrent",
            "expires_in_seconds",
        }
        allowed = required | {"context_window", "max_output_tokens"}
        if not required.issubset(policy) or not set(policy).issubset(allowed):
            raise ValidationError("Frozen model access policy is invalid")
        token = "jhm_" + secrets.token_urlsafe(48)
        record = await asyncio.to_thread(
            self.store.create_host_model_grant,
            grant_id=f"model_grant_{uuid4().hex}",
            token_fingerprint=model_grant_token_fingerprint(token),
            user_id=device.user_id,
            delivery_id=delivery_id,
            provider_id=str(policy["provider_id"]),
            provider_revision_id=str(policy["provider_revision_id"]),
            model_id=str(policy["model_id"]),
            token_budget=int(policy["token_budget"]),
            cost_budget_micros=int(policy["cost_budget_micros"]),
            max_concurrent=int(policy["max_concurrent"]),
            expires_at=datetime.now(UTC)
            + timedelta(seconds=int(policy["expires_in_seconds"])),
        )
        rotated = await asyncio.to_thread(
            self.store.rotate_device_host_model_grant_token,
            grant_id=record.grant_id,
            user_id=device.user_id,
            device_id=device.device_id,
            delivery_id=delivery_id,
            claim_session_id=claim_session_id,
            claim_version=claim_version,
            token_fingerprint=model_grant_token_fingerprint(token),
        )
        if rotated is None:
            raise ConflictError("Device delivery claim expired while issuing model grant")
        return rotated, token

    async def revoke(self, context: RequestContext, grant_id: str) -> None:
        revoked = await asyncio.to_thread(
            self.store.revoke_host_model_grant,
            user_id=context.user_id,
            grant_id=grant_id,
        )
        if not revoked:
            raise NotFoundError("active Host model grant not found")


__all__ = ["ModelGrantService", "model_grant_token_fingerprint"]
