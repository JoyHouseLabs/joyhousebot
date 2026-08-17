"""Owner-scoped controlled GraphPatch use cases."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from porthouse.application.errors import ConflictError, ValidationError
from porthouse.application.graph_patch_commands import (
    ApplyGraphPatchCommand,
    ResolveGraphPatchProposalCommand,
)
from porthouse.application.graph_patch_preparation import (
    GraphPatchPreparation,
    prepare_graph_patch,
)
from porthouse.runtime.models import AgentEvent, EventType
from porthouse.storage.contracts import RuntimeStores


class GraphPatchService:
    def __init__(self, runtime: Any, runs: Any, store: object) -> None:
        self.runtime = runtime
        self.runs = runs
        self.stores = RuntimeStores.from_backend(store)

    async def apply(
        self, context: Any, run_id: str, command: ApplyGraphPatchCommand
    ) -> tuple[Any, Any]:
        run = await self.runs.get(context, run_id)
        prepared = await prepare_graph_patch(
            self.stores,
            run=run,
            context=context,
            command=command,
        )
        if command.defer_activation:
            proposal = await self._save_proposal(
                context, run, prepared
            )
            return proposal, run
        return await self._activate_patch(context, run, prepared)

    async def _save_proposal(
        self, context: Any, run: Any, prepared: GraphPatchPreparation
    ) -> Any:
        try:
            proposal, created = await asyncio.to_thread(
                self.stores.graph_patches.propose_graph_patch,
                proposal=prepared.proposal_value,
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
        if created:
            await self.runtime.events.publish(
                AgentEvent(
                    run_id=run.run_id,
                    user_id=context.user_id,
                    session_id=run.session_id,
                    agent_id=run.agent_id,
                    type=EventType.GRAPH_PATCH_PROPOSED.value,
                    status="pending",
                    summary="执行图变更等待独立审批",
                    data={
                        "proposal_id": proposal.proposal_id,
                        "base_revision_id": proposal.base_revision_id,
                        "proposer_type": prepared.proposer_type,
                        "proposer_id": prepared.proposer_id,
                        "risk": prepared.risk,
                    },
                )
            )
        return proposal

    async def _activate_patch(
        self, context: Any, run: Any, prepared: GraphPatchPreparation
    ) -> tuple[Any, Any]:
        try:
            patch, saved_run, created = await asyncio.to_thread(
                self.stores.graph_patches.apply_graph_patch,
                patch=prepared.patch_value,
                revision=prepared.revision,
                task_rows=list(prepared.task_rows),
                append_ids=list(prepared.append_ids),
                replace_ids=list(prepared.replace_ids),
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
        if created:
            await self.runtime.events.publish(
                AgentEvent(
                    run_id=run.run_id,
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
                        "added": list(prepared.append_ids),
                        "replaced": list(prepared.replace_ids),
                        "risk": prepared.risk,
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
        resolution, note = _validate_resolution(command)
        proposal = await asyncio.to_thread(
            self.stores.graph_patches.get_graph_patch_proposal,
            proposal_id,
            expected_user_id=context.user_id,
        )
        if proposal is None or proposal.run_id != run_id:
            raise ValidationError("GraphPatch proposal does not belong to Run")
        if resolution == "reject":
            return await self._reject_proposal(context, run, proposal, note=note)
        return await self._approve_proposal(context, run, proposal, note=note)

    async def _reject_proposal(
        self, context: Any, run: Any, proposal: Any, *, note: str | None
    ) -> tuple[Any, None]:
        rejected = await asyncio.to_thread(
            self.stores.graph_patches.reject_graph_patch_proposal,
            proposal.proposal_id,
            expected_user_id=context.user_id,
            resolved_by=context.principal.subject,
            note=note,
        )
        if rejected is None:
            raise ConflictError(
                f"GraphPatch proposal is already {proposal.status}"
            )
        await self._publish_proposal_resolution(run, rejected, status="rejected")
        return rejected, None

    async def _approve_proposal(
        self, context: Any, run: Any, proposal: Any, *, note: str | None
    ) -> tuple[Any, Any | None]:
        worker_id = f"patch-approval:{context.request_id}"
        claimed = await asyncio.to_thread(
            self.stores.graph_patches.claim_graph_patch_proposal,
            proposal.proposal_id,
            expected_user_id=context.user_id,
            worker_id=worker_id,
            lease_seconds=60,
        )
        if claimed is None:
            return await self._resolve_unclaimed_approval(context, proposal)
        validation = {
            **dict(claimed.validation),
            "independent_approval": True,
            "high_risk_approved": claimed.validation.get("risk") == "high",
            "approved_by": context.principal.subject,
        }
        patch_value = _approved_patch_value(claimed, validation)
        try:
            patch, saved_run, _created = await asyncio.to_thread(
                self.stores.graph_patches.apply_graph_patch,
                patch=patch_value,
                revision=claimed.candidate_revision,
                task_rows=claimed.task_rows,
                append_ids=claimed.append_ids,
                replace_ids=claimed.replace_ids,
            )
        except ValueError as exc:
            await self._finish_failed_approval(
                context, claimed, worker_id=worker_id, note=note, error=exc
            )
            raise ConflictError(str(exc)) from exc
        approved = await asyncio.to_thread(
            self.stores.graph_patches.finish_graph_patch_proposal,
            proposal.proposal_id,
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
        await self._publish_activated_patch(
            context, run, proposal.proposal_id, patch, validation
        )
        await self._publish_proposal_resolution(run, approved, status="approved")
        return approved, saved_run

    async def _resolve_unclaimed_approval(
        self, context: Any, proposal: Any
    ) -> tuple[Any, None]:
        current = await asyncio.to_thread(
            self.stores.graph_patches.get_graph_patch_proposal,
            proposal.proposal_id,
            expected_user_id=context.user_id,
        )
        if current is not None and current.status == "approved":
            return current, None
        raise ConflictError(
            "GraphPatch proposal cannot be approved from status "
            f"{current.status if current else 'missing'}"
        )

    async def _finish_failed_approval(
        self,
        context: Any,
        claimed: Any,
        *,
        worker_id: str,
        note: str | None,
        error: ValueError,
    ) -> None:
        await asyncio.to_thread(
            self.stores.graph_patches.finish_graph_patch_proposal,
            claimed.proposal_id,
            expected_user_id=context.user_id,
            worker_id=worker_id,
            lease_version=claimed.lease_version,
            status="activation_failed",
            error={"message": str(error)},
            resolved_by=context.principal.subject,
            note=note,
        )

    async def _publish_activated_patch(
        self,
        context: Any,
        run: Any,
        proposal_id: str,
        patch: Any,
        validation: dict[str, Any],
    ) -> None:
        await self.runtime.events.publish(
            AgentEvent(
                run_id=run.run_id,
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
            self.stores.graph_patches.list_graph_patches,
            run_id,
            expected_user_id=context.user_id,
        )

    async def list_proposals(self, context: Any, run_id: str) -> list[Any]:
        await self.runs.get(context, run_id)
        return await asyncio.to_thread(
            self.stores.graph_patches.list_graph_patch_proposals,
            run_id,
            expected_user_id=context.user_id,
        )


def _validate_resolution(
    command: ResolveGraphPatchProposalCommand,
) -> tuple[str, str | None]:
    resolution = str(command.resolution or "")
    if resolution not in {"approve", "reject"}:
        raise ValidationError(
            "GraphPatch proposal resolution must be approve or reject"
        )
    note = (command.note or "").strip() or None
    if note and len(note) > 4000:
        raise ValidationError("GraphPatch proposal note exceeds 4000 characters")
    return resolution, note


def _approved_patch_value(
    claimed: Any, validation: dict[str, Any]
) -> dict[str, Any]:
    return {
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
