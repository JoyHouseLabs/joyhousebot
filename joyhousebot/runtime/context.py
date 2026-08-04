"""Run-scoped context for the native agent runtime.

Request-specific data must live here instead of on the shared ``NativeAgentExecutor`` or
tool instances.  Explicit tool context is the primary API; the ContextVar is a
fallback for callbacks whose public signature cannot carry the context yet.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator


class RunCancelledError(asyncio.CancelledError):
    """Raised when a runtime cancellation token has been cancelled."""


class RunBudgetExceededError(RuntimeError):
    """Raised when a per-run token, cost, or turn budget is exceeded."""


class CancellationToken:
    """Cooperative, run-scoped cancellation signal."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason: str | None = None

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    def cancel(self, reason: str | None = None) -> None:
        if not self._event.is_set():
            self._reason = reason
            self._event.set()

    async def wait(self) -> str | None:
        await self._event.wait()
        return self._reason

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise RunCancelledError(self._reason or "agent run cancelled")


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """Minimal immutable context exposed to tools."""

    run_id: str
    session_key: str
    channel: str
    chat_id: str
    user_id: str = "system"
    agent_id: str = "default"
    session_id: str | None = None
    memory_scope: str | None = None
    memory_policy: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    root_run_id: str | None = None
    parent_run_id: str | None = None
    parent_task_id: str | None = None
    request_id: str | None = None
    tracker_id: str | None = None
    parent_request_id: str | None = None
    parent_span_id: str | None = None
    permission_mode: str = "default"
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    disallowed_tools: frozenset[str] = field(default_factory=frozenset)
    cancellation: CancellationToken = field(default_factory=CancellationToken)
    worker_id: str | None = None


@dataclass(frozen=True, slots=True)
class RunContext:
    """Immutable identity and routing context for one agent run."""

    run_id: str
    session_key: str
    channel: str
    chat_id: str
    user_id: str = "system"
    agent_id: str = "default"
    session_id: str | None = None
    memory_scope: str | None = None
    memory_policy: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    root_run_id: str | None = None
    parent_run_id: str | None = None
    parent_task_id: str | None = None
    request_id: str | None = None
    tracker_id: str | None = None
    parent_request_id: str | None = None
    parent_span_id: str | None = None
    trace_store: Any = field(default=None, repr=False, compare=False)
    model: str | None = None
    system_prompt: str | None = None
    output_schema: dict[str, Any] | None = None
    max_turns: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_cost_usd: float | None = None
    permission_mode: str = "default"
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    disallowed_tools: frozenset[str] = field(default_factory=frozenset)
    cancellation: CancellationToken = field(default_factory=CancellationToken)
    worker_id: str | None = None
    skill_names: tuple[str, ...] = ()

    def for_tools(self) -> ToolExecutionContext:
        return ToolExecutionContext(
            run_id=self.run_id,
            user_id=self.user_id,
            agent_id=self.agent_id,
            session_key=self.session_key,
            session_id=self.session_id,
            channel=self.channel,
            chat_id=self.chat_id,
            memory_scope=self.memory_scope,
            memory_policy=self.memory_policy,
            task_id=self.task_id,
            root_run_id=self.root_run_id,
            parent_run_id=self.parent_run_id,
            parent_task_id=self.parent_task_id,
            request_id=self.request_id,
            tracker_id=self.tracker_id,
            parent_request_id=self.parent_request_id,
            parent_span_id=self.parent_span_id,
            permission_mode=self.permission_mode,
            allowed_tools=self.allowed_tools,
            disallowed_tools=self.disallowed_tools,
            cancellation=self.cancellation,
            worker_id=self.worker_id,
        )


_current_run_context: ContextVar[RunContext | None] = ContextVar(
    "joyhousebot_run_context",
    default=None,
)


def get_current_run_context() -> RunContext | None:
    """Return the context bound to the current async task, if any."""

    return _current_run_context.get()


@contextmanager
def bind_run_context(context: RunContext) -> Iterator[RunContext]:
    """Bind a run context for the duration of the current execution path."""

    token = _current_run_context.set(context)
    try:
        yield context
    finally:
        _current_run_context.reset(token)
