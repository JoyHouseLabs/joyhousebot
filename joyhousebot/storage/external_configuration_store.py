"""Storage contracts for versioned external runtime configuration."""

from __future__ import annotations

from typing import Any, Protocol


class ExternalConfigurationStore(Protocol):
    """Provider and remote-connection control-plane persistence."""

    def save_model_provider_revision(
        self, provider_id: str, **kwargs: Any
    ) -> dict[str, Any]: ...

    def stage_model_provider_revision(
        self, provider_id: str, revision_id: str, **kwargs: Any
    ) -> str: ...

    def list_model_providers(self) -> list[dict[str, Any]]: ...

    def get_model_provider(self, provider_id: str) -> dict[str, Any] | None: ...

    def get_model_provider_revision(
        self, provider_id: str, revision_id: str
    ) -> dict[str, Any] | None: ...

    def list_active_model_provider_configurations(self) -> dict[str, dict[str, Any]]: ...

    def list_active_models(self) -> list[dict[str, Any]]: ...

    def save_remote_connection_revision(
        self, connection_id: str, **kwargs: Any
    ) -> dict[str, Any]: ...

    def stage_remote_connection_revision(
        self, connection_id: str, revision_id: str, **kwargs: Any
    ) -> str: ...

    def list_remote_connections(self) -> list[dict[str, Any]]: ...

    def get_remote_connection(self, connection_id: str) -> dict[str, Any] | None: ...

    def get_remote_connection_revision(
        self, connection_id: str, revision_id: str
    ) -> dict[str, Any] | None: ...

    def list_active_remote_connection_configurations(self) -> dict[str, dict[str, Any]]: ...

    def sync_extension_inventory(
        self, candidates: list[dict[str, Any]], **kwargs: Any
    ) -> list[dict[str, Any]]: ...

    def list_extension_inventory(self) -> list[dict[str, Any]]: ...

    def get_extension_inventory(self, extension_id: str) -> dict[str, Any] | None: ...

    def set_extension_desired_active(
        self, extension_id: str, desired_active: bool, **kwargs: Any
    ) -> dict[str, Any]: ...

    def is_extension_execution_enabled(self, extension_id: str) -> bool: ...


__all__ = ["ExternalConfigurationStore"]
