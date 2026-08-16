"""Bounded repair decisions kept outside the provider/tool turn engine."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from porthouse.agent.durable_loop import DurableTurnJournal
from porthouse.runtime.context import RunContext, VerificationFailedError
from porthouse.runtime.verification import repair_limit, verify_output


async def accept_or_repair_final_response(
    *,
    content: str | None,
    messages: list[dict[str, Any]],
    journal: DurableTurnJournal,
    context: RunContext,
    turn_id: str,
    turn_index: int,
    max_turns: int,
    repairs_used: int,
    event_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
) -> tuple[bool, int]:
    """Return ``(accepted, repairs_used)`` or raise a terminal verification error."""

    decision = await verify_output(
        context,
        content,
        turn_id=turn_id,
        attempt=repairs_used + 1,
        event_callback=event_callback,
    )
    if decision.passed:
        await journal.finish(turn_id, status="completed", stop_reason="final_response")
        if event_callback:
            await event_callback(
                "turn_completed",
                {
                    "turn_id": turn_id,
                    "iteration": turn_index,
                    "stop_reason": "final_response",
                    "verification_attempt": decision.attempt,
                },
            )
        return True, repairs_used

    can_repair = (
        decision.repairable
        and repairs_used < repair_limit(context)
        and turn_index < max_turns
    )
    if can_repair:
        await journal.finish(
            turn_id,
            status="completed",
            stop_reason="verification_failed",
            error={"failures": list(decision.failures)},
        )
        messages.append({"role": "assistant", "content": content or ""})
        messages.append({"role": "user", "content": decision.repair_prompt or "Repair the result."})
        if event_callback:
            await event_callback(
                "turn_completed",
                {
                    "turn_id": turn_id,
                    "iteration": turn_index,
                    "stop_reason": "verification_failed",
                    "next_action": "repair",
                    "repair_attempt": repairs_used + 1,
                },
            )
        return False, repairs_used + 1

    await journal.finish(
        turn_id,
        status="failed",
        stop_reason="verification_failed",
        error={"failures": list(decision.failures)},
    )
    raise VerificationFailedError(decision.failures, decision.attempt)
