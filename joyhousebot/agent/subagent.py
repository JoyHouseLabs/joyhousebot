"""Durable subagent submission through the native distributed runtime."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from typing import Any

from loguru import logger

from joyhousebot.capabilities.tool_adapter import ToolInvocationError, ToolOutput
from joyhousebot.contracts import OperationReconciliationResult
from joyhousebot.domain.capabilities import InvocationStatus
from joyhousebot.runtime.context import get_current_run_context
from joyhousebot.runtime.models import AgentEvent, AgentOptions, EventType


class SubagentManager:
    """Submit child runs; execution is always claimed from the shared store."""

    def __init__(self, *, model: str, max_spawns_per_run: int = 10) -> None:
        self.model = model
        self.max_spawns_per_run = max_spawns_per_run
        self._runtime: Any | None = None

    def set_runtime(self, runtime: Any) -> None:
        self._runtime = runtime

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        agent_id: str | None = None,
        output_schema: dict[str, Any] | None = None,
        origin_channel: str = "api",
        origin_chat_id: str = "direct",
        idempotency_key: str | None = None,
    ) -> ToolOutput:
        if self._runtime is None:
            raise RuntimeError("distributed runtime is not attached to this Agent worker")
        description = str(task or "").strip()
        if not description:
            raise ValueError("subagent task is required")

        operation_key = idempotency_key or f"direct:{uuid.uuid4().hex}"
        digest = hashlib.sha256(operation_key.encode("utf-8")).hexdigest()
        task_id = digest[:12]
        child_run_id = f"sub_{digest[:32]}"
        display_label = label or description[:30] + ("..." if len(description) > 30 else "")
        parent = get_current_run_context()
        user_id = parent.user_id if parent else "system"
        selected_agent_id = str(agent_id or (parent.agent_id if parent else "default"))
        selected_agent_revision_id = None
        team_metadata: dict[str, Any] = {}
        team_limit = self.max_spawns_per_run
        team_ref = parent.metadata.get("team_ref") if parent else None
        if isinstance(team_ref, dict):
            team = await asyncio.to_thread(
                self._runtime.store.get_agent_team_revision,
                str(team_ref.get("revision_id") or ""),
            )
            current_member_id = str(parent.metadata.get("team_member_id") or "")
            current_member = team.member(current_member_id) if team is not None else None
            selected_member = next(
                (
                    item
                    for item in (team.members if team is not None else ())
                    if item.agent_id == selected_agent_id
                ),
                None,
            )
            if (
                team is None
                or team.status not in {"published", "retired"}
                or team.team_id != str(team_ref.get("team_id") or "")
                or current_member is None
                or selected_member is None
            ):
                raise ToolInvocationError(
                    "SUBAGENT_TEAM_BOUNDARY",
                    "subagent target is outside the published AgentTeam",
                )
            if selected_member.member_id != current_member.member_id and (
                not current_member.can_delegate
                or selected_member.member_id not in current_member.allowed_handoffs
            ):
                raise ToolInvocationError(
                    "SUBAGENT_HANDOFF_DENIED",
                    "AgentTeam handoff policy denies this subagent target",
                )
            selected_agent_revision_id = selected_member.agent_revision_id
            team_limit = min(
                team_limit, int(team.budget_policy.get("max_handoffs") or team_limit)
            )
            team_metadata = {
                "team_ref": dict(team_ref),
                "team_members": [item.to_dict() for item in team.members],
                "team_member_id": selected_member.member_id,
                "team_context_policy": dict(team.context_policy),
                "team_budget_policy": dict(team.budget_policy),
                "team_approval_policy": dict(team.approval_policy),
            }
        parent_run_id = parent.run_id if parent else None
        parent_task_id = parent.task_id if parent else None
        root_run_id = (parent.root_run_id or parent.run_id) if parent else None
        parent_session = (
            (parent.session_id or parent.session_key)
            if parent
            else f"{origin_channel}:{origin_chat_id}"
        )

        try:
            record = await self._runtime.submit_run(
                AgentOptions(
                    prompt=self._build_prompt(description),
                    user_id=user_id,
                    session_id=f"{parent_session}:subagent:{task_id}",
                    agent_id=selected_agent_id,
                    agent_revision_id=selected_agent_revision_id,
                    channel="subagent",
                    chat_id=task_id,
                    model=self.model,
                    max_turns=15,
                    output_schema=output_schema,
                    disallowed_tools=["message", "spawn"],
                    metadata={
                        "source": "spawn",
                        "label": display_label,
                        "origin_channel": origin_channel,
                        "origin_chat_id": origin_chat_id,
                        "parent_run_id": parent_run_id,
                        **team_metadata,
                    },
                    root_run_id=root_run_id,
                    parent_run_id=parent_run_id,
                    parent_task_id=parent_task_id,
                    max_children_per_root=team_limit,
                    idempotency_key=f"subagent:{operation_key}",
                ),
                run_id=child_run_id,
            )
        except RuntimeError as exc:
            if "fan-out limit" in str(exc):
                raise ToolInvocationError("SUBAGENT_FANOUT_LIMIT", str(exc)) from exc
            raise
        if parent_run_id:
            await self._runtime.events.publish(
                AgentEvent(
                    run_id=parent_run_id,
                    task_id=parent_task_id,
                    type=EventType.SUBAGENT_SPAWNED.value,
                    data={
                        "child_run_id": record.run_id,
                        "subagent_task_id": task_id,
                        "label": display_label,
                        "agent_id": selected_agent_id,
                    },
                )
            )
        logger.info("Persisted subagent [{}]: {}", task_id, record.run_id)
        return ToolOutput(
            content=f"Subagent [{display_label}] queued.",
            summary=f"子 Agent {display_label} 已入队",
            data={"child_run_id": record.run_id, "label": display_label},
            operation={
                "run_id": record.run_id,
                "root_run_id": root_run_id or record.run_id,
                "parent_run_id": parent_run_id,
                "status": "queued",
                "idempotency_key": operation_key,
            },
            status=InvocationStatus.ACCEPTED,
        )

    async def reconcile(
        self, operation: dict[str, Any], *, user_id: str
    ) -> OperationReconciliationResult:
        if self._runtime is None:
            return OperationReconciliationResult(
                status="unknown", summary="distributed runtime is not attached"
            )
        store = getattr(self._runtime, "store", None)
        run_id = str(operation.get("run_id") or "")
        if store is None or not run_id:
            return OperationReconciliationResult(
                status="unknown", summary="child Run identity is unavailable"
            )
        record = await asyncio.to_thread(
            store.get_runtime_run, run_id, expected_user_id=user_id
        )
        if record is None:
            return OperationReconciliationResult(
                status="unknown", summary="child Run was not found"
            )
        if record.status == "completed":
            metadata = dict(dict(record.options or {}).get("metadata") or {})
            team_ref = metadata.get("team_ref")
            policy = dict(metadata.get("team_context_policy") or {})
            if (
                isinstance(team_ref, dict)
                and metadata.get("team_member_id")
                and bool(policy.get("workspace_enabled", True))
            ):
                result = dict(record.result or {})
                content = str(result.get("content") or "")
                max_chars = max(
                    500,
                    min(int(policy.get("max_entry_chars") or 6000), 100000),
                )
                await asyncio.to_thread(
                    store.append_team_workspace_entry,
                    entry_id=f"teamws:{record.run_id}:output",
                    user_id=user_id,
                    root_run_id=record.root_run_id or record.run_id,
                    team_id=str(team_ref.get("team_id") or ""),
                    team_revision_id=str(team_ref.get("revision_id") or ""),
                    source_run_id=record.run_id,
                    source_task_id=None,
                    member_id=str(metadata["team_member_id"]),
                    entry_type="subagent_result",
                    summary=content[:2000],
                    data={
                        "content": content[:max_chars],
                        "structured_output": result.get("structured_output"),
                        "usage": result.get("usage"),
                        "tools_used": result.get("tools_used"),
                    },
                    visibility=str(policy.get("default_visibility") or "team"),
                )
            return OperationReconciliationResult(
                status="succeeded",
                summary="子 Agent 已完成",
                output=record.result or {},
                operation={**operation, "status": record.status},
            )
        if record.status in {"failed", "cancelled", "timed_out"}:
            error = dict(record.error or {})
            return OperationReconciliationResult(
                status="failed",
                summary=str(error.get("message") or f"child Run {record.status}"),
                error={
                    "code": str(error.get("code") or f"CHILD_RUN_{record.status.upper()}"),
                    "message": str(error.get("message") or f"child Run {record.status}"),
                },
                operation={**operation, "status": record.status},
            )
        return OperationReconciliationResult(
            status="pending",
            summary=f"子 Agent 状态：{record.status}",
            operation={**operation, "status": record.status},
            retry_after_seconds=2,
        )

    @staticmethod
    def _build_prompt(task: str) -> str:
        return (
            "You are a focused child Agent in a distributed workflow. Complete only the "
            "assigned task, use tools when needed, and return a concise evidence-backed "
            f"result to the parent workflow.\n\n## Assigned task\n{task}"
        )
