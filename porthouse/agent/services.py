"""Explicit service composition for the native Agent executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from porthouse.agent.memory_lifecycle import MemoryLifecycleMixin
from porthouse.agent.message_processor import MessageProcessorMixin
from porthouse.agent.model_invoker import ModelInvokerMixin
from porthouse.agent.tool_runtime import ToolRuntimeMixin
from porthouse.agent.turn_engine import TurnEngineMixin

if TYPE_CHECKING:
    from porthouse.agent.executor import NativeAgentExecutor


class _AgentService:
    """Share executor dependencies while keeping service ownership explicit."""

    def __init__(self, executor: NativeAgentExecutor) -> None:
        object.__setattr__(self, "_executor", executor)

    def __getattr__(self, name: str) -> Any:
        executor = object.__getattribute__(self, "_executor")
        try:
            return object.__getattribute__(executor, name)
        except AttributeError:
            return executor._resolve_service_method(name, requester=self)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_executor":
            object.__setattr__(self, name, value)
            return
        setattr(object.__getattribute__(self, "_executor"), name, value)


class AgentModelService(_AgentService, ModelInvokerMixin):
    """Resolve providers, fallbacks, retries, caching, and model health."""


class AgentToolService(_AgentService, ToolRuntimeMixin):
    """Own connector generations and capability catalog refreshes."""


class AgentTurnService(_AgentService, TurnEngineMixin):
    """Execute bounded model/tool turns and terminal verification."""


class AgentMessageService(_AgentService, MessageProcessorMixin):
    """Prepare, serialize, persist, and respond to inbound messages."""


class AgentMemoryService(_AgentService, MemoryLifecycleMixin):
    """Consolidate durable memory under the effective memory policy."""


@dataclass(frozen=True, slots=True)
class AgentServices:
    models: AgentModelService
    tools: AgentToolService
    turns: AgentTurnService
    messages: AgentMessageService
    memory: AgentMemoryService

    @classmethod
    def create(cls, executor: NativeAgentExecutor) -> AgentServices:
        return cls(
            models=AgentModelService(executor),
            tools=AgentToolService(executor),
            turns=AgentTurnService(executor),
            messages=AgentMessageService(executor),
            memory=AgentMemoryService(executor),
        )

    def resolve(self, name: str, *, requester: object | None = None) -> Any:
        for service in (self.models, self.tools, self.turns, self.messages, self.memory):
            if service is requester:
                continue
            for base in type(service).__mro__:
                if name in vars(base):
                    return getattr(service, name)
        raise AttributeError(name)
