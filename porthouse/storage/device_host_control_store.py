"""Persistence contract for bounded Device Host control requests."""

from __future__ import annotations

from typing import Any, Protocol

from porthouse.storage.device_host_records import DeviceHostControlRequestRecord


class DeviceHostControlStore(Protocol):
    def create_device_host_control_request(
        self, **kwargs: Any
    ) -> DeviceHostControlRequestRecord: ...

    def list_device_host_control_requests(
        self, *, user_id: str, device_id: str, limit: int
    ) -> list[DeviceHostControlRequestRecord]: ...

    def claim_device_host_control_requests(
        self, **kwargs: Any
    ) -> list[DeviceHostControlRequestRecord]: ...

    def complete_device_host_control_request(
        self, request_id: str, **kwargs: Any
    ) -> DeviceHostControlRequestRecord | None: ...
