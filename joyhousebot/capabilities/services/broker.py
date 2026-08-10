"""Composition root for extension-facing Core ports."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .context import ContextPort
from .runtime_control import ChildRunPort, DeliveryPort, SchedulePort
from .sandbox import SandboxPort
from .scratch import ScratchPort


class CapabilityServiceBroker:
    """Group narrow ports without exposing repositories or runtime internals."""

    def __init__(
        self,
        runtime_store: Any | None,
        *,
        scratch_root: Path | None = None,
        outbound_sink: Any = None,
        subagent_manager: Any = None,
        schedule_service: Any = None,
    ) -> None:
        self.context = ContextPort(runtime_store)
        self.scratch = ScratchPort(scratch_root)
        self.sandbox = SandboxPort(self.scratch)
        self.delivery = DeliveryPort(outbound_sink)
        self.child_runs = ChildRunPort(subagent_manager)
        self.schedules = SchedulePort(schedule_service)


__all__ = ["CapabilityServiceBroker"]
