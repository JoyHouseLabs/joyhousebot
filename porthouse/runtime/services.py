"""Explicit service composition for the native durable Runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from porthouse.runtime.agent_execution import AgentExecutionMixin
from porthouse.runtime.controls import RuntimeControlsMixin
from porthouse.runtime.coordinator import RuntimeCoordinatorMixin
from porthouse.runtime.request_coordination import RequestCoordinationMixin
from porthouse.runtime.submission import SubmissionMixin

if TYPE_CHECKING:
    from porthouse.runtime.runner import NativeAgentRuntime


class _RuntimeService:
    """Share Runtime dependencies while keeping service ownership explicit."""

    def __init__(self, runtime: NativeAgentRuntime) -> None:
        object.__setattr__(self, "_runtime", runtime)

    def __getattr__(self, name: str) -> Any:
        runtime = object.__getattribute__(self, "_runtime")
        try:
            return object.__getattribute__(runtime, name)
        except AttributeError:
            return runtime._resolve_service_method(name, requester=self)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_runtime":
            object.__setattr__(self, name, value)
            return
        setattr(object.__getattribute__(self, "_runtime"), name, value)


class RuntimeSubmissionService(_RuntimeService, SubmissionMixin):
    """Accept and atomically materialize Agent and Graph Runs."""


class RuntimeAgentExecutionService(_RuntimeService, AgentExecutionMixin):
    """Execute Agent Runs and own their terminal transitions."""


class RuntimeCoordinationService(_RuntimeService, RuntimeCoordinatorMixin):
    """Coordinate workers, Graph Tasks, finalization, and maintenance."""


class RuntimeRequestCoordinationService(_RuntimeService, RequestCoordinationMixin):
    """Prepare scenarios, clarification, plans, and execution inputs."""


class RuntimeControlService(_RuntimeService, RuntimeControlsMixin):
    """Cancel, resume, and wait for durable Runs."""


@dataclass(frozen=True, slots=True)
class RuntimeServices:
    submission: RuntimeSubmissionService
    execution: RuntimeAgentExecutionService
    coordinator: RuntimeCoordinationService
    requests: RuntimeRequestCoordinationService
    controls: RuntimeControlService

    @classmethod
    def create(cls, runtime: NativeAgentRuntime) -> RuntimeServices:
        return cls(
            submission=RuntimeSubmissionService(runtime),
            execution=RuntimeAgentExecutionService(runtime),
            coordinator=RuntimeCoordinationService(runtime),
            requests=RuntimeRequestCoordinationService(runtime),
            controls=RuntimeControlService(runtime),
        )

    def resolve(self, name: str, *, requester: object | None = None) -> Any:
        for service in (
            self.submission,
            self.execution,
            self.coordinator,
            self.requests,
            self.controls,
        ):
            if service is requester:
                continue
            for base in type(service).__mro__:
                if name in vars(base):
                    return getattr(service, name)
        raise AttributeError(name)
