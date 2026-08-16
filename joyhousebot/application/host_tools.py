"""Device-authenticated submission boundary for governed Host child Actions."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from joyhousebot.application.errors import ConflictError, NotFoundError, ValidationError
from joyhousebot.domain.capabilities import CapabilityRef
from joyhousebot.domain.identity import payload_hash
from joyhousebot.runtime.action_identity import durable_action_id, durable_turn_id


class HostToolService:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def create(self, device: Any, delivery_id: str, **values: Any) -> tuple[Any, bool]:
        delivery = await asyncio.to_thread(
            self.store.get_device_operation_delivery,
            delivery_id,
            expected_user_id=device.user_id,
            expected_device_id=device.device_id,
        )
        if delivery is None:
            raise NotFoundError("Device delivery not found")
        authorization = dict(delivery.request.get("authorization") or {})
        ref_value = next(
            (
                dict(item)
                for item in authorization.get("tool_access") or ()
                if item.get("capability_id") == values["capability_id"]
                and item.get("version") == values["capability_version"]
            ),
            None,
        )
        if ref_value is None:
            raise ValidationError("Host tool is outside the frozen allowlist")
        ref = CapabilityRef.from_dict(ref_value)
        input_value = dict(values["input"])
        identity_material = (
            f"{delivery_id}\0{values['host_request_id']}\0"
            f"{ref.capability_id}\0{ref.version}"
        )
        identity_hash = hashlib.sha256(identity_material.encode("utf-8")).hexdigest()
        turn_index = int(identity_hash[:8], 16) & 0x7FFFFFFF
        turn_id = durable_turn_id(
            delivery.run_id,
            delivery.task_id,
            turn_index,
            scope=f"host_tool:{delivery_id}",
        )
        action_id = durable_action_id(
            run_id=delivery.run_id,
            task_id=delivery.task_id,
            turn_index=turn_index,
            action_index=0,
            capability_ref=ref,
            inputs=input_value,
        )
        try:
            return await asyncio.to_thread(
                self.store.create_host_tool_request,
                request_id=f"host_tool_{identity_hash}",
                host_request_id=values["host_request_id"],
                delivery_id=delivery_id,
                user_id=device.user_id,
                device_id=device.device_id,
                claim_session_id=values["claim_session_id"],
                claim_version=values["claim_version"],
                capability_ref=ref.to_dict(),
                input=input_value,
                input_hash=payload_hash(input_value),
                turn_id=turn_id,
                turn_index=turn_index,
                action_id=action_id,
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc

    async def issue_for_device(
        self,
        device: Any,
        delivery_id: str,
        *,
        claim_session_id: str,
        claim_version: int,
        expires_in_seconds: int,
    ) -> tuple[Any, str]:
        token = "jht_" + secrets.token_urlsafe(48)
        record = await asyncio.to_thread(
            self.store.create_host_tool_grant,
            grant_id=f"tool_grant_{uuid4().hex}",
            token_fingerprint=host_tool_grant_fingerprint(token),
            delivery_id=delivery_id,
            user_id=device.user_id,
            device_id=device.device_id,
            claim_session_id=claim_session_id,
            claim_version=claim_version,
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in_seconds),
        )
        if record is None:
            raise ConflictError(
                "Device claim is stale or delivery has no frozen Host tool access"
            )
        return record, token

    async def authenticate_grant(self, token: str) -> Any | None:
        if not token.startswith("jht_") or len(token) < 40:
            return None
        return await asyncio.to_thread(
            self.store.authenticate_host_tool_grant,
            token_fingerprint=host_tool_grant_fingerprint(token),
        )

    async def create_with_grant(
        self, grant: Any, **values: Any
    ) -> tuple[Any, bool]:
        device = type(
            "HostToolGrantPrincipal",
            (),
            {"user_id": grant.user_id, "device_id": grant.device_id},
        )()
        return await self.create(
            device,
            grant.delivery_id,
            claim_session_id=grant.claim_session_id,
            claim_version=grant.claim_version,
            **values,
        )

    async def get(self, device: Any, delivery_id: str, request_id: str) -> Any:
        record = await asyncio.to_thread(
            self.store.get_host_tool_request,
            request_id,
            user_id=device.user_id,
            delivery_id=delivery_id,
        )
        if record is None:
            raise NotFoundError("Host tool request not found")
        return record

    async def get_with_grant(self, grant: Any, request_id: str) -> Any:
        record = await asyncio.to_thread(
            self.store.get_host_tool_request,
            request_id,
            user_id=grant.user_id,
            delivery_id=grant.delivery_id,
        )
        if record is None:
            raise NotFoundError("Host tool request not found")
        return record


def host_tool_grant_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
