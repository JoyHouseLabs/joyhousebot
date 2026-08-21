"""Framework-owned registry for trusted in-process Capability Extensions."""

from __future__ import annotations

import inspect
from collections.abc import Iterable
from dataclasses import is_dataclass, replace
from typing import Any

from joyhousebot.contracts.capabilities import CapabilityContext, CapabilityResult
from joyhousebot.contracts.capability_extensions import CapabilityExtensionManifest
from joyhousebot.domain.capabilities.models import CapabilityRef
from joyhousebot.extension_discovery import allowed_entry_points, validate_manifest
from joyhousebot.utils.permissions import missing_permissions


class CapabilityExtensionRegistry:
    """Register versioned Extension capabilities without product assets."""

    def __init__(self) -> None:
        self._extensions: dict[str, Any] = {}
        self._capabilities: dict[str, tuple[Any, Any, str | None]] = {}
        self._active_extension: str | None = None

    def register_extension(self, extension: Any) -> None:
        extension_id = str(getattr(extension, "extension_id", "")).strip()
        if not extension_id:
            raise ValueError("extension_id is required")
        version = str(getattr(extension, "version", "")).strip()
        if not version:
            raise ValueError(f"Extension {extension_id} version is required")
        manifest = self._manifest_for(extension)
        if manifest.extension_id != extension_id or manifest.version != version:
            raise ValueError("Extension identity does not match its manifest")
        existing = self._extensions.get(extension_id)
        if existing is not None and str(getattr(existing, "version", "")) != version:
            raise ValueError(f"Extension {extension_id} is already registered at another version")
        self._extensions[extension_id] = extension
        self._active_extension = extension_id
        try:
            extension.register(self)
        finally:
            self._active_extension = None

    def load_entry_points(
        self,
        group: str = "joyhousebot.capabilities",
        *,
        allowed_ids: Iterable[str] | None = None,
    ) -> list[str]:
        """Discover installed Capability Extensions and register allowed releases."""
        allowed = {str(item).strip() for item in allowed_ids or () if str(item).strip()}
        loaded: list[str] = []
        for entry in allowed_entry_points(group, allowed):
            extension = entry.load()
            if callable(extension) and not hasattr(extension, "register"):
                extension = extension()
            if str(getattr(extension, "extension_id", "")) != str(entry.name):
                raise ValueError(
                    f"entry point {entry.name!r} does not match Extension id "
                    f"{getattr(extension, 'extension_id', None)!r}"
                )
            manifest = self._manifest_for(extension)
            validate_manifest(
                manifest.to_extension_manifest(),
                entry_name=str(entry.name),
                expected_type="capability",
            )
            self.register_extension(extension)
            loaded.append(entry.name)
        return loaded

    def register_capability(self, definition: Any, handler: Any) -> None:
        ref = getattr(definition, "ref", None)
        capability_id = str(getattr(ref, "capability_id", "") or getattr(definition, "name", ""))
        version = str(getattr(ref, "version", "1.0.0"))
        if not capability_id or handler is None or not callable(getattr(handler, "execute", None)):
            raise ValueError("capability definition and async handler are required")
        if (
            self._active_extension
            and is_dataclass(definition)
        ):
            extension = self._extensions.get(self._active_extension)
            if extension is None:
                raise ValueError("Capability registration has no active Extension")
            manifest = self._manifest_for(extension)
            side_effect = str(getattr(definition, "side_effect", "unknown") or "unknown")
            if (
                side_effect.strip().lower() not in {"none", "read"}
                and manifest.runtime_contract_version < 2
            ):
                raise ValueError(
                    f"side-effecting capability {capability_id}@{version} requires "
                    "Extension runtime_contract_version >= 2"
                )
            if isinstance(ref, CapabilityRef) and not ref.is_bound:
                definition = replace(
                    definition,
                    ref=CapabilityRef(
                        capability_id=capability_id,
                        version=version,
                        kind=ref.kind,
                        extension_id=manifest.extension_id,
                        extension_version=manifest.version,
                        extension_build_digest=manifest.build_digest,
                    ),
                )
                ref = definition.ref
            if isinstance(ref, CapabilityRef) and (
                ref.extension_id != manifest.extension_id
                or ref.extension_version != manifest.version
                or ref.extension_build_digest != manifest.build_digest
            ):
                raise ValueError(
                    f"Capability {capability_id}@{version} is not bound to its active Extension release"
                )
            if "origin" in getattr(definition, "__dataclass_fields__", {}) and not getattr(definition, "origin", None):
                definition = replace(
                    definition,
                    origin={
                        "extension_id": manifest.extension_id,
                        "extension_version": manifest.version,
                        "extension_build_digest": manifest.build_digest,
                    },
                )
        key = f"{capability_id}@{version}"
        existing = self._capabilities.get(key)
        if existing is not None and existing[:2] != (definition, handler):
            raise ValueError(f"capability {key} is already registered")
        self._capabilities[key] = (definition, handler, self._active_extension)

    def get(self, capability_id: str, version: str | None = None) -> tuple[Any, Any] | None:
        prefix = f"{capability_id}@"
        if version is not None:
            value = self._capabilities.get(f"{capability_id}@{version}")
            return value[:2] if value else None
        matches = [value for key, value in self._capabilities.items() if key.startswith(prefix)]
        if not matches:
            return None
        return matches[-1][:2]

    def list_capabilities(self) -> list[Any]:
        return [value[0] for value in self._capabilities.values()]

    @property
    def extensions(self) -> tuple[Any, ...]:
        return tuple(self._extensions.values())

    def manifests(self) -> tuple[CapabilityExtensionManifest, ...]:
        """Return safe manifests for registered Extension releases."""
        values: list[CapabilityExtensionManifest] = []
        for extension in self.extensions:
            values.append(self._manifest_for(extension))
        return tuple(values)

    @staticmethod
    def _manifest_for(extension: Any) -> CapabilityExtensionManifest:
        declared = getattr(extension, "manifest", None)
        manifest = declared() if callable(declared) else None
        if not isinstance(manifest, CapabilityExtensionManifest):
            raise TypeError(
                f"Extension {extension.extension_id!r} must declare a CapabilityExtensionManifest"
            )
        return manifest

    async def invoke(
        self,
        capability_id: str,
        input: dict[str, Any],
        *,
        context: CapabilityContext,
        version: str | None = None,
    ) -> CapabilityResult:
        resolved = self.get(capability_id, version)
        if resolved is None:
            return CapabilityResult(success=False, error={"code": "CAPABILITY_NOT_FOUND", "message": capability_id})
        definition, handler = resolved
        requires_action = (
            str(getattr(definition, "side_effect", "unknown") or "unknown")
            .strip()
            .lower()
            not in {"none", "read"}
        )
        if requires_action and (not context.action_id or not context.idempotency_key):
            return CapabilityResult(
                success=False,
                error={
                    "code": "DURABLE_ACTION_REQUIRED",
                    "message": "side-effecting capability requires a frozen Action identity",
                },
            )
        required = set(getattr(definition, "permissions", ()) or ())
        granted = set((context.metadata or {}).get("permissions", ()) or ())
        missing = missing_permissions(granted, required)
        if missing:
            return CapabilityResult(
                success=False,
                error={"code": "PERMISSION_DENIED", "message": f"missing permissions: {', '.join(missing)}"},
            )
        result = handler.execute(context, input)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, CapabilityResult):
            raise TypeError(f"capability handler {capability_id} returned an invalid result")
        if requires_action and result.success:
            receipt = result.write_receipt
            if (
                receipt is None
                or receipt.action_id != context.action_id
                or receipt.idempotency_key != context.idempotency_key
            ):
                return CapabilityResult(
                    success=False,
                    error={
                        "code": "WRITE_IDENTITY_MISMATCH",
                        "message": "business write did not acknowledge the frozen Action identity",
                    },
                )
        return result


__all__ = ["CapabilityExtensionRegistry"]
