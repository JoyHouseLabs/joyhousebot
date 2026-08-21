"""Bounded, durable coordinator planning with crash-safe replan decisions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from joyhousebot.domain.collaboration_blueprints import frozen_enforced_blueprint
from joyhousebot.orchestration.blueprint_compiler import (
    BlueprintRepairError,
    PlanBoundaryViolationError,
)
from joyhousebot.orchestration.coordinator_agent import (
    COORDINATOR_OUTPUT_SCHEMA,
    build_coordinator_prompt,
)
from joyhousebot.runtime.action_identity import payload_hash
from joyhousebot.runtime.context import (
    AgentLoopExhaustedError,
    CancellationToken,
    PlannerLoopExhaustedError,
    RunBudgetExceededError,
    VerificationFailedError,
)
from joyhousebot.runtime.models import AgentEvent, AgentOptions, AgentUsage, EventType
from joyhousebot.runtime.structured import StructuredOutputError
from joyhousebot.storage.contracts import AgentCatalogStorePort, PlanningStorePort

_SCOPE_PREFIX = "coordinator_plan"
_DEFAULT_MAX_REPLANS = 2
_MAX_REPLANS = 10
_PLANNING_ERRORS = (
    AgentLoopExhaustedError,
    StructuredOutputError,
    VerificationFailedError,
    ValueError,
)
# Fatal blueprint fences are not replan-able; they fail the Run closed after
# the escalate decision is recorded.
_FATAL_PLANNING_ERRORS = (PlanBoundaryViolationError,)


@dataclass(frozen=True, slots=True)
class CoordinatorPlanningResult:
    plan: dict[str, Any]
    usage: AgentUsage


@dataclass(slots=True)
class _PlanningLoopState:
    runtime: Any
    record: Any
    options: AgentOptions
    cancellation: CancellationToken
    user_prompt: str
    scenarios: list[dict[str, Any]]
    capabilities: list[dict[str, Any]]
    routing_decision: dict[str, Any]
    normalize: Callable[[dict[str, Any]], dict[str, Any]]
    scope: str
    max_replans: int
    decisions: list[Any]
    replans_used: int
    previous_reason: str | None


async def run_coordinator_planning(
    runtime: Any,
    *,
    record: Any,
    options: AgentOptions,
    cancellation: CancellationToken,
    user_prompt: str,
    scenarios: list[dict[str, Any]],
    capabilities: list[dict[str, Any]],
    routing_decision: dict[str, Any],
    normalize: Callable[[dict[str, Any]], dict[str, Any]],
) -> CoordinatorPlanningResult:
    """Return one accepted plan or fail after the persisted replan budget."""
    max_replans = await _effective_max_replans(runtime.stores.catalog, record.run_id, options)
    scope = _planning_scope(
        user_prompt,
        scenarios,
        capabilities,
        routing_decision,
        generation=int(options.metadata.get("plan_generation") or 0),
    )
    decisions = await asyncio.to_thread(
        runtime.stores.planning.list_loop_decisions,
        record.run_id,
        scope=scope,
    )
    replayed = await _replay_terminal_planning_decision(
        runtime, decisions=decisions, max_replans=max_replans
    )
    if replayed is not None:
        return replayed
    state = _PlanningLoopState(
        runtime=runtime,
        record=record,
        options=options,
        cancellation=cancellation,
        user_prompt=user_prompt,
        scenarios=scenarios,
        capabilities=capabilities,
        routing_decision=routing_decision,
        normalize=normalize,
        scope=scope,
        max_replans=max_replans,
        decisions=decisions,
        replans_used=sum(item.decision == "replan" for item in decisions),
        previous_reason=next(
            (item.reason_code for item in reversed(decisions) if item.decision == "replan"),
            None,
        ),
    )
    return await _run_planning_loop(state)


async def _replay_terminal_planning_decision(
    runtime: Any, *, decisions: list[Any], max_replans: int
) -> CoordinatorPlanningResult | None:
    accepted = next(
        (
            item
            for item in reversed(decisions)
            if item.decision == "continue" and item.reason_code == "plan_accepted"
        ),
        None,
    )
    if accepted is not None:
        await _publish_decision(runtime, accepted)
        return CoordinatorPlanningResult(
            plan=dict(accepted.details["plan"]),
            usage=_usage_from_dict(accepted.details.get("usage")),
        )
    exhausted = next(
        (
            item
            for item in reversed(decisions)
            if item.decision == "escalate"
            and item.reason_code == "max_replans_exhausted"
        ),
        None,
    )
    if exhausted is not None:
        await _publish_decision(runtime, exhausted)
        raise PlannerLoopExhaustedError(max_replans, exhausted.attempt)
    return None


async def _run_planning_loop(state: _PlanningLoopState) -> CoordinatorPlanningResult:
    while True:
        attempt = state.replans_used + 1
        attempt_prompt = _attempt_prompt(
            state.user_prompt, attempt, state.previous_reason
        )
        full_prompt = build_coordinator_prompt(
            attempt_prompt,
            scenarios=state.scenarios,
            capabilities=state.capabilities,
            routing_decision=state.routing_decision,
            team={
                "team_ref": dict(state.options.metadata.get("team_ref") or {}),
                "members": list(state.options.metadata.get("team_members") or []),
                "budget_policy": dict(
                    state.options.metadata.get("team_budget_policy") or {}
                ),
                "approval_policy": dict(
                    state.options.metadata.get("team_approval_policy") or {}
                ),
                "collaboration_blueprint": frozen_enforced_blueprint(
                    state.options.metadata.get("team_collaboration_blueprint")
                ),
            }
            if state.options.metadata.get("team_ref")
            else None,
        )
        try:
            plan = await _execute_planning_attempt(
                state, attempt=attempt, full_prompt=full_prompt
            )
        except _FATAL_PLANNING_ERRORS as exc:
            await _record_fatal_planning_error(
                state, attempt=attempt, full_prompt=full_prompt, exc=exc
            )
            raise
        except _PLANNING_ERRORS as exc:
            if await _handle_planning_error(
                state, attempt=attempt, full_prompt=full_prompt, exc=exc
            ):
                continue
        return await _accept_planning_result(
            state, attempt=attempt, full_prompt=full_prompt, plan=plan
        )


async def _execute_planning_attempt(
    state: _PlanningLoopState, *, attempt: int, full_prompt: str
) -> dict[str, Any]:
    options, record = state.options, state.record
    content, _, _ = await state.runtime._call_agent(
        run_id=record.run_id,
        task_id=None,
        prompt=full_prompt,
        user_id=record.user_id,
        session_id=f"{options.session_id}:coordinator",
        agent_id=record.agent_id,
        channel="runtime",
        chat_id="coordinator",
        model=options.model,
        system_prompt=None,
        output_schema=COORDINATOR_OUTPUT_SCHEMA,
        timeout_seconds=min(options.timeout_seconds, 90),
        max_turns=1,
        max_input_tokens=options.max_input_tokens,
        max_output_tokens=min(options.max_output_tokens or 2048, 2048),
        max_cost_usd=options.max_cost_usd,
        permission_mode="coordinator",
        allowed_tools=[],
        disallowed_tools=[],
        cancellation=state.cancellation,
        sender_id=options.sender_id or record.user_id,
        metadata={"phase": "coordination", "planning_attempt": attempt},
        max_repairs=0,
        run_lease_version=record.lease_version,
        turn_scope=f"{state.scope}:{attempt}",
    )
    return state.normalize(_structured_plan(content))


async def _record_fatal_planning_error(
    state: _PlanningLoopState,
    *,
    attempt: int,
    full_prompt: str,
    exc: PlanBoundaryViolationError,
) -> None:
    usage = await _planning_usage(state.runtime.stores.planning, state.record.run_id)
    decision = await _save_attempt_decision(
        state,
        attempt=attempt,
        decision="escalate",
        reason_code="plan_boundary_violation",
        summary="协调器计划越过 Blueprint 协作边界，运行失败关闭",
        full_prompt=full_prompt,
        details={
            "violations": [
                {"code": item.code, "message": item.message} for item in exc.violations
            ],
            "usage": usage.to_dict(),
        },
    )
    state.decisions.append(decision)
    await _publish_decision(state.runtime, decision)


async def _handle_planning_error(
    state: _PlanningLoopState,
    *,
    attempt: int,
    full_prompt: str,
    exc: BaseException,
) -> bool:
    usage = await _planning_usage(state.runtime.stores.planning, state.record.run_id)
    budget_reason = _budget_reason(state.options, usage)
    if budget_reason is not None:
        decision = await _save_attempt_decision(
            state,
            attempt=attempt,
            decision="escalate",
            reason_code="planning_budget_exceeded",
            summary="规划阶段已达到运行预算上限",
            full_prompt=full_prompt,
            details={"budget": budget_reason, "usage": usage.to_dict()},
        )
        state.decisions.append(decision)
        await _publish_decision(state.runtime, decision)
        raise RunBudgetExceededError(budget_reason) from exc
    reason_code, summary = _planning_failure(exc)
    if state.replans_used < state.max_replans:
        decision = await _save_attempt_decision(
            state,
            attempt=attempt,
            decision="replan",
            reason_code=reason_code,
            summary=summary,
            full_prompt=full_prompt,
            details={
                "failed_attempt": attempt,
                "next_attempt": attempt + 1,
                "error_type": type(exc).__name__,
                "usage": usage.to_dict(),
            },
        )
        state.decisions.append(decision)
        await _publish_decision(state.runtime, decision)
        state.replans_used += 1
        state.previous_reason = reason_code
        return True
    decision = await _save_attempt_decision(
        state,
        attempt=attempt,
        decision="escalate",
        reason_code="max_replans_exhausted",
        summary="协调器计划未通过校验，重规划次数已耗尽",
        full_prompt=full_prompt,
        details={
            "last_reason_code": reason_code,
            "attempts": attempt,
            "replans_used": state.replans_used,
            "usage": usage.to_dict(),
        },
    )
    state.decisions.append(decision)
    await _publish_decision(state.runtime, decision)
    raise PlannerLoopExhaustedError(state.max_replans, attempt) from exc


async def _save_attempt_decision(
    state: _PlanningLoopState,
    *,
    attempt: int,
    decision: str,
    reason_code: str,
    summary: str,
    full_prompt: str,
    details: dict[str, Any],
) -> Any:
    return await _record_decision(
        state.runtime,
        record=state.record,
        decisions=state.decisions,
        scope=state.scope,
        attempt=attempt,
        decision=decision,
        reason_code=reason_code,
        summary=summary,
        input_hash=payload_hash(full_prompt),
        output_hash=await _attempt_output_hash(
            state.runtime.stores.planning, state.record.run_id, state.scope, attempt
        ),
        max_replans=state.max_replans,
        details=details,
    )


async def _accept_planning_result(
    state: _PlanningLoopState,
    *,
    attempt: int,
    full_prompt: str,
    plan: dict[str, Any],
) -> CoordinatorPlanningResult:
    usage = await _planning_usage(state.runtime.stores.planning, state.record.run_id)
    budget_reason = _budget_reason(state.options, usage)
    if budget_reason is not None:
        decision = await _record_decision(
            state.runtime,
            record=state.record,
            decisions=state.decisions,
            scope=state.scope,
            attempt=attempt,
            decision="escalate",
            reason_code="planning_budget_exceeded",
            summary="规划阶段已达到运行预算上限",
            input_hash=payload_hash(full_prompt),
            output_hash=payload_hash(plan),
            max_replans=state.max_replans,
            details={"budget": budget_reason, "usage": usage.to_dict()},
        )
        await _publish_decision(state.runtime, decision)
        raise RunBudgetExceededError(budget_reason)
    decision = await _record_decision(
        state.runtime,
        record=state.record,
        decisions=state.decisions,
        scope=state.scope,
        attempt=attempt,
        decision="continue",
        reason_code="plan_accepted",
        summary="协调器计划已通过结构化校验",
        input_hash=payload_hash(full_prompt),
        output_hash=payload_hash(plan),
        max_replans=state.max_replans,
        details={
            "plan": plan,
            "usage": usage.to_dict(),
            "attempts": attempt,
            "replans_used": state.replans_used,
        },
    )
    await _publish_decision(state.runtime, decision)
    return CoordinatorPlanningResult(plan=plan, usage=usage)


async def _effective_max_replans(
    store: AgentCatalogStorePort, run_id: str, options: AgentOptions
) -> int:
    if options.max_replans is not None:
        return max(0, min(_MAX_REPLANS, int(options.max_replans)))
    snapshot = await asyncio.to_thread(store.get_run_execution_snapshot, run_id)
    configured = (snapshot.planning_policy if snapshot is not None else {}).get(
        "max_replans"
    )
    if configured is None:
        return _DEFAULT_MAX_REPLANS
    try:
        return max(0, min(_MAX_REPLANS, int(configured)))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_REPLANS


def _structured_plan(content: str | None) -> dict[str, Any]:
    from joyhousebot.runtime.structured import parse_structured_output

    return parse_structured_output(content, COORDINATOR_OUTPUT_SCHEMA)


def _attempt_prompt(user_prompt: str, attempt: int, reason_code: str | None) -> str:
    if attempt <= 1:
        return user_prompt
    return (
        f"{user_prompt}\n\n## Runtime planning feedback\n"
        f"The previous plan was rejected ({reason_code or 'validation_failed'}). "
        "Produce a complete replacement plan that satisfies the same request and schema."
    )


def _planning_scope(
    user_prompt: str,
    scenarios: list[dict[str, Any]],
    capabilities: list[dict[str, Any]],
    routing_decision: dict[str, Any],
    *,
    generation: int = 0,
) -> str:
    planning_key = payload_hash(
        {
            "user_prompt": user_prompt,
            "scenarios": scenarios,
            "capabilities": capabilities,
            "routing_decision": routing_decision,
            # A regenerated plan must not replay the previous generation's
            # accepted decision, even when the feedback text repeats.
            "plan_generation": generation,
        }
    )
    return f"{_SCOPE_PREFIX}:{planning_key[:24]}"


def _planning_failure(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, VerificationFailedError):
        return "plan_verification_failed", "协调器输出未通过结构化计划校验"
    if isinstance(exc, StructuredOutputError):
        return "plan_schema_invalid", "协调器输出不是有效的结构化计划"
    if isinstance(exc, AgentLoopExhaustedError):
        return "plan_turn_exhausted", "协调器单次规划未形成有效结果"
    if isinstance(exc, BlueprintRepairError):
        codes = ", ".join(sorted({item.code for item in exc.violations}))
        return (
            "plan_blueprint_violation",
            f"协调器计划不符合协作 Blueprint（{codes}）",
        )
    return "plan_semantic_invalid", "协调器计划未通过运行时语义校验"


async def _record_decision(
    runtime: Any,
    *,
    record: Any,
    decisions: list[Any],
    scope: str,
    attempt: int,
    decision: str,
    reason_code: str,
    summary: str,
    input_hash: str | None,
    output_hash: str | None,
    max_replans: int,
    details: dict[str, Any],
) -> Any:
    decision_index = max((item.decision_index for item in decisions), default=0) + 1
    decision_id = "decision_" + payload_hash(
        {
            "run_id": record.run_id,
            "scope": scope,
            "decision_index": decision_index,
        }
    )[:32]
    saved = await asyncio.to_thread(
        runtime.stores.planning.record_loop_decision,
        decision_id=decision_id,
        run_id=record.run_id,
        task_id=None,
        scope=scope,
        decision_index=decision_index,
        attempt=attempt,
        decision=decision,
        reason_code=reason_code,
        summary=summary,
        input_hash=input_hash,
        output_hash=output_hash,
        max_replans=max_replans,
        details=details,
        worker_id=runtime.worker_id,
        run_lease_version=record.lease_version,
    )
    if saved is None:
        raise asyncio.CancelledError("run ownership lost before planning decision")
    return saved


async def _publish_decision(runtime: Any, record: Any) -> None:
    status = (
        "failed"
        if record.decision == "escalate"
        else "replanning"
        if record.decision == "replan"
        else "completed"
    )
    data = {
        "decision_id": record.decision_id,
        "scope": record.scope,
        "decision": record.decision,
        "reason_code": record.reason_code,
        "attempt": record.attempt,
        "max_replans": record.max_replans,
    }
    await runtime.events.publish(
        AgentEvent(
            run_id=record.run_id,
            type=EventType.DECISION_RECORDED.value,
            phase="planning",
            status=status,
            summary=record.summary,
            data=data,
            event_id=f"{record.decision_id}:recorded",
        )
    )
    if record.decision == "replan":
        await runtime.events.publish(
            AgentEvent(
                run_id=record.run_id,
                type=EventType.PLAN_UPDATED.value,
                phase="planning",
                status="replanning",
                summary=record.summary,
                data={**data, "next_attempt": record.attempt + 1},
                event_id=f"{record.decision_id}:plan-updated",
            )
        )
    elif record.reason_code == "max_replans_exhausted":
        await runtime.events.publish(
            AgentEvent(
                run_id=record.run_id,
                type=EventType.LOOP_EXHAUSTED.value,
                phase="planning",
                status="failed",
                summary=record.summary,
                data={**data, "stop_reason": "max_replans"},
                event_id=f"{record.decision_id}:loop-exhausted",
            )
        )


async def _planning_usage(store: PlanningStorePort, run_id: str) -> AgentUsage:
    turns = await asyncio.to_thread(store.list_runtime_turns, run_id)
    usage = AgentUsage()
    for turn in turns:
        if not str(turn.scope).startswith(f"{_SCOPE_PREFIX}:"):
            continue
        value = dict(turn.usage or {})
        value["model"] = turn.model
        usage.add(AgentUsage.from_dict(value))
    return usage


async def _attempt_output_hash(
    store: PlanningStorePort, run_id: str, scope: str, attempt: int
) -> str | None:
    turns = await asyncio.to_thread(store.list_runtime_turns, run_id)
    turn_scope = f"{scope}:{attempt}"
    turn = next((item for item in turns if item.scope == turn_scope), None)
    return payload_hash(turn.response) if turn is not None and turn.response is not None else None


def _budget_reason(options: AgentOptions, usage: AgentUsage) -> str | None:
    if options.max_input_tokens is not None and usage.input_tokens > options.max_input_tokens:
        return "maximum input token budget exceeded during planning"
    if options.max_output_tokens is not None and usage.output_tokens > options.max_output_tokens:
        return "maximum output token budget exceeded during planning"
    if options.max_cost_usd is not None and usage.missing_billing_invocations:
        return "maximum cost budget cannot be enforced because planning billing is missing"
    if options.max_cost_usd is not None and float(usage.cost_usd or 0) > options.max_cost_usd:
        return "maximum cost budget exceeded during planning"
    return None


def _usage_from_dict(value: Any) -> AgentUsage:
    return AgentUsage.from_dict(value)
