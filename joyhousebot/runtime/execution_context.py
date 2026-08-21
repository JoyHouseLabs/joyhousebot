"""Build immutable RunContext state for one model execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from joyhousebot.runtime.context import CancellationToken, RunContext
from joyhousebot.runtime.execution_events import ExecutionEventBridge, ExecutionEventRuntime
from joyhousebot.runtime.execution_metadata import build_execution_metadata
from joyhousebot.runtime.identity import conversation_key as build_conversation_key
from joyhousebot.runtime.models import AgentUsage
from joyhousebot.runtime.tracking import ensure_tracking_ids


class AgentContextRuntime(ExecutionEventRuntime, Protocol):
    async def _execution_permissions(
        self,
        run_id: str,
        agent_id: str,
        agent_revision_id: str | None = None,
    ) -> frozenset[str]: ...


@dataclass(frozen=True, slots=True)
class AgentCallContextRequest:
    run_id: str
    task_id: str | None
    user_id: str
    session_id: str
    agent_id: str
    agent_revision_id: str | None
    channel: str
    chat_id: str
    model: str | None
    system_prompt: str | None
    output_schema: dict[str, Any] | None
    max_turns: int | None
    max_input_tokens: int | None
    max_output_tokens: int | None
    max_cost_usd: float | None
    permission_mode: str
    allowed_tools: tuple[str, ...]
    disallowed_tools: tuple[str, ...]
    cancellation: CancellationToken
    metadata: dict[str, Any]
    verification_policy: dict[str, Any]
    max_repairs: int | None
    run_lease_version: int | None
    task_lease_version: int | None
    turn_scope: str


async def prepare_agent_call_context(
    runtime: AgentContextRuntime,
    request: AgentCallContextRequest,
) -> tuple[RunContext, ExecutionEventBridge]:
    record = await asyncio.to_thread(
        runtime.stores.runs.get_runtime_run, request.run_id
    )
    stored_options = dict(record.options or {}) if record else {}
    snapshot = await asyncio.to_thread(
        runtime.stores.catalog.get_run_execution_snapshot, request.run_id
    )
    bindings = _prompt_bindings(snapshot, request.agent_id)
    system_prompt = _frozen_system_prompt(bindings, request.system_prompt)
    request_id, tracker_id = ensure_tracking_ids(
        request_id=stored_options.get("request_id") or f"req_{request.run_id}",
        tracker_id=stored_options.get("tracker_id"),
    )
    execution_span_id = f"span_exec_{uuid4().hex}"
    bridge = ExecutionEventBridge(
        runtime=runtime,
        run_id=request.run_id,
        task_id=request.task_id,
        tracker_id=tracker_id,
        execution_span_id=execution_span_id,
        model=request.model,
        usage=AgentUsage(model=request.model),
    )
    permissions = await runtime._execution_permissions(
        request.run_id, request.agent_id, request.agent_revision_id
    )
    scenario_state, scenario_policy = await _scenario_context(runtime, request)
    metadata = build_execution_metadata(
        request.metadata,
        scenario_state=scenario_state,
        scenario_execution_policy=scenario_policy,
    )
    if bindings:
        metadata["prompt_revisions"] = [
            {
                "prompt_id": str(item.get("prompt_id") or ""),
                "revision_id": str(item.get("revision_id") or ""),
                "content_sha256": str(item.get("content_sha256") or ""),
                "purpose": str(item.get("purpose") or "system_instruction"),
            }
            for item in bindings
            if isinstance(item, dict)
        ]
    context = RunContext(
        run_id=request.run_id,
        task_id=request.task_id,
        turn_scope=request.turn_scope,
        root_run_id=(record.root_run_id if record else None) or request.run_id,
        parent_run_id=record.parent_run_id if record else None,
        parent_task_id=record.parent_task_id if record else None,
        request_id=request_id,
        tracker_id=tracker_id,
        parent_request_id=stored_options.get("parent_request_id"),
        parent_span_id=execution_span_id,
        trace_store=runtime.stores.execution,
        user_id=request.user_id,
        agent_id=request.agent_id,
        session_key=build_conversation_key(
            request.user_id, request.agent_id, request.session_id
        ),
        session_id=request.session_id,
        channel=request.channel,
        chat_id=request.chat_id,
        model=request.model,
        system_prompt=system_prompt,
        output_schema=request.output_schema,
        verification_policy=request.verification_policy,
        max_repairs=request.max_repairs,
        max_turns=request.max_turns,
        max_input_tokens=request.max_input_tokens,
        max_output_tokens=request.max_output_tokens,
        max_cost_usd=request.max_cost_usd,
        permission_mode=request.permission_mode,
        allowed_tools=frozenset(request.allowed_tools),
        disallowed_tools=frozenset(request.disallowed_tools),
        granted_permissions=permissions,
        cancellation=request.cancellation,
        worker_id=runtime.worker_id,
        run_lease_version=request.run_lease_version,
        task_lease_version=request.task_lease_version,
        context_timestamp=record.created_at if record else None,
        skill_names=tuple(str(item) for item in request.metadata.get("skill_names", [])),
        skill_refs=tuple(
            dict(item)
            for item in request.metadata.get("skill_refs", [])
            if isinstance(item, dict)
        ),
        memory_policy=dict(getattr(snapshot, "memory_policy", {}) or {}),
        metadata=metadata,
    )
    return context, bridge


def _prompt_bindings(snapshot: Any, agent_id: str) -> tuple[Any, ...]:
    if snapshot is None or agent_id not in {"default", snapshot.agent_id}:
        return ()
    return tuple(getattr(snapshot, "prompt_bindings", ()) or ())


def _frozen_system_prompt(bindings: tuple[Any, ...], fallback: str | None) -> str | None:
    content = [
        str(item.get("content") or "").strip()
        for item in bindings
        if isinstance(item, dict) and str(item.get("content") or "").strip()
    ]
    if not content:
        return fallback
    return "\n\n---\n\n".join([*content, *([fallback] if fallback else [])])


async def _scenario_context(
    runtime: AgentContextRuntime,
    request: AgentCallContextRequest,
) -> tuple[Any, dict[str, Any]]:
    state = await asyncio.to_thread(
        runtime.stores.scenarios.get_run_scenario_state,
        request.run_id,
        expected_user_id=request.user_id,
    )
    if state is None or not getattr(state, "scenario_id", None):
        return state, {}
    scenario = await asyncio.to_thread(
        runtime.stores.scenarios.get_scenario_version,
        str(state.scenario_id),
        int(getattr(state, "scenario_version", 0) or 0),
    )
    return state, dict(getattr(scenario, "execution_policy", {}) or {})


__all__ = ["AgentCallContextRequest", "prepare_agent_call_context"]
