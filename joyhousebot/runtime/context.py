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


class ContextBudgetExceededError(RunBudgetExceededError):
    """Raised before a model call when required context cannot fit its input budget."""

    def __init__(self, *, budget_tokens: int, required_tokens: int) -> None:
        super().__init__(
            "required model context exceeds budget: "
            f"required={required_tokens} tokens, budget={budget_tokens} tokens"
        )
        self.budget_tokens = budget_tokens
        self.required_tokens = required_tokens


class AgentLoopExhaustedError(RuntimeError):
    """Raised when a loop consumes its turn budget without a final answer."""

    def __init__(self, max_turns: int) -> None:
        super().__init__(f"agent loop exhausted after {max_turns} turns without a final result")
        self.max_turns = max_turns


class AgentLoopStalledError(RuntimeError):
    """Raised when consecutive turns propose the same action without progress."""

    def __init__(self, turn_index: int) -> None:
        super().__init__(
            f"agent loop stalled at turn {turn_index}: repeated action without progress"
        )
        self.turn_index = turn_index


class PlannerLoopExhaustedError(RuntimeError):
    """Raised when coordinator planning consumes its bounded replan budget."""

    def __init__(self, max_replans: int, attempt: int) -> None:
        super().__init__(
            "coordinator planning exhausted after "
            f"{attempt} attempts ({max_replans} replans allowed)"
        )
        self.max_replans = max_replans
        self.attempt = attempt


class VerificationFailedError(RuntimeError):
    """Raised when required output verification cannot be repaired."""

    def __init__(self, failures: tuple[dict[str, Any], ...], attempt: int) -> None:
        summary = "; ".join(str(item.get("message") or "failed") for item in failures)
        super().__init__(f"required output verification failed: {summary}")
        self.failures = failures
        self.attempt = attempt


class ActionOutcomeUnknownError(RuntimeError):
    """Raised when replay safety cannot prove whether an action took effect."""

    def __init__(self, action_id: str, invocation_id: str) -> None:
        super().__init__(f"action outcome is unknown and requires reconciliation: {action_id}")
        self.action_id = action_id
        self.invocation_id = invocation_id


class ActionApprovalRequiredError(RuntimeError):
    """Raised after a frozen Action has created a durable approval request."""

    def __init__(self, approval_id: str, action_id: str, required_role: str) -> None:
        super().__init__(f"action requires {required_role} approval: {action_id}")
        self.approval_id = approval_id
        self.action_id = action_id
        self.required_role = required_role


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
    granted_permissions: frozenset[str] = field(default_factory=frozenset)
    cancellation: CancellationToken = field(default_factory=CancellationToken)
    worker_id: str | None = None
    turn_id: str | None = None
    turn_index: int | None = None
    action_index: int | None = None
    action_id: str | None = None
    idempotency_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


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
    turn_scope: str = "execution"
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
    verification_policy: dict[str, Any] = field(default_factory=dict)
    max_repairs: int | None = None
    max_turns: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_cost_usd: float | None = None
    permission_mode: str = "default"
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    disallowed_tools: frozenset[str] = field(default_factory=frozenset)
    granted_permissions: frozenset[str] = field(default_factory=frozenset)
    cancellation: CancellationToken = field(default_factory=CancellationToken)
    worker_id: str | None = None
    run_lease_version: int | None = None
    task_lease_version: int | None = None
    context_timestamp: str | None = None
    skill_names: tuple[str, ...] = ()
    # Prompt Skills are immutable capabilities too.  Keep their approved
    # references with a Run so replay never silently reads a newer prompt.
    skill_refs: tuple[dict[str, str], ...] = ()
    # Content-free provenance captured while assembling the initial messages.
    # DurableTurnJournal extends it with Tool schemas and later loop messages.
    context_sources: tuple[dict[str, Any], ...] = ()
    context_candidates: tuple[dict[str, Any], ...] = field(
        default_factory=tuple, repr=False, compare=False
    )
    context_initial_message_count: int = 0
    context_budget_tokens: int | None = None
    context_budget_strategy: str = "history_tail_v1"
    # Immutable-by-convention execution metadata. Scenario inputs are copied
    # here so every capability can enforce the same confirmed constraints;
    # plugins must never need direct access to framework storage.
    metadata: dict[str, Any] = field(default_factory=dict)

    def for_tools(
        self,
        *,
        turn_id: str | None = None,
        turn_index: int | None = None,
        action_index: int | None = None,
    ) -> ToolExecutionContext:
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
            granted_permissions=self.granted_permissions,
            cancellation=self.cancellation,
            worker_id=self.worker_id,
            turn_id=turn_id,
            turn_index=turn_index,
            action_index=action_index,
            metadata=dict(self.metadata),
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
