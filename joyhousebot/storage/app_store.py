"""Storage contract for App releases and per-user installations."""

from __future__ import annotations

from typing import Any, Protocol


class AppStore(Protocol):
    def save_app_release(
        self, manifest: dict[str, Any], *, manifest_sha256: str, actor_id: str
    ) -> dict[str, Any]: ...

    def record_app_validation(
        self, app_id: str, version: str, report: dict[str, Any]
    ) -> dict[str, Any]: ...

    def publish_app_release(
        self, app_id: str, version: str, *, actor_id: str
    ) -> dict[str, Any]: ...

    def list_app_releases(self, app_id: str | None = None) -> list[dict[str, Any]]: ...

    def get_app_release(
        self, app_id: str, version: str | None = None
    ) -> dict[str, Any] | None: ...

    def install_app_release(self, **values: Any) -> dict[str, Any]: ...

    def transition_app_installation(
        self, installation_id: str, *, user_id: str, action: str, actor_id: str
    ) -> dict[str, Any]: ...

    def get_app_installation(
        self, installation_id: str, *, expected_user_id: str
    ) -> dict[str, Any] | None: ...

    def list_app_installations(self, *, user_id: str) -> list[dict[str, Any]]: ...
