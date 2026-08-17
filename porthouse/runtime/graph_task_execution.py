"""Durable execution state machine for one claimed Graph Task."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from porthouse.domain.capabilities import CapabilityRef, InvocationStatus
from porthouse.runtime.action_identity import durable_turn_id
from porthouse.runtime.artifact_materialization import materialize_capability_artifacts
from porthouse.runtime.context import (
    ActionApprovalRequiredError,
    ActionOutcomeUnknownError,
    CancellationToken,
    RunContext,
    VerificationFailedError,
)
from porthouse.runtime.graph_aggregate_execution import execute_graph_aggregate
from porthouse.runtime.graph_bounded_loop_execution import execute_graph_bounded_loop
from porthouse.runtime.graph_branch_execution import (
    execute_graph_branch,
    graph_task_prompt,
)
from porthouse.runtime.graph_capability_execution import execute_graph_capability
from porthouse.runtime.graph_compensation_execution import (
    complete_graph_compensation,
    prepare_graph_compensation,
    publish_graph_compensation_failed,
)
from porthouse.runtime.graph_control_execution import (
    execute_graph_approval,
    execute_graph_verify,
)
from porthouse.runtime.graph_foreach_execution import execute_graph_foreach
from porthouse.runtime.graph_reconciliation import reconcile_after_graph_task
from porthouse.runtime.graph_subrun_execution import execute_graph_subrun
from porthouse.runtime.graph_task_lifecycle import (
    graph_task_heartbeat,
    publish_task_started,
)
from porthouse.runtime.graph_wait_event_execution import execute_graph_wait_event
from porthouse.runtime.models import AgentEvent, AgentUsage, EventType, TaskStatus
from porthouse.runtime.verification import verify_output


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    return int(value) if value is not None else None


def _optional_float(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    return float(value) if value is not None else None


@dataclass(slots=True)
class GraphTaskExecutionState:
    capability: CapabilityRef | None
    capability_result: Any = None
    direct_turn_id: str | None = None
    suspended: bool = False
    result_metadata: dict[str, Any] = field(default_factory=dict)
    structured_output: Any = None
    content: str | None = None
    tools: list[str] = field(default_factory=list)
    usage: AgentUsage = field(default_factory=AgentUsage)
    node_type: str = "agent"


class GraphTaskExecutionMixin:
    async def _execute_claimed_graph_task(self, task: Any) -> None:
        run = await self._load_executable_graph_run(task)
        if run is None:
            return
        cancellation = CancellationToken()
        owner_task = asyncio.current_task()
        heartbeat = asyncio.create_task(
            graph_task_heartbeat(self, run, task, cancellation, owner_task),
            name=f"task-heartbeat:{task.task_id}",
        )
        await publish_task_started(self, task, run)
        state = GraphTaskExecutionState(
            capability=(
                CapabilityRef.from_dict(dict(task.payload["capability"]))
                if task.payload.get("capability")
                else None
            )
        )
        try:
            await self._run_graph_task_node(run, task, state, cancellation)
        except asyncio.CancelledError:
            raise
        except (ActionApprovalRequiredError, ActionOutcomeUnknownError) as exc:
            await self._finish_suspended_direct_turn(state.direct_turn_id, exc)
            await self._suspend_graph_action(run, task, exc, cancellation)
            state.suspended = True
        except Exception as exc:
            await self._fail_or_retry_graph_task(
                run,
                task,
                exc,
                capability=state.capability,
                capability_result=state.capability_result,
                direct_turn_id=state.direct_turn_id,
            )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        await reconcile_after_graph_task(self, run, task=task, suspended=state.suspended)

    async def _run_graph_task_node(
        self,
        run: Any,
        task: Any,
        state: GraphTaskExecutionState,
        cancellation: CancellationToken,
    ) -> None:
        prompt, dependency_context = await graph_task_prompt(self, task)
        spec_id = str(task.payload.get("spec_id") or task.task_id)
        state.node_type = str(task.payload.get("node_type") or "agent")
        handled = await self._execute_graph_control_node(
            run, task, state, prompt, dependency_context, cancellation
        )
        if not handled and state.capability is not None:
            await self._execute_graph_capability_node(
                run, task, state, dependency_context, spec_id, cancellation
            )
        elif not handled:
            await self._execute_graph_agent_node(
                run, task, state, prompt, spec_id, cancellation
            )
        if not state.suspended and state.node_type not in {
            "branch",
            "bounded_loop",
            "foreach",
            "wait_event",
            "approval",
            "verify",
        }:
            await self._complete_graph_task(
                run,
                task,
                content=state.content,
                tools=state.tools,
                usage=state.usage,
                capability_result=state.capability_result,
                result_metadata=state.result_metadata,
                structured_output_override=state.structured_output,
            )

    async def _execute_graph_control_node(
        self,
        run: Any,
        task: Any,
        state: GraphTaskExecutionState,
        prompt: str,
        dependency_context: dict[str, Any],
        cancellation: CancellationToken,
    ) -> bool:
        node_type = state.node_type
        if node_type == "branch":
            await execute_graph_branch(self, run, task, dependency_context)
        elif node_type == "bounded_loop":
            await execute_graph_bounded_loop(self, run, task, dependency_context)
        elif node_type == "foreach":
            await execute_graph_foreach(self, run, task, dependency_context)
        elif node_type == "wait_event":
            await execute_graph_wait_event(self, run, task)
            state.suspended = True
        elif node_type == "approval":
            await execute_graph_approval(self, run, task, dependency_context)
            state.suspended = True
        elif node_type == "verify":
            await execute_graph_verify(self, run, task, dependency_context)
        elif node_type == "aggregate":
            (
                state.content,
                state.tools,
                state.usage,
                state.structured_output,
                state.result_metadata,
            ) = await execute_graph_aggregate(
                self, run, task, dependency_context, cancellation
            )
        elif node_type == "subrun":
            result = await execute_graph_subrun(
                self, run, task, prompt, dependency_context
            )
            if result is None:
                state.suspended = True
            else:
                (
                    state.content,
                    state.tools,
                    state.usage,
                    state.structured_output,
                    state.result_metadata,
                ) = result
        else:
            return False
        return True

    async def _execute_graph_capability_node(
        self,
        run: Any,
        task: Any,
        state: GraphTaskExecutionState,
        dependency_context: dict[str, Any],
        spec_id: str,
        cancellation: CancellationToken,
    ) -> None:
        compensation_context = (
            await prepare_graph_compensation(self, run, task)
            if state.node_type == "compensation"
            else None
        )
        state.direct_turn_id = durable_turn_id(run.run_id, task.task_id, task.attempt)
        (
            state.content,
            state.tools,
            state.usage,
            state.capability_result,
            returned_turn_id,
        ) = await execute_graph_capability(
            self,
            run,
            task,
            state.capability,
            dependency_context,
            spec_id,
            cancellation,
        )
        if returned_turn_id != state.direct_turn_id:
            raise RuntimeError("Graph Task durable Turn identity changed")
        if compensation_context is not None:
            state.result_metadata = await complete_graph_compensation(
                self, run, task, compensation_context
            )
        await self._verify_graph_capability_output(
            run, task, state.content, turn_id=state.direct_turn_id
        )
        await asyncio.to_thread(
            self.stores.execution.finish_runtime_turn,
            state.direct_turn_id,
            status="completed",
            stop_reason="capability_completed",
        )

    async def _execute_graph_agent_node(
        self,
        run: Any,
        task: Any,
        state: GraphTaskExecutionState,
        prompt: str,
        spec_id: str,
        cancellation: CancellationToken,
    ) -> None:
        metadata = dict(task.payload.get("metadata") or {})
        state.content, state.tools, state.usage = await self._call_agent(
            run_id=run.run_id,
            task_id=task.task_id,
            prompt=prompt,
            user_id=run.user_id,
            session_id=f"{run.session_id}:task:{spec_id}",
            agent_id=task.agent_id,
            agent_revision_id=str(metadata.get("agent_revision_id") or "") or None,
            channel="runtime",
            chat_id=spec_id,
            model=None,
            system_prompt=None,
            output_schema=(
                dict(task.payload["output_schema"])
                if task.payload.get("output_schema")
                else None
            ),
            timeout_seconds=float(task.payload.get("timeout_seconds") or 300),
            max_turns=None,
            max_input_tokens=_optional_int(task.payload, "max_input_tokens"),
            max_output_tokens=_optional_int(task.payload, "max_output_tokens"),
            max_cost_usd=_optional_float(task.payload, "max_cost_usd"),
            permission_mode="default",
            allowed_tools=[str(item) for item in task.payload.get("allowed_tools") or []],
            disallowed_tools=[],
            cancellation=cancellation,
            metadata={
                **metadata,
                "skill_names": list(task.payload.get("skill_names") or []),
            },
            verification_policy=dict(task.payload.get("verification_policy") or {}),
            max_repairs=_optional_int(task.payload, "max_repairs"),
            task_lease_version=task.lease_version,
        )

    async def _finish_suspended_direct_turn(
        self,
        direct_turn_id: str | None,
        exc: ActionApprovalRequiredError | ActionOutcomeUnknownError,
    ) -> None:
        if not direct_turn_id:
            return
        approval_required = isinstance(exc, ActionApprovalRequiredError)
        await asyncio.to_thread(
            self.stores.execution.finish_runtime_turn,
            direct_turn_id,
            status="waiting_approval" if approval_required else "waiting_external",
            stop_reason="approval_required" if approval_required else "outcome_unknown",
        )

    async def _load_executable_graph_run(self, task: Any) -> Any | None:
        run = await asyncio.to_thread(
            self.stores.runs.get_runtime_run, task.run_id
        )
        if (
            run is None
            or run.kind != "graph"
            or run.status
            not in {
                "queued",
                "running",
                "waiting_approval",
                "waiting_external",
            }
        ):
            await asyncio.to_thread(
                self.stores.tasks.update_runtime_task,
                task.task_id,
                status=TaskStatus.CANCELLED.value,
                error={"message": "parent run is not executable"},
                worker_id=self.worker_id,
                lease_version=task.lease_version,
            )
            return None
        await self._start_graph_if_needed(run)
        return run

    async def _start_graph_if_needed(self, run: Any) -> None:
        started = await asyncio.to_thread(
            self.stores.graphs.start_runtime_graph, run.run_id
        )
        if not started:
            return
        await self.events.publish(
            AgentEvent(
                run_id=run.run_id,
                type=EventType.RUN_STARTED.value,
                data={"kind": "graph", "distributed": True},
            )
        )
        await self._log(run.run_id, "graph.started", "Distributed graph execution started")

    async def _verify_graph_capability_output(
        self, run: Any, task: Any, content: str, *, turn_id: str
    ) -> None:
        policy = dict(task.payload.get("verification_policy") or {})
        schema = dict(task.payload["output_schema"]) if task.payload.get("output_schema") else None
        if not policy and schema is None:
            return

        async def _event(kind: str, data: dict[str, Any]) -> None:
            event_type = {
                "verification_started": EventType.VERIFICATION_STARTED.value,
                "verification_passed": EventType.VERIFICATION_PASSED.value,
                "verification_failed": EventType.VERIFICATION_FAILED.value,
            }[kind]
            await self.events.publish(
                AgentEvent(
                    run_id=run.run_id,
                    task_id=task.task_id,
                    turn_id=turn_id,
                    type=event_type,
                    event_id=f"{data['verification_id']}:{event_type}",
                    status=("failed" if kind == "verification_failed" else "completed"),
                    data=data,
                )
            )

        context = RunContext(
            run_id=run.run_id,
            task_id=task.task_id,
            root_run_id=run.root_run_id or run.run_id,
            user_id=run.user_id,
            agent_id=task.agent_id,
            session_key=f"{run.user_id}:{task.agent_id}:{run.session_id}",
            session_id=run.session_id,
            channel="runtime",
            chat_id=str(task.payload.get("spec_id") or task.task_id),
            trace_store=self.stores.execution,
            output_schema=schema,
            verification_policy=policy,
            worker_id=self.worker_id,
            task_lease_version=task.lease_version,
        )
        decision = await verify_output(
            context,
            content,
            turn_id=turn_id,
            attempt=task.attempt,
            event_callback=_event,
        )
        if not decision.passed:
            raise VerificationFailedError(decision.failures, decision.attempt)

    async def _complete_graph_task(
        self,
        run: Any,
        task: Any,
        *,
        content: str | None,
        tools: list[str],
        usage: AgentUsage,
        capability_result: Any,
        result_metadata: dict[str, Any] | None = None,
        structured_output_override: Any = None,
    ) -> None:
        structured_output = None
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, (dict, list)):
                    structured_output = parsed
            except (TypeError, ValueError):
                pass
        if structured_output_override is not None:
            structured_output = structured_output_override
        value = {
            "status": "completed",
            "content": content,
            "structured_output": structured_output,
            "tools_used": tools,
            "usage": usage.to_dict(),
            "capability_result": (
                capability_result.to_dict() if capability_result is not None else None
            ),
            **dict(result_metadata or {}),
        }
        if capability_result is not None:
            await asyncio.to_thread(
                materialize_capability_artifacts,
                self.stores.execution,
                run_id=run.run_id,
                task_id=task.task_id,
                agent_id=task.agent_id,
                capability_result=capability_result,
                capability_id=(tools[0] if len(tools) == 1 else None),
            )
        await asyncio.to_thread(
            self.stores.execution.add_runtime_artifact,
            artifact_id=f"{task.task_id}:output",
            run_id=run.run_id,
            task_id=task.task_id,
            name=f"{task.name}-output",
            media_type="text/plain",
            content=content,
        )
        completion_event = await self.events.prepare(
            AgentEvent(
                run_id=run.run_id,
                task_id=task.task_id,
                type=EventType.TASK_COMPLETED.value,
                status=TaskStatus.COMPLETED.value,
                data=value,
            )
        )
        task_metadata = dict(task.payload.get("metadata") or {})
        team_ref = task_metadata.get("team_ref")
        workspace_entry = None
        context_policy = dict(task_metadata.get("team_context_policy") or {})
        if (
            isinstance(team_ref, dict)
            and task_metadata.get("team_member_id")
            and bool(context_policy.get("workspace_enabled", True))
        ):
            max_entry_chars = max(
                500,
                min(int(context_policy.get("max_entry_chars") or 6000), 100000),
            )
            workspace_structured = structured_output
            if structured_output is not None:
                encoded_structured = json.dumps(
                    structured_output, ensure_ascii=False, default=str
                )
                if len(encoded_structured) > max_entry_chars:
                    workspace_structured = {
                        "truncated": True,
                        "preview": encoded_structured[:max_entry_chars],
                    }
            workspace_entry = {
                "entry_id": f"teamws:{task.task_id}:output",
                "user_id": run.user_id,
                "root_run_id": str(
                    task_metadata.get("team_workspace_run_id") or run.run_id
                ),
                "team_id": str(team_ref.get("team_id") or ""),
                "team_revision_id": str(team_ref.get("revision_id") or ""),
                "source_run_id": run.run_id,
                "source_task_id": task.task_id,
                "member_id": str(task_metadata["team_member_id"]),
                "entry_type": "task_result",
                "summary": str(content or task.name)[:2000],
                "data": {
                    "content": str(content or "")[:max_entry_chars],
                    "structured_output": workspace_structured,
                    "tools_used": list(tools),
                    "usage": usage.to_dict(),
                    "artifact_id": f"{task.task_id}:output",
                },
                "visibility": str(
                    context_policy.get("default_visibility") or "team"
                ),
            }
        saved = await asyncio.to_thread(
            self.stores.tasks.update_runtime_task,
            task.task_id,
            status=TaskStatus.COMPLETED.value,
            result=value,
            worker_id=self.worker_id,
            lease_version=task.lease_version,
            event=completion_event,
            workspace_entry=workspace_entry,
        )
        if not saved:
            raise asyncio.CancelledError("task completion fenced by a newer lease")
        # The event committed with the Task transition. Publishing it again is
        # idempotent and only performs live fanout; replay remains PostgreSQL-backed.
        await self.events.publish(completion_event)
        await self._log(
            run.run_id,
            "task.completed",
            "Graph task completed",
            task_id=task.task_id,
            data={"attempt": task.attempt, "usage": usage.to_dict()},
        )

    async def _suspend_graph_action(
        self,
        run: Any,
        task: Any,
        exc: ActionApprovalRequiredError | ActionOutcomeUnknownError,
        cancellation: CancellationToken,
    ) -> None:
        if isinstance(exc, ActionApprovalRequiredError):
            transitioned = await asyncio.to_thread(
                self.stores.graphs.suspend_graph_task_for_approval,
                run_id=run.run_id,
                task_id=task.task_id,
                approval_id=exc.approval_id,
                action_id=exc.action_id,
                worker_id=self.worker_id,
                task_lease_version=task.lease_version,
            )
            event_type = EventType.APPROVAL_REQUESTED.value
            waiting_type = EventType.RUN_WAITING_APPROVAL.value
            data = {
                "approval_id": exc.approval_id,
                "action_id": exc.action_id,
                "required_role": exc.required_role,
                "waiting_on": exc.approval_id,
            }
        else:
            reconciliation = await asyncio.to_thread(
                self.stores.reconciliations.get_action_reconciliation,
                exc.action_id,
            )
            if reconciliation is None:
                raise RuntimeError(f"Action reconciliation missing: {exc.action_id}")
            transitioned = await asyncio.to_thread(
                self.stores.graphs.suspend_graph_task_for_reconciliation,
                run_id=run.run_id,
                task_id=task.task_id,
                reconciliation_id=reconciliation.reconciliation_id,
                action_id=exc.action_id,
                invocation_id=exc.invocation_id,
                worker_id=self.worker_id,
                task_lease_version=task.lease_version,
            )
            event_type = EventType.OPERATION_RECONCILIATION_REQUESTED.value
            waiting_type = EventType.RUN_WAITING_EXTERNAL.value
            data = {
                "reconciliation_id": reconciliation.reconciliation_id,
                "action_id": exc.action_id,
                "invocation_id": exc.invocation_id,
                "required_role": reconciliation.required_role,
                "waiting_on": reconciliation.reconciliation_id,
            }
        if not transitioned:
            cancellation.cancel("Graph Task state changed while suspending Action")
            raise asyncio.CancelledError(cancellation.reason)
        await self.events.publish(
            AgentEvent(
                run_id=run.run_id,
                task_id=task.task_id,
                type=event_type,
                status="pending",
                data=data,
            )
        )
        await self.events.publish(
            AgentEvent(
                run_id=run.run_id,
                task_id=task.task_id,
                type=waiting_type,
                status=(
                    "waiting_approval"
                    if isinstance(exc, ActionApprovalRequiredError)
                    else "waiting_external"
                ),
                data=data,
            )
        )
        await self._log(
            run.run_id,
            waiting_type,
            str(exc),
            task_id=task.task_id,
            data=data,
        )

    async def _fail_or_retry_graph_task(
        self,
        run: Any,
        task: Any,
        exc: Exception,
        *,
        capability: CapabilityRef | None,
        capability_result: Any,
        direct_turn_id: str | None,
    ) -> None:
        successful_side_effect = (
            capability_result is not None and capability_result.status == InvocationStatus.SUCCEEDED
        )
        if str(task.payload.get("node_type") or "") == "compensation":
            await publish_graph_compensation_failed(self, run, task, exc)
        retry = task.attempt < task.max_attempts and not (
            successful_side_effect and isinstance(exc, VerificationFailedError)
        )
        if direct_turn_id:
            await asyncio.to_thread(
                self.stores.execution.finish_runtime_turn,
                direct_turn_id,
                status="failed",
                stop_reason="task_retry" if retry else "task_failed",
                error={"message": str(exc)},
            )
        if capability is not None:
            await self.events.publish(
                AgentEvent(
                    run_id=run.run_id,
                    task_id=task.task_id,
                    type=EventType.CAPABILITY_FAILED.value,
                    status="failed",
                    data={
                        "capability_id": capability.capability_id,
                        "invocation_id": (
                            capability_result.invocation_id
                            if capability_result is not None
                            else None
                        ),
                        "error": str(exc),
                        "retry": retry,
                    },
                )
            )
        status = TaskStatus.QUEUED.value if retry else TaskStatus.FAILED.value
        event_type = EventType.TASK_QUEUED if retry else EventType.TASK_FAILED
        transition_event = await self.events.prepare(
            AgentEvent(
                run_id=run.run_id,
                task_id=task.task_id,
                type=event_type.value,
                status=status,
                data={"attempt": task.attempt, "error": str(exc), "retry": retry},
            )
        )
        saved = await asyncio.to_thread(
            self.stores.tasks.update_runtime_task,
            task.task_id,
            status=status,
            result=(
                {"stop_reason": "retry_scheduled", "previous_error": str(exc)} if retry else None
            ),
            error={"message": str(exc)},
            retry_delay_seconds=(min(30.0, 2 ** max(0, task.attempt - 1)) if retry else None),
            worker_id=self.worker_id,
            lease_version=task.lease_version,
            event=transition_event,
        )
        if not saved:
            return
        await self.events.publish(transition_event)
        await self._log(
            run.run_id,
            "task.retry" if retry else "task.failed",
            str(exc),
            level="warning" if retry else "error",
            task_id=task.task_id,
            data={"attempt": task.attempt, "retry": retry},
        )
