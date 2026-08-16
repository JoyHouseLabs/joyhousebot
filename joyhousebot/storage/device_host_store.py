"""Narrow persistence contract for Device Host transport projections."""

from __future__ import annotations

from typing import Any, Protocol

from joyhousebot.storage.device_host_records import (
    DeviceHostRegistrationRecord,
    DeviceOperationDeliveryEventRecord,
    DeviceOperationDeliveryRecord,
)


class DeviceHostStore(Protocol):
    def register_device_host(self, **kwargs: Any) -> DeviceHostRegistrationRecord | None: ...

    def rotate_device_host_token(self, **kwargs: Any) -> bool: ...

    def authenticate_device_host(
        self, *, token_fingerprint: str, device_id: str
    ) -> DeviceHostRegistrationRecord | None: ...

    def heartbeat_device_host(self, **kwargs: Any) -> DeviceHostRegistrationRecord | None: ...

    def list_device_hosts(self, *, user_id: str) -> list[DeviceHostRegistrationRecord]: ...

    def revoke_device_host(self, **kwargs: Any) -> bool: ...

    def enqueue_device_operation(self, **kwargs: Any) -> DeviceOperationDeliveryRecord: ...

    def claim_device_operations(self, **kwargs: Any) -> list[DeviceOperationDeliveryRecord]: ...

    def append_device_operation_events(
        self, delivery_id: str, **kwargs: Any
    ) -> DeviceOperationDeliveryRecord | None: ...

    def heartbeat_device_operation(
        self, delivery_id: str, **kwargs: Any
    ) -> DeviceOperationDeliveryRecord | None: ...

    def complete_device_operation(
        self, delivery_id: str, **kwargs: Any
    ) -> DeviceOperationDeliveryRecord | None: ...

    def get_device_operation_delivery(
        self, delivery_id: str, **kwargs: Any
    ) -> DeviceOperationDeliveryRecord | None: ...

    def list_device_operation_events(
        self, delivery_id: str, **kwargs: Any
    ) -> list[DeviceOperationDeliveryEventRecord]: ...
