"""Provider registry and generic Capability handler."""

from __future__ import annotations

from typing import Any

from joyhousebot.extension_sdk import (
    CapabilityContext,
    CapabilityResult,
    OperationReconciliationResult,
)

from .jimeng import JimengAdapter
from .media_provider import (
    MediaProviderAdapter,
    MediaProviderRegistry,
    capability_settings,
)
from .volcengine_ark import VolcengineArkAdapter


class MediaGenerationHandler:
    def __init__(self, kind: str, providers: MediaProviderRegistry) -> None:
        self.kind = kind
        self.providers = providers

    async def execute(
        self, context: CapabilityContext, input: dict[str, Any]
    ) -> CapabilityResult:
        settings = capability_settings(context)
        provider_id = str(
            input.get("provider") or settings.get("default_provider") or "volcengine_ark"
        )
        provider = self.providers.get(provider_id)
        if provider is None:
            return CapabilityResult(
                success=False,
                error={
                    "code": "MEDIA_PROVIDER_NOT_FOUND",
                    "message": f"media provider is not installed: {provider_id}",
                },
            )
        return await provider.execute(self.kind, context, input, settings)

    async def reconcile_operation(
        self, context: CapabilityContext, operation: dict[str, Any]
    ) -> OperationReconciliationResult:
        provider_id = str(operation.get("provider") or "")
        provider = self.providers.get(provider_id)
        if provider is None:
            return OperationReconciliationResult(
                status="unknown",
                summary=f"media provider is not installed: {provider_id or 'missing'}",
                operation=operation,
            )
        return await provider.reconcile(context, operation)


__all__ = [
    "JimengAdapter",
    "MediaGenerationHandler",
    "MediaProviderAdapter",
    "MediaProviderRegistry",
    "VolcengineArkAdapter",
]
