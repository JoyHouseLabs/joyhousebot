"""Explicit mutable state for one durable Agent turn loop."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from porthouse.agent.durable_loop import DurableTurnJournal
from porthouse.providers.base import LLMResponse
from porthouse.runtime.action_identity import durable_turn_id
from porthouse.runtime.context import RunBudgetExceededError, RunContext


@dataclass(slots=True)
class UsageAccumulator:
    input_tokens: int = 0
    output_tokens: int = 0
    billed_input_tokens: int = 0
    billed_output_tokens: int = 0
    cost_usd: float = 0.0
    model_invocations: int = 0
    missing_usage_invocations: int = 0
    partial_usage_invocations: int = 0
    missing_billing_invocations: int = 0

    def record(
        self,
        response: LLMResponse,
        *,
        model: str,
        iteration: int,
        turn_id: str,
    ) -> dict[str, Any]:
        usage = response.usage or {}
        input_tokens = int(
            usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
        )
        output_tokens = int(
            usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
        )
        cache_hit = usage.get("usage_source") == "cache"
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.billed_input_tokens += int(
            usage.get("billed_input_tokens", 0 if cache_hit else input_tokens) or 0
        )
        self.billed_output_tokens += int(
            usage.get("billed_output_tokens", 0 if cache_hit else output_tokens) or 0
        )
        self.cost_usd += float(
            usage.get("cost_usd", usage.get("total_cost", usage.get("cost", 0.0))) or 0.0
        )
        self.model_invocations += 1
        usage_status = str(usage.get("usage_status") or "exact")
        billing_status = str(
            usage.get("billing_status")
            or (
                "not_billed"
                if cache_hit
                else "exact"
                if any(key in usage for key in ("cost_usd", "total_cost", "cost"))
                else "missing"
            )
        )
        self.missing_usage_invocations += int(usage_status == "missing")
        self.partial_usage_invocations += int(usage_status == "partial")
        self.missing_billing_invocations += int(billing_status == "missing")
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "billed_input_tokens": self.billed_input_tokens,
            "billed_output_tokens": self.billed_output_tokens,
            "billed_total_tokens": self.billed_input_tokens + self.billed_output_tokens,
            "cost_usd": self.cost_usd,
            "model_invocations": self.model_invocations,
            "missing_usage_invocations": self.missing_usage_invocations,
            "partial_usage_invocations": self.partial_usage_invocations,
            "missing_billing_invocations": self.missing_billing_invocations,
            "usage_status": self.usage_status,
            "billing_status": self.billing_status,
            "model": model,
            "iteration": iteration,
            "turn_id": turn_id,
        }

    @property
    def usage_status(self) -> str:
        if self.missing_usage_invocations >= self.model_invocations:
            return "missing"
        if self.missing_usage_invocations or self.partial_usage_invocations:
            return "partial"
        return "exact"

    @property
    def billing_status(self) -> str:
        if self.missing_billing_invocations >= self.model_invocations:
            return "missing"
        if self.missing_billing_invocations:
            return "partial"
        return "exact"

    def enforce_budget(self, context: RunContext) -> None:
        if context.max_input_tokens is not None and self.input_tokens > context.max_input_tokens:
            raise RunBudgetExceededError("maximum input token budget exceeded")
        if (
            context.max_output_tokens is not None
            and self.output_tokens > context.max_output_tokens
        ):
            raise RunBudgetExceededError("maximum output token budget exceeded")
        if context.max_cost_usd is not None and self.missing_billing_invocations:
            raise RunBudgetExceededError(
                "maximum cost budget cannot be enforced because model billing is missing"
            )
        if context.max_cost_usd is not None and self.cost_usd > context.max_cost_usd:
            raise RunBudgetExceededError("maximum cost budget exceeded")


@dataclass(slots=True)
class TurnLoopState:
    messages: list[dict[str, Any]]
    base_message_count: int
    active_model: str
    max_iterations: int
    journal: DurableTurnJournal
    iteration: int = 0
    current_turn_id: str | None = None
    turn_started_at: float = 0.0
    final_content: str | None = None
    last_response: LLMResponse | None = None
    tools_used: list[str] = field(default_factory=list)
    previous_action_signature: str | None = None
    repairs_used: int = 0
    usage: UsageAccumulator = field(default_factory=UsageAccumulator)

    @classmethod
    async def create(
        cls,
        initial_messages: list[dict[str, Any]],
        *,
        context: RunContext,
        default_model: str,
        configured_max_iterations: int,
    ) -> TurnLoopState:
        max_iterations = configured_max_iterations
        if context.max_turns is not None:
            max_iterations = max(1, min(max_iterations, context.max_turns))
        return cls(
            messages=initial_messages,
            base_message_count=len(initial_messages),
            active_model=context.model or default_model,
            max_iterations=max_iterations,
            journal=await DurableTurnJournal.open(context),
        )

    def begin_turn(self, context: RunContext) -> str:
        self.iteration += 1
        self.current_turn_id = durable_turn_id(
            context.run_id,
            context.task_id,
            self.iteration,
            scope=context.turn_scope,
        )
        self.turn_started_at = time.monotonic()
        return self.current_turn_id
