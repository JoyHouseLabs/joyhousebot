"""Shared contracts and helpers for media provider adapters."""

from __future__ import annotations

import hashlib
import os
from typing import Any, Protocol, runtime_checkable

from joyhousebot.extension_sdk import (
    Artifact,
    CapabilityContext,
    CapabilityResult,
    OperationReconciliationResult,
    WriteReceipt,
)
from joyhousebot.extension_sdk.network import sanitize_error_message


class MediaProviderError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.outcome_unknown = outcome_unknown


@runtime_checkable
class MediaProviderAdapter(Protocol):
    """One provider implementation behind the stable media capabilities."""

    provider_id: str

    async def execute(
        self,
        kind: str,
        context: CapabilityContext,
        input: dict[str, Any],
        settings: dict[str, Any],
    ) -> CapabilityResult: ...

    async def reconcile(
        self,
        context: CapabilityContext,
        operation: dict[str, Any],
    ) -> OperationReconciliationResult: ...


class MediaProviderRegistry:
    def __init__(self, providers: tuple[MediaProviderAdapter, ...] = ()) -> None:
        self._providers: dict[str, MediaProviderAdapter] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: MediaProviderAdapter) -> None:
        provider_id = str(provider.provider_id).strip()
        if not provider_id:
            raise ValueError("media provider_id is required")
        if provider_id in self._providers and self._providers[provider_id] is not provider:
            raise ValueError(f"media provider is already registered: {provider_id}")
        self._providers[provider_id] = provider

    def get(self, provider_id: str) -> MediaProviderAdapter | None:
        return self._providers.get(provider_id)

    @property
    def provider_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))


def capability_settings(context: CapabilityContext) -> dict[str, Any]:
    value = context.metadata.get("capability_configuration") or {}
    return dict(value) if isinstance(value, dict) else {}


def credential(names: tuple[str, ...]) -> str:
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def failure(error: MediaProviderError) -> CapabilityResult:
    return CapabilityResult(
        success=False,
        error={
            "code": error.code,
            "message": sanitize_error_message(str(error)),
            "retryable": error.retryable,
        },
    )


def require_identity(context: CapabilityContext) -> None:
    if not context.action_id or not context.idempotency_key:
        raise MediaProviderError(
            "ACTION_IDENTITY_REQUIRED",
            "media generation requires a frozen Runtime Action identity",
        )


def write_receipt(context: CapabilityContext, operation_id: str) -> WriteReceipt:
    require_identity(context)
    return WriteReceipt(
        action_id=str(context.action_id),
        idempotency_key=str(context.idempotency_key),
        provider_operation_id=operation_id,
    )


def submission_unknown(
    *,
    context: CapabilityContext,
    provider: str,
    media_kind: str,
    model: str,
    message: str,
) -> CapabilityResult:
    """Persist an ambiguous POST without ever resubmitting the paid operation."""
    require_identity(context)
    operation_id = f"unknown:{context.action_id}"
    operation = {
        "provider": provider,
        "provider_operation_id": operation_id,
        "media_kind": media_kind,
        "model": model,
        "status": "submission_unknown",
        "submission_error": sanitize_error_message(message),
    }
    return CapabilityResult(
        success=True,
        output={
            "provider": provider,
            "model": model,
            "status": "submission_unknown",
        },
        status="accepted",
        operation=operation,
        write_receipt=write_receipt(context, operation_id),
    )


def _artifact_id(action_id: str, media_kind: str, index: int) -> str:
    digest = hashlib.sha256(f"{action_id}:{media_kind}:{index}".encode()).hexdigest()[:32]
    return f"artifact_media_{digest}"


def _media_type(uri: str, default: str) -> str:
    path = uri.lower().split("?", 1)[0]
    if path.endswith(".png"):
        return "image/png"
    if path.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if path.endswith(".webp"):
        return "image/webp"
    if path.endswith(".mp4"):
        return "video/mp4"
    return default


def media_artifacts(
    *,
    action_id: str,
    media_kind: str,
    urls: list[str],
    provider: str,
    model: str,
    operation_id: str,
    source_expires_seconds: int | None,
) -> list[Artifact]:
    default_type = "image/png" if media_kind == "image" else "video/mp4"
    return [
        Artifact(
            artifact_id=_artifact_id(action_id, media_kind, index),
            artifact_type=f"media.{media_kind}",
            media_type=_media_type(url, default_type),
            data={
                "provider": provider,
                "model": model,
                "source_url": url,
                "source_expires_seconds": source_expires_seconds,
            },
            uri=url,
            object_version=operation_id,
            provenance={
                "provider": provider,
                "provider_operation_id": operation_id,
                "model": model,
            },
            evidence={"provider_operation_id": operation_id},
            metadata={
                "name": f"generated-{media_kind}-{index + 1}",
                "source_is_ephemeral": source_expires_seconds is not None,
            },
        )
        for index, url in enumerate(urls)
    ]


def response_error(response: Any, provider: str) -> MediaProviderError | None:
    if int(response.status_code) < 400:
        return None
    try:
        payload = response.json()
    except Exception:
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        error = payload if isinstance(payload, dict) else {}
    code = str(error.get("code") or f"{provider.upper()}_HTTP_{response.status_code}")
    message = str(error.get("message") or f"{provider} request failed")
    return MediaProviderError(
        code,
        message,
        retryable=int(response.status_code) == 429 or int(response.status_code) >= 500,
        outcome_unknown=int(response.status_code) >= 500,
    )


__all__ = [
    "MediaProviderAdapter",
    "MediaProviderError",
    "MediaProviderRegistry",
    "capability_settings",
    "credential",
    "failure",
    "media_artifacts",
    "require_identity",
    "response_error",
    "submission_unknown",
    "write_receipt",
]
