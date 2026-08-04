"""Durable subagent submission through the native distributed runtime."""

from __future__ import annotations

import uuid
from typing import Any

from loguru import logger

from joyhousebot.capabilities.tool_adapter import ToolInvocationError, ToolOutput
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
    ) -> ToolOutput:
        if self._runtime is None:
            raise RuntimeError("distributed runtime is not attached to this Agent worker")
        description = str(task or "").strip()
        if not description:
            raise ValueError("subagent task is required")

        task_id = uuid.uuid4().hex[:8]
        child_run_id = uuid.uuid4().hex
        display_label = label or description[:30] + ("..." if len(description) > 30 else "")
        parent = get_current_run_context()
        user_id = parent.user_id if parent else "system"
        selected_agent_id = str(agent_id or (parent.agent_id if parent else "default"))
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
                    },
                    root_run_id=root_run_id,
                    parent_run_id=parent_run_id,
                    parent_task_id=parent_task_id,
                    max_children_per_root=self.max_spawns_per_run,
                    idempotency_key=f"subagent:{parent_run_id}:{parent_task_id}:{task_id}",
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
            },
        )

    @staticmethod
    def _build_prompt(task: str) -> str:
        return (
            "You are a focused child Agent in a distributed workflow. Complete only the "
            "assigned task, use tools when needed, and return a concise evidence-backed "
            f"result to the parent workflow.\n\n## Assigned task\n{task}"
        )
