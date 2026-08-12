"""Owner-scoped controlled GraphPatch use cases."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from hashlib import sha256
from typing import Any

from joyhousebot.application.errors import ConflictError, ValidationError
from joyhousebot.application.graph_patch_commands import (
    ApplyGraphPatchCommand,
    ResolveGraphPatchProposalCommand,
)
from joyhousebot.application.graph_validation import (
    graph_snapshot_scope,
    task_executables,
    validate_graph_catalog,
    validate_patch_snapshot_scope,
)
from joyhousebot.application.run_commands import GraphTaskCommand
from joyhousebot.domain.capabilities import CapabilityRef
from joyhousebot.orchestration.failure_policy import validate_saga_declarations
from joyhousebot.orchestration.task_graph import validate_and_order_graph
from joyhousebot.runtime.graph_revision import (
    freeze_graph_patch_revision,
    graph_task_rows,
)
from joyhousebot.runtime.models import AgentEvent, EventType, GraphTaskSpec, TaskGraphSpec


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return sha256(encoded).hexdigest()


def _task_spec(command: GraphTaskCommand) -> GraphTaskSpec:
    return GraphTaskSpec(
        id=command.id,
        prompt=command.prompt,
        agent_id=command.agent_id,
        dependencies=list(command.dependencies),
        name=command.name or "",
        timeout_seconds=command.timeout_seconds or 300.0,
        max_attempts=command.max_attempts,
        max_input_tokens=command.max_input_tokens,
        max_output_tokens=command.max_output_tokens,
        max_cost_usd=command.max_cost_usd,
        metadata=dict(command.metadata),
        capability=command.capability,
        capability_input=dict(command.capability_input),
        output_schema=dict(command.output_schema) if command.output_schema else None,
        verification_policy=dict(command.verification_policy),
        max_repairs=command.max_repairs,
        allowed_tools=list(command.allowed_tools),
        skill_names=list(command.skill_names),
        node_type=command.node_type,
        branch=dict(command.branch),
        foreach=dict(command.foreach),
        wait_event=dict(command.wait_event),
        approval=dict(command.approval),
        verify=dict(command.verify),
        compensation=dict(command.compensation),
        bounded_loop=dict(command.bounded_loop),
        aggregate=dict(command.aggregate),
        subrun=dict(command.subrun),
    )


def _definition_spec(value: dict[str, Any]) -> GraphTaskSpec:
    return GraphTaskSpec.from_dict({**value, "id": value["node_id"]})


def _risk_level(
    changed: list[GraphTaskSpec], catalog: list[dict[str, Any]]
) -> tuple[str, list[str]]:
    definitions = {CapabilityRef.from_dict(dict(item["ref"])).identity: item for item in catalog}
    level = "low"
    reasons: set[str] = set()
    for task in changed:
        approval_risk = str(task.approval.get("risk") or "low")
        if approval_risk == "high":
            level = "high"
            reasons.add(f"approval:{task.id}:high")
        elif approval_risk == "medium" and level == "low":
            level = "medium"
            reasons.add(f"approval:{task.id}:medium")
        pinned, _, _ = task_executables(task)
        for reference in pinned:
            definition = definitions.get(reference.identity) or {}
            side_effect = str(definition.get("side_effect") or "none")
            classification = str(definition.get("data_classification") or "internal")
            if side_effect not in {"none", "read"}:
                level = "high"
                reasons.add(f"capability:{reference.capability_id}:side_effect")
            if classification == "restricted":
                level = "high"
                reasons.add(f"capability:{reference.capability_id}:restricted")
            elif classification == "confidential" and level == "low":
                level = "medium"
                reasons.add(f"capability:{reference.capability_id}:confidential")
    return level, sorted(reasons)


class GraphPatchService:
    def __init__(self, runtime: Any, runs: Any, store: Any) -> None:
        self.runtime = runtime
        self.runs = runs
        self.store = store

    async def apply(
        self, context: Any, run_id: str, command: ApplyGraphPatchCommand
    ) -> tuple[Any, Any]:
        run = await self.runs.get(context, run_id)
        reason = command.reason.strip()
        if not 1 <= len(reason) <= 2000:
            raise ValidationError("GraphPatch reason must contain 1-2000 characters")
        if not 1 <= len(command.operations) <= 32:
            raise ValidationError("GraphPatch requires 1-32 operations")
        parent = await asyncio.to_thread(
            self.store.get_graph_revision,
            command.base_revision_id,
            expected_user_id=context.user_id,
        )
        if parent is None or parent.run_id != run_id:
            raise ValidationError("GraphPatch base revision does not belong to Run")

        parent_nodes = [dict(item.definition) for item in parent.nodes]
        node_map = {node["node_id"]: _definition_spec(node) for node in parent_nodes}
        changed: list[GraphTaskSpec] = []
        append_ids: list[str] = []
        replace_ids: list[str] = []
        seen: set[str] = set()
        operation_values: list[dict[str, Any]] = []
        for operation in command.operations:
            if operation.op not in {"append", "replace_pending"}:
                raise ValidationError(f"unsupported GraphPatch operation: {operation.op}")
            task = _task_spec(operation.node)
            if task.id in seen:
                raise ValidationError(f"GraphPatch node appears more than once: {task.id}")
            seen.add(task.id)
            if operation.op == "append":
                if task.id in node_map:
                    raise ValidationError(f"GraphPatch append node already exists: {task.id}")
                append_ids.append(task.id)
            else:
                if task.id not in node_map:
                    raise ValidationError(f"GraphPatch replacement node does not exist: {task.id}")
                replace_ids.append(task.id)
            if not 0 < task.timeout_seconds <= 3600 or not 1 <= task.max_attempts <= 20:
                raise ValidationError(f"GraphPatch node limits are invalid: {task.id}")
            node_map[task.id] = task
            changed.append(task)
            operation_values.append({"op": operation.op, "node_id": task.id})

        scope = graph_snapshot_scope(parent_nodes, parent.settings)
        try:
            validate_patch_snapshot_scope(changed, scope)
            ordered = validate_and_order_graph(list(node_map.values()))
            catalog = await asyncio.to_thread(validate_graph_catalog, self.store, ordered)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        settings = parent.settings
        spec = TaskGraphSpec(
            goal=str(settings.get("goal") or run.prompt),
            tasks=ordered,
            user_id=context.user_id,
            session_id=run.session_id,
            agent_id=str(settings.get("agent_id") or run.agent_id),
            agent_revision_id=(
                str(settings["agent_revision_id"])
                if settings.get("agent_revision_id")
                else None
            ),
            max_concurrent=max(1, int(settings.get("max_concurrent") or 1)),
            fail_fast=bool(settings.get("fail_fast", False)),
            failure_policy=dict(settings.get("failure_policy") or {}),
            aggregate=bool(settings.get("aggregate", True)),
            aggregation_policy=dict(settings.get("aggregation_policy") or {}),
            max_input_tokens=(
                int(settings["max_input_tokens"])
                if settings.get("max_input_tokens") is not None
                else None
            ),
            max_output_tokens=(
                int(settings["max_output_tokens"])
                if settings.get("max_output_tokens") is not None
                else None
            ),
            max_cost_usd=(
                float(settings["max_cost_usd"])
                if settings.get("max_cost_usd") is not None
                else None
            ),
            metadata=dict(settings.get("metadata") or {}),
        )
        try:
            validate_saga_declarations(
                ordered,
                catalog,
                spec.failure_policy,
                max_concurrent=spec.max_concurrent,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        proposer_type = str(command.proposer_type or "user")
        if proposer_type not in {"user", "agent", "system"}:
            raise ValidationError("GraphPatch proposer_type is invalid")
        proposer_id = str(command.proposer_id or context.principal.subject)
        revision = freeze_graph_patch_revision(
            run_id,
            parent.revision_id,
            parent.revision_number + 1,
            spec,
            ordered,
            source=("owner_patch" if proposer_type == "user" else "agent_patch_proposal"),
        )
        previous = {node["node_id"]: node for node in parent_nodes}
        current = {node["node_id"]: node for node in revision["nodes"]}
        operation_values = [{**item, "node": current[item["node_id"]]} for item in operation_values]
        unchanged_replacements = [
            node_id for node_id in replace_ids if previous[node_id] == current[node_id]
        ]
        if unchanged_replacements:
            raise ValidationError(
                f"GraphPatch replacements must change the node: {sorted(unchanged_replacements)}"
            )

        risk, risk_reasons = _risk_level(changed, catalog)
        if risk == "high" and not command.approve_high_risk and not command.defer_activation:
            raise ValidationError("high-risk GraphPatch requires approve_high_risk=true")
        diff = {
            "added": append_ids,
            "replaced": replace_ids,
            "unchanged_count": len(current) - len(append_ids) - len(replace_ids),
            "base_spec_hash": parent.spec_hash,
            "result_spec_hash": revision["spec_hash"],
        }
        validation = {
            "acyclic": True,
            "node_count": len(ordered),
            "max_node_count": 128,
            "fan_out_bounded": True,
            "budget_limits_valid": True,
            "snapshot_scope_valid": True,
            "published_capabilities_valid": True,
            "data_classification_not_expanded": True,
            "mutation_scope": "append_or_replace_unstarted",
            "risk": risk,
            "risk_reasons": risk_reasons,
            "approval_required": bool(command.defer_activation),
            "high_risk_approved": (
                risk == "high" and command.approve_high_risk and not command.defer_activation
            ),
            "approved_by": (
                context.principal.subject
                if risk == "high" and command.approve_high_risk and not command.defer_activation
                else None
            ),
        }
        request_value = {
            "run_id": run_id,
            "user_id": context.user_id,
            "base_revision_id": parent.revision_id,
            "reason": reason,
            "operations": operation_values,
            "approve_high_risk": command.approve_high_risk,
            "defer_activation": command.defer_activation,
            "proposer_type": proposer_type,
            "proposer_id": proposer_id,
        }
        request_hash = _canonical_hash(request_value)
        patch_id = f"graphpatch_{request_hash}"
        patch_value = {
            "patch_id": patch_id,
            "run_id": run_id,
            "user_id": context.user_id,
            "base_revision_id": parent.revision_id,
            "proposer_type": proposer_type,
            "proposer_id": proposer_id,
            "reason": reason,
            "operations": operation_values,
            "diff": diff,
            "validation": validation,
            "request_hash": request_hash,
        }
        task_rows = graph_task_rows(run_id, revision)
        if command.defer_activation:
            proposal_value = {
                "proposal_id": f"graphpatchproposal_{request_hash}",
                "run_id": run_id,
                "user_id": context.user_id,
                "base_revision_id": parent.revision_id,
                "proposer_type": proposer_type,
                "proposer_id": proposer_id,
                "reason": reason,
                "operations": operation_values,
                "diff": diff,
                "validation": validation,
                "request_hash": request_hash,
                "candidate_revision": revision,
                "task_rows": task_rows,
                "append_ids": append_ids,
                "replace_ids": replace_ids,
            }
            try:
                proposal, created = await asyncio.to_thread(
                    self.store.propose_graph_patch, proposal=proposal_value
                )
            except ValueError as exc:
                raise ConflictError(str(exc)) from exc
            if created:
                await self.runtime.events.publish(
                    AgentEvent(
                        run_id=run_id,
                        user_id=context.user_id,
                        session_id=run.session_id,
                        agent_id=run.agent_id,
                        type=EventType.GRAPH_PATCH_PROPOSED.value,
                        status="pending",
                        summary="执行图变更等待独立审批",
                        data={
                            "proposal_id": proposal.proposal_id,
                            "base_revision_id": proposal.base_revision_id,
                            "proposer_type": proposer_type,
                            "proposer_id": proposer_id,
                            "risk": risk,
                        },
                    )
                )
            return proposal, run
        try:
            patch, saved_run, created = await asyncio.to_thread(
                self.store.apply_graph_patch,
                patch=patch_value,
                revision=revision,
                task_rows=task_rows,
                append_ids=append_ids,
                replace_ids=replace_ids,
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
        if created:
            await self.runtime.events.publish(
                AgentEvent(
                    run_id=run_id,
                    user_id=context.user_id,
                    session_id=run.session_id,
                    agent_id=run.agent_id,
                    type=EventType.GRAPH_PATCHED.value,
                    status="completed",
                    summary="执行图已安全更新",
                    data={
                        "patch_id": patch.patch_id,
                        "base_revision_id": patch.base_revision_id,
                        "result_revision_id": patch.result_revision_id,
                        "added": append_ids,
                        "replaced": replace_ids,
                        "risk": risk,
                    },
                )
            )
        return patch, saved_run

    async def propose(
        self,
        context: Any,
        run_id: str,
        command: ApplyGraphPatchCommand,
        *,
        proposer_type: str = "user",
        proposer_id: str | None = None,
    ) -> tuple[Any, Any]:
        return await self.apply(
            context,
            run_id,
            replace(
                command,
                defer_activation=True,
                approve_high_risk=False,
                proposer_type=proposer_type,
                proposer_id=proposer_id,
            ),
        )

    async def resolve_proposal(
        self,
        context: Any,
        run_id: str,
        proposal_id: str,
        command: ResolveGraphPatchProposalCommand,
    ) -> tuple[Any, Any | None]:
        run = await self.runs.get(context, run_id)
        resolution = str(command.resolution or "")
        if resolution not in {"approve", "reject"}:
            raise ValidationError("GraphPatch proposal resolution must be approve or reject")
        note = (command.note or "").strip() or None
        if note and len(note) > 4000:
            raise ValidationError("GraphPatch proposal note exceeds 4000 characters")
        proposal = await asyncio.to_thread(
            self.store.get_graph_patch_proposal,
            proposal_id,
            expected_user_id=context.user_id,
        )
        if proposal is None or proposal.run_id != run_id:
            raise ValidationError("GraphPatch proposal does not belong to Run")
        if resolution == "reject":
            rejected = await asyncio.to_thread(
                self.store.reject_graph_patch_proposal,
                proposal_id,
                expected_user_id=context.user_id,
                resolved_by=context.principal.subject,
                note=note,
            )
            if rejected is None:
                raise ConflictError(f"GraphPatch proposal is already {proposal.status}")
            await self._publish_proposal_resolution(run, rejected, status="rejected")
            return rejected, None

        worker_id = f"patch-approval:{context.request_id}"
        claimed = await asyncio.to_thread(
            self.store.claim_graph_patch_proposal,
            proposal_id,
            expected_user_id=context.user_id,
            worker_id=worker_id,
            lease_seconds=60,
        )
        if claimed is None:
            current = await asyncio.to_thread(
                self.store.get_graph_patch_proposal,
                proposal_id,
                expected_user_id=context.user_id,
            )
            if current is not None and current.status == "approved":
                return current, None
            raise ConflictError(
                f"GraphPatch proposal cannot be approved from status "
                f"{current.status if current else 'missing'}"
            )
        validation = {
            **dict(claimed.validation),
            "independent_approval": True,
            "high_risk_approved": claimed.validation.get("risk") == "high",
            "approved_by": context.principal.subject,
        }
        patch_value = {
            "patch_id": f"graphpatch_{claimed.request_hash}",
            "run_id": claimed.run_id,
            "user_id": claimed.user_id,
            "base_revision_id": claimed.base_revision_id,
            "proposer_type": claimed.proposer_type,
            "proposer_id": claimed.proposer_id,
            "reason": claimed.reason,
            "operations": claimed.operations,
            "diff": claimed.diff,
            "validation": validation,
            "request_hash": claimed.request_hash,
        }
        try:
            patch, saved_run, _created = await asyncio.to_thread(
                self.store.apply_graph_patch,
                patch=patch_value,
                revision=claimed.candidate_revision,
                task_rows=claimed.task_rows,
                append_ids=claimed.append_ids,
                replace_ids=claimed.replace_ids,
            )
        except ValueError as exc:
            await asyncio.to_thread(
                self.store.finish_graph_patch_proposal,
                proposal_id,
                expected_user_id=context.user_id,
                worker_id=worker_id,
                lease_version=claimed.lease_version,
                status="activation_failed",
                error={"message": str(exc)},
                resolved_by=context.principal.subject,
                note=note,
            )
            raise ConflictError(str(exc)) from exc
        approved = await asyncio.to_thread(
            self.store.finish_graph_patch_proposal,
            proposal_id,
            expected_user_id=context.user_id,
            worker_id=worker_id,
            lease_version=claimed.lease_version,
            status="approved",
            applied_patch_id=patch.patch_id,
            resolved_by=context.principal.subject,
            note=note,
        )
        if approved is None:
            raise ConflictError("GraphPatch proposal activation lease was lost")
        await self.runtime.events.publish(
            AgentEvent(
                run_id=run_id,
                user_id=context.user_id,
                session_id=run.session_id,
                agent_id=run.agent_id,
                type=EventType.GRAPH_PATCHED.value,
                status="completed",
                summary="审批后的执行图变更已激活",
                data={
                    "proposal_id": proposal_id,
                    "patch_id": patch.patch_id,
                    "base_revision_id": patch.base_revision_id,
                    "result_revision_id": patch.result_revision_id,
                    "risk": validation.get("risk"),
                },
            )
        )
        await self._publish_proposal_resolution(run, approved, status="approved")
        return approved, saved_run

    async def _publish_proposal_resolution(
        self, run: Any, proposal: Any, *, status: str
    ) -> None:
        await self.runtime.events.publish(
            AgentEvent(
                run_id=run.run_id,
                user_id=run.user_id,
                session_id=run.session_id,
                agent_id=run.agent_id,
                type=EventType.GRAPH_PATCH_RESOLVED.value,
                status=status,
                data={
                    "proposal_id": proposal.proposal_id,
                    "resolution": proposal.resolution,
                    "applied_patch_id": proposal.applied_patch_id,
                    "resolved_by": proposal.resolved_by,
                },
            )
        )

    async def list(self, context: Any, run_id: str) -> list[Any]:
        await self.runs.get(context, run_id)
        return await asyncio.to_thread(
            self.store.list_graph_patches,
            run_id,
            expected_user_id=context.user_id,
        )

    async def list_proposals(self, context: Any, run_id: str) -> list[Any]:
        await self.runs.get(context, run_id)
        return await asyncio.to_thread(
            self.store.list_graph_patch_proposals,
            run_id,
            expected_user_id=context.user_id,
        )
