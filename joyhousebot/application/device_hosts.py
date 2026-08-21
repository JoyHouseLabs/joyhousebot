"""Application boundary for Cloud-to-local Device Host delivery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from joyhousebot.application.context import Principal, RequestContext
from joyhousebot.application.errors import ConflictError, NotFoundError, ValidationError

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_ENVELOPE_BYTES = 1024 * 1024
_MAX_RESULT_BYTES = 4 * 1024 * 1024
_MAX_CONTROL_RESULT_BYTES = 128 * 1024
# Auto-delivery window: a phone must claim within the hour or the operation
# falls back to manual reconciliation; only fresh freezes are scanned.
_AUTO_DELIVERY_DEADLINE_SECONDS = 3600
_AUTO_DELIVERY_MAX_ATTEMPTS = 3
_AUTO_DELIVERY_CREATED_WITHIN_SECONDS = 21_600
_CONTROL_ACTIONS = {
    "preflight",
    "diagnose_opencli",
    "diagnose_pi",
    "enable_opencli",
    "disable_opencli",
    "enable_pi",
    "disable_pi",
    "restart_host",
}
_CONTROL_PARAMETER_KEYS = {"browser_profile_ref", "workspace_ref"}


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def device_token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class DeviceHostService:
    def __init__(self, store: Any, runtime: Any) -> None:
        self.store = store
        self.runtime = runtime

    async def register(
        self, context: RequestContext, **values: Any
    ) -> tuple[Any, str]:
        self._validate_registration(values)
        token = "jhd_" + secrets.token_urlsafe(48)
        existing = await asyncio.to_thread(
            self.store.list_device_hosts, user_id=context.user_id
        )
        record = await asyncio.to_thread(
            self.store.register_device_host,
            user_id=context.user_id,
            token_fingerprint=device_token_fingerprint(token),
            is_default=bool(values.get("is_default"))
            or not any(item.status == "active" for item in existing),
            **{key: value for key, value in values.items() if key != "is_default"},
        )
        if record is None:
            raise ConflictError("Device id is already registered; rotate its token explicitly")
        return record, token

    async def rotate_token(
        self, context: RequestContext, device_id: str
    ) -> str:
        token = "jhd_" + secrets.token_urlsafe(48)
        updated = await asyncio.to_thread(
            self.store.rotate_device_host_token,
            user_id=context.user_id,
            device_id=self._identifier(device_id, "device_id"),
            token_fingerprint=device_token_fingerprint(token),
        )
        if not updated:
            raise NotFoundError("active Device Host not found")
        return token

    async def list(self, context: RequestContext) -> list[Any]:
        return await asyncio.to_thread(
            self.store.list_device_hosts, user_id=context.user_id
        )

    async def revoke(self, context: RequestContext, device_id: str) -> None:
        revoked = await asyncio.to_thread(
            self.store.revoke_device_host,
            user_id=context.user_id,
            device_id=self._identifier(device_id, "device_id"),
        )
        if not revoked:
            raise NotFoundError("active Device Host not found")

    async def request_control(
        self,
        context: RequestContext,
        device_id: str,
        *,
        action: str,
        parameters: dict[str, Any] | None = None,
    ) -> Any:
        normalized_action = str(action or "").strip()
        if normalized_action not in _CONTROL_ACTIONS:
            raise ValidationError("Device Host control action is not allowlisted")
        normalized_parameters = self._control_parameters(parameters or {})
        normalized_device_id = self._identifier(device_id, "device_id")
        return await asyncio.to_thread(
            self.store.create_device_host_control_request,
            request_id=f"hostctl_{uuid4().hex}",
            user_id=context.user_id,
            device_id=normalized_device_id,
            action=normalized_action,
            parameters=normalized_parameters,
            request_digest=_digest(
                {
                    "action": normalized_action,
                    "device_id": normalized_device_id,
                    "parameters": normalized_parameters,
                }
            ),
            max_attempts=3,
            requested_by=context.user_id,
        )

    async def list_controls(
        self, context: RequestContext, device_id: str, *, limit: int
    ) -> list[Any]:
        return await asyncio.to_thread(
            self.store.list_device_host_control_requests,
            user_id=context.user_id,
            device_id=self._identifier(device_id, "device_id"),
            limit=limit,
        )

    async def claim_controls(self, device: Any, **values: Any) -> list[Any]:
        return await asyncio.to_thread(
            self.store.claim_device_host_control_requests,
            user_id=device.user_id,
            device_id=device.device_id,
            claim_session_id=self._claim_session(values["claim_session_id"]),
            limit=values["limit"],
            lease_seconds=values["lease_seconds"],
        )

    async def complete_control(
        self, device: Any, request_id: str, **values: Any
    ) -> Any:
        result = dict(values.get("result") or {})
        error = dict(values.get("error") or {})
        if len(_canonical({"result": result, "error": error})) > _MAX_CONTROL_RESULT_BYTES:
            raise ValidationError("Device Host control result exceeds 128 KiB")
        control_status = str(values.get("status") or "")
        if control_status not in {"succeeded", "failed", "manual_required"}:
            raise ValidationError("Device Host control completion status is invalid")
        saved = await asyncio.to_thread(
            self.store.complete_device_host_control_request,
            self._identifier(request_id, "request_id"),
            user_id=device.user_id,
            device_id=device.device_id,
            claim_session_id=self._claim_session(values["claim_session_id"]),
            claim_version=values["claim_version"],
            status=control_status,
            result=result,
            error=error,
        )
        if saved is None:
            raise ConflictError("Device Host control request is stale or not owned by this device")
        return saved

    async def get_delivery(self, context: RequestContext, delivery_id: str) -> Any:
        record = await asyncio.to_thread(
            self.store.get_device_operation_delivery,
            delivery_id,
            expected_user_id=context.user_id,
        )
        if record is None:
            raise NotFoundError("Device delivery not found")
        return record

    async def events(
        self,
        context: RequestContext,
        delivery_id: str,
        *,
        after_sequence: int,
        limit: int,
    ) -> list[Any]:
        await self.get_delivery(context, delivery_id)
        return await asyncio.to_thread(
            self.store.list_device_operation_events,
            delivery_id,
            expected_user_id=context.user_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    async def authenticate(self, device_id: str, token: str) -> Any | None:
        if not token.startswith("jhd_") or len(token) < 40:
            return None
        return await asyncio.to_thread(
            self.store.authenticate_device_host,
            token_fingerprint=device_token_fingerprint(token),
            device_id=self._identifier(device_id, "device_id"),
        )

    async def heartbeat_with_token(self, device: Any, token: str, **values: Any) -> Any:
        return await asyncio.to_thread(
            self._heartbeat_sync, device, token, values
        )

    def _heartbeat_sync(self, device: Any, token: str, values: dict[str, Any]) -> Any:
        record = self.store.heartbeat_device_host(
            token_fingerprint=device_token_fingerprint(token),
            device_id=device.device_id,
            host_revision=values["host_revision"],
            host_manifest_digest=self._sha256(
                values["host_manifest_digest"], "host_manifest_digest"
            ),
        )
        if record is None:
            raise ConflictError("Device Host revision differs from its active registration")
        return record

    async def enqueue(
        self,
        context: RequestContext,
        *,
        run_id: str,
        reconciliation_id: str,
        device_id: str,
        operation_id: str,
        portable: bool,
        deadline_seconds: int,
        max_attempts: int,
        model_access: dict[str, Any] | None,
        tool_access: list[dict[str, Any]],
    ) -> Any:
        reconciliation = await asyncio.to_thread(
            self.store.get_operation_reconciliation,
            reconciliation_id,
            expected_user_id=context.user_id,
        )
        if reconciliation is None or reconciliation.run_id != run_id:
            raise NotFoundError("operation reconciliation not found")
        action = await asyncio.to_thread(
            self.store.get_action_intent, reconciliation.action_id
        )
        if action is None:
            raise NotFoundError("frozen Action not found")
        run = await asyncio.to_thread(
            self.store.get_runtime_run, run_id, context.user_id
        )
        if run is None:
            raise NotFoundError("Run not found")
        devices = await asyncio.to_thread(
            self.store.list_device_hosts, user_id=context.user_id
        )
        selected = next(
            (
                item
                for item in devices
                if item.device_id == device_id and item.status == "active"
            ),
            None,
        )
        if selected is None:
            raise NotFoundError("active Device Host not found")
        capability = next(
            (
                item
                for item in selected.capabilities
                if item["capability_id"]
                == reconciliation.capability_ref.get("capability_id")
                and item["version"] == reconciliation.capability_ref.get("version")
            ),
            None,
        )
        if capability is None:
            raise ConflictError("Device Host does not provide the exact Capability revision")
        delivery_id = f"delivery_{uuid4().hex}"
        capability_identity = {
            "capability_id": capability["capability_id"],
            "version": capability["version"],
            "implementation_digest": capability["implementation_digest"],
        }
        subject = {
            "user_id": context.user_id,
            "agent_id": run.agent_id,
            "session_id": run.session_id,
        }
        authorization = {"permissions": [], "permission_mode": "enforced"}
        if model_access is not None:
            authorization["model_access"] = await asyncio.to_thread(
                self._freeze_model_access,
                model_access,
            )
        if tool_access:
            frozen_tools, permissions = await asyncio.to_thread(
                self._freeze_tool_access,
                tool_access,
                parent_capability_ref=dict(reconciliation.capability_ref),
            )
            authorization["tool_access"] = frozen_tools
            authorization["permissions"] = permissions
        invocation_digest = _digest(
            {
                "authorization": authorization,
                "capability": capability_identity,
                "input": action.input,
                "subject": subject,
            }
        )
        request = {
            "protocol_version": "1",
            "capability": capability_identity,
            "subject": subject,
            "execution": {
                "run_id": run_id,
                "root_run_id": run.root_run_id or run_id,
                "task_id": action.task_id,
                "request_id": context.request_id,
                "action_id": action.action_id,
                "idempotency_key": reconciliation.idempotency_key,
                "request_digest": invocation_digest,
            },
            "authorization": authorization,
            "input": action.input,
        }
        if len(_canonical(request)) > _MAX_ENVELOPE_BYTES:
            raise ValidationError("Device delivery envelope exceeds 1 MiB")
        return await asyncio.to_thread(
            self.store.enqueue_device_operation,
            delivery_id=delivery_id,
            user_id=context.user_id,
            device_id=self._identifier(device_id, "device_id"),
            reconciliation_id=reconciliation_id,
            run_id=run_id,
            operation_id=str(operation_id).strip(),
            portable=portable,
            deadline_at=datetime.now(UTC) + timedelta(seconds=deadline_seconds),
            max_attempts=max_attempts,
            request=request,
            request_digest=_digest(request),
        )

    async def auto_enqueue_pending(
        self,
        *,
        limit: int = 20,
        created_within_seconds: int = _AUTO_DELIVERY_CREATED_WITHIN_SECONDS,
    ) -> int:
        """Route frozen operations to the paired device that declared the capability.

        Candidates already passed capability approval before their Action froze:
        this pass only chooses the executor and never bypasses governance.
        Operations without a matching active device stay on the existing manual
        reconciliation path (fail-closed).
        """
        candidates = await asyncio.to_thread(
            self.store.find_device_delivery_candidates,
            limit=limit,
            created_within_seconds=created_within_seconds,
        )
        enqueued = 0
        for candidate in candidates:
            context = RequestContext(
                principal=Principal(
                    subject=f"device-delivery:{candidate['device_id']}",
                    user_id=str(candidate["user_id"]),
                ),
                request_id=f"auto-delivery_{uuid4().hex}",
            )
            try:
                await self.enqueue(
                    context,
                    run_id=str(candidate["run_id"]),
                    reconciliation_id=str(candidate["reconciliation_id"]),
                    device_id=str(candidate["device_id"]),
                    operation_id=str(candidate.get("provider_operation_id") or ""),
                    portable=False,
                    deadline_seconds=_AUTO_DELIVERY_DEADLINE_SECONDS,
                    max_attempts=_AUTO_DELIVERY_MAX_ATTEMPTS,
                    model_access=None,
                    tool_access=[],
                )
            except (ConflictError, NotFoundError, ValidationError):
                # The reconciliation moved on or the device was revoked between
                # the scan and the enqueue; the next pass re-evaluates.
                continue
            enqueued += 1
        return enqueued

    async def claim(self, device: Any, **values: Any) -> list[Any]:
        return await asyncio.to_thread(
            self.store.claim_device_operations,
            user_id=device.user_id,
            device_id=device.device_id,
            claim_session_id=self._claim_session(values["claim_session_id"]),
            limit=values["limit"],
            lease_seconds=values["lease_seconds"],
        )

    async def append_events(
        self, device: Any, delivery_id: str, **values: Any
    ) -> Any:
        record = await asyncio.to_thread(
            self.store.append_device_operation_events,
            delivery_id,
            user_id=device.user_id,
            device_id=device.device_id,
            claim_session_id=self._claim_session(values["claim_session_id"]),
            claim_version=values["claim_version"],
            events=values["events"],
        )
        if record is None:
            raise ConflictError("Device delivery claim is stale or not owned by this device")
        return record

    async def heartbeat_operation(
        self, device: Any, delivery_id: str, **values: Any
    ) -> Any:
        record = await asyncio.to_thread(
            self.store.heartbeat_device_operation,
            delivery_id,
            user_id=device.user_id,
            device_id=device.device_id,
            claim_session_id=self._claim_session(values["claim_session_id"]),
            claim_version=values["claim_version"],
            lease_seconds=values["lease_seconds"],
        )
        if record is None:
            raise ConflictError("Device delivery claim is stale or expired")
        return record

    async def complete(
        self, device: Any, delivery_id: str, **values: Any
    ) -> Any:
        result = dict(values["result"])
        if len(_canonical(result)) > _MAX_RESULT_BYTES:
            raise ValidationError("Device result exceeds 4 MiB; upload large outputs as Artifacts")
        existing = await asyncio.to_thread(
            self.store.get_device_operation_delivery,
            delivery_id,
            expected_user_id=device.user_id,
            expected_device_id=device.device_id,
        )
        if existing is None:
            raise NotFoundError("Device delivery not found")
        if result.get("invocation_id") != existing.invocation_id:
            raise ValidationError("Device result invocation identity is invalid")
        digest = _digest(result)
        saved = await asyncio.to_thread(
            self.store.complete_device_operation,
            delivery_id,
            user_id=device.user_id,
            device_id=device.device_id,
            claim_session_id=self._claim_session(values["claim_session_id"]),
            claim_version=values["claim_version"],
            result=result,
            result_digest=digest,
        )
        if saved is None:
            raise ConflictError("Device delivery claim is stale or not owned by this device")
        reconciled = await asyncio.to_thread(
            self.store.complete_operation_reconciliation,
            saved.reconciliation_id,
            run_id=saved.run_id,
            user_id=saved.user_id,
            result=result,
            operation={"operation_id": saved.operation_id, "device_id": saved.device_id},
            resolution_source="device",
            resolved_by=f"device:{saved.device_id}",
        )
        if reconciled is None:
            raise ConflictError("Runtime operation can no longer accept the Device result")
        if reconciled.result is not None and _digest(reconciled.result) != digest:
            raise ConflictError("Runtime operation already has a different terminal result")
        await asyncio.to_thread(self.store.notify_work, saved.run_id)
        return saved

    def _validate_registration(self, values: dict[str, Any]) -> None:
        self._identifier(values["device_id"], "device_id")
        self._sha256(values["host_manifest_digest"], "host_manifest_digest")
        capabilities = values.get("capabilities") or []
        identities: set[tuple[str, str]] = set()
        for capability in capabilities:
            self._identifier(capability["capability_id"], "capability_id")
            version = str(capability["version"]).strip()
            if not version or len(version) > 128:
                raise ValidationError("Device capability version is invalid")
            self._sha256(
                capability["implementation_digest"], "implementation_digest"
            )
            identity = (capability["capability_id"], version)
            if identity in identities:
                raise ValidationError("Device registration contains duplicate capabilities")
            identities.add(identity)

    def _freeze_model_access(self, policy: dict[str, Any]) -> dict[str, Any]:
        provider_id = str(policy["provider_id"])
        revision_id = str(policy["provider_revision_id"])
        provider = self.store.get_model_provider(provider_id)
        revision = self.store.get_model_provider_revision(provider_id, revision_id)
        if (
            provider is None
            or revision is None
            or revision["status"] != "published"
            or provider["current_revision_id"] != revision_id
        ):
            raise ValidationError("model_access must reference the current published Provider revision")
        model = next(
            (
                dict(item)
                for item in dict(revision["configuration"]).get("models") or ()
                if item.get("model_id") == policy["model_id"]
                and item.get("kind", "llm") == "llm"
                and item.get("enabled", True)
            ),
            None,
        )
        if model is None:
            raise ValidationError("model_access must reference an active exact LLM model")
        return {
            **dict(policy),
            "context_window": int(model.get("context_window") or 128_000),
            "max_output_tokens": int(model.get("max_output_tokens") or 4096),
        }

    def _freeze_tool_access(
        self,
        requested: list[dict[str, Any]],
        *,
        parent_capability_ref: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        frozen: list[dict[str, Any]] = []
        permissions: set[str] = set()
        seen: set[tuple[str, str]] = set()
        parent_identity = (
            str(parent_capability_ref.get("capability_id") or ""),
            str(parent_capability_ref.get("version") or ""),
        )
        for item in requested:
            identity = (str(item["capability_id"]), str(item["version"]))
            if identity == parent_identity:
                raise ValidationError(
                    "tool_access cannot recursively authorize the parent Capability"
                )
            if identity in seen:
                raise ValidationError("tool_access contains duplicate Capability identities")
            definition = self.store.get_capability_definition(*identity)
            if definition is None:
                raise ValidationError(
                    f"tool_access Capability is not active: {identity[0]}@{identity[1]}"
                )
            ref = dict(definition.get("ref") or {})
            frozen.append(ref)
            permissions.update(str(value) for value in definition.get("permissions") or ())
            seen.add(identity)
        return frozen, sorted(permissions)

    @staticmethod
    def _control_parameters(value: dict[str, Any]) -> dict[str, str]:
        if not isinstance(value, dict) or set(value) - _CONTROL_PARAMETER_KEYS:
            raise ValidationError("Device Host control parameters are not allowlisted")
        normalized: dict[str, str] = {}
        for key, raw in value.items():
            item = str(raw or "").strip()
            if not _IDENTIFIER.fullmatch(item):
                raise ValidationError(f"Device Host control parameter {key} is invalid")
            normalized[key] = item
        return normalized

    @staticmethod
    def _identifier(value: str, field: str) -> str:
        normalized = str(value or "").strip()
        if not _IDENTIFIER.fullmatch(normalized):
            raise ValidationError(f"{field} is invalid")
        return normalized

    @staticmethod
    def _sha256(value: str, field: str) -> str:
        normalized = str(value or "").strip()
        if not _DIGEST.fullmatch(normalized):
            raise ValidationError(f"{field} must be sha256:<64 lowercase hex>")
        return normalized

    @staticmethod
    def _claim_session(value: str) -> str:
        normalized = str(value or "").strip()
        if len(normalized) < 16 or len(normalized) > 256 or any(char.isspace() for char in normalized):
            raise ValidationError("claim_session_id is invalid")
        return normalized
