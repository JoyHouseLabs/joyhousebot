"""Controlled immutable GraphPatch behavior and concurrency fencing."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from porthouse.api.app import create_app
from porthouse.application.context import Principal, RequestContext
from porthouse.application.errors import ConflictError, ValidationError
from porthouse.application.graph_patch_commands import (
    ApplyGraphPatchCommand,
    GraphPatchOperationCommand,
    ResolveGraphPatchProposalCommand,
)
from porthouse.application.run_commands import GraphTaskCommand
from porthouse.bootstrap.container import build_api_container
from porthouse.config.schema import Config
from porthouse.domain.capabilities import (
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
)
from porthouse.runtime.models import GraphTaskSpec, TaskGraphSpec
from porthouse.runtime.runner import NativeAgentRuntime
from tests.support.postgres_store import PostgresTestStore


class _RecordingAgent:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def process_direct(self, content: str, **_kwargs):  # noqa: ANN003, ANN201
        self.calls.append(content)
        return f"done:{content}"


def _owner_context() -> RequestContext:
    return RequestContext(
        principal=Principal(subject="user:graph-owner", user_id="graph-owner"),
        request_id="graph-patch-test",
    )


def _patch_body(base_revision_id: str) -> dict:
    return {
        "base_revision_id": base_revision_id,
        "reason": "Refine the pending first step and add a follow-up",
        "operations": [
            {
                "op": "replace_pending",
                "node": {"id": "first", "prompt": "FIRST-PATCHED"},
            },
            {
                "op": "append",
                "node": {
                    "id": "second",
                    "prompt": "SECOND",
                    "dependencies": ["first"],
                },
            },
        ],
    }


def test_graph_patch_api_applies_revision_and_executes_materialized_tasks(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-patch-api.db")
    store.create_api_access_token(user_id="graph-owner", actor_id="test", token="owner-token")
    store.create_api_access_token(user_id="other-owner", actor_id="test", token="other-token")
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    owner = {"Authorization": "Bearer owner-token"}
    with client:
        submitted = client.post(
            "/v1/runs/graphs",
            headers=owner,
            json={
                "goal": "patch a pending graph",
                "session_id": "graph-patch-api",
                "tasks": [{"id": "first", "prompt": "FIRST"}],
            },
        )
        assert submitted.status_code == 202, submitted.text
        run = submitted.json()
        base_revision_id = run["graph_revision_id"]
        applied = client.post(
            f"/v1/runs/{run['run_id']}/graph-patches",
            headers=owner,
            json=_patch_body(base_revision_id),
        )
        duplicate = client.post(
            f"/v1/runs/{run['run_id']}/graph-patches",
            headers=owner,
            json=_patch_body(base_revision_id),
        )
        listed = client.get(f"/v1/runs/{run['run_id']}/graph-patches", headers=owner)
        foreign = client.get(
            f"/v1/runs/{run['run_id']}/graph-patches",
            headers={"Authorization": "Bearer other-token"},
        )

    assert applied.status_code == 201, applied.text
    assert duplicate.status_code == 201, duplicate.text
    assert duplicate.json()["patch"]["patch_id"] == applied.json()["patch"]["patch_id"]
    patch = applied.json()["patch"]
    assert patch["diff"]["added"] == ["second"]
    assert patch["diff"]["replaced"] == ["first"]
    assert patch["validation"]["mutation_scope"] == "append_or_replace_unstarted"
    assert patch["operations"][0]["node"]["prompt"] == "FIRST-PATCHED"
    assert listed.status_code == 200 and len(listed.json()["items"]) == 1
    assert foreign.status_code == 404

    revisions = store.list_graph_revisions(run["run_id"], expected_user_id="graph-owner")
    assert [item.revision_number for item in revisions] == [1, 2]
    assert revisions[1].parent_revision_id == revisions[0].revision_id
    saved_run = store.get_runtime_run(run["run_id"], expected_user_id="graph-owner")
    assert saved_run is not None
    assert saved_run.graph_revision_id == revisions[1].revision_id
    assert saved_run.total_task_count == 2
    tasks = {
        item.payload["spec_id"]: item for item in store.list_runtime_tasks(run_id=run["run_id"])
    }
    assert tasks["first"].payload["prompt"] == "FIRST-PATCHED"
    assert store.get_runtime_task_dependencies(tasks["second"].task_id) == [tasks["first"].task_id]
    events = store.list_runtime_events(run["run_id"])
    assert [item.type for item in events].count("graph.patched") == 1
    with store._pool.connection() as connection:
        with pytest.raises(Exception, match="Graph patches are immutable"):
            with connection.transaction():
                connection.execute(
                    "UPDATE graph_patches SET reason='tampered' WHERE patch_id=%s",
                    (patch["patch_id"],),
                )

    agent = _RecordingAgent()
    runtime = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=1)

    async def execute() -> None:
        await runtime.start()
        completed = await runtime.wait(run["run_id"], timeout=5)
        assert completed.status == "completed", completed.error
        await runtime.close()

    asyncio.run(execute())
    assert "FIRST-PATCHED" in agent.calls
    assert any(call.startswith("SECOND") for call in agent.calls)


@pytest.mark.asyncio
async def test_concurrent_patches_from_same_base_have_one_winner(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "graph-patch-race.db")
    container = build_api_container(config=Config(), store=store)
    context = _owner_context()
    submitted = await container.runtime.submit_graph(
        TaskGraphSpec(
            goal="patch race",
            user_id="graph-owner",
            session_id="patch-race",
            tasks=[GraphTaskSpec(id="root", prompt="ROOT")],
            aggregate=False,
        )
    )
    assert submitted.graph_revision_id

    def command(node_id: str) -> ApplyGraphPatchCommand:
        return ApplyGraphPatchCommand(
            base_revision_id=submitted.graph_revision_id or "",
            reason=f"append {node_id}",
            operations=(
                GraphPatchOperationCommand(
                    op="append",
                    node=GraphTaskCommand(
                        id=node_id,
                        prompt=node_id.upper(),
                        dependencies=["root"],
                    ),
                ),
            ),
        )

    outcomes = await asyncio.gather(
        container.graph_patches.apply(context, submitted.run_id, command("left")),
        container.graph_patches.apply(context, submitted.run_id, command("right")),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert sum(isinstance(item, ConflictError) for item in outcomes) == 1
    patches = store.list_graph_patches(submitted.run_id, expected_user_id="graph-owner")
    revisions = store.list_graph_revisions(submitted.run_id, expected_user_id="graph-owner")
    assert len(patches) == 1
    assert len(revisions) == 2
    await container.close()


@pytest.mark.asyncio
async def test_graph_patch_and_finalizer_claim_are_mutually_fenced(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "graph-patch-finalizer.db")
    container = build_api_container(config=Config(), store=store)
    submitted = await container.runtime.submit_graph(
        TaskGraphSpec(
            goal="finalizer fence",
            user_id="graph-owner",
            session_id="finalizer-fence",
            tasks=[GraphTaskSpec(id="root", prompt="ROOT")],
            aggregate=False,
        )
    )
    assert submitted.graph_revision_id
    assert store.start_runtime_graph(submitted.run_id)
    assert (
        store.claim_runtime_run(submitted.run_id, worker_id="early-finalizer", lease_seconds=30)
        is None
    )
    task = store.claim_runtime_task(worker_id="task-worker", run_id=submitted.run_id)
    assert task is not None
    assert store.update_runtime_task(
        task.task_id,
        status="completed",
        result={"data": "done"},
        worker_id="task-worker",
        lease_version=task.lease_version,
    )
    finalizer = store.claim_runtime_run(
        submitted.run_id, worker_id="real-finalizer", lease_seconds=30
    )
    assert finalizer is not None
    command = ApplyGraphPatchCommand(
        base_revision_id=submitted.graph_revision_id,
        reason="append while finalizer owns the Run",
        operations=(
            GraphPatchOperationCommand(
                op="append",
                node=GraphTaskCommand(id="late", prompt="LATE", dependencies=["root"]),
            ),
        ),
    )
    with pytest.raises(ConflictError, match="active Graph finalization"):
        await container.graph_patches.apply(_owner_context(), submitted.run_id, command)
    assert store.list_graph_patches(submitted.run_id, expected_user_id="graph-owner") == []
    await container.close()


@pytest.mark.asyncio
async def test_high_risk_graph_patch_requires_explicit_owner_confirmation(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-patch-risk.db")
    reference = CapabilityRef(
        "test.patch_write",
        "1.0.0",
        CapabilityKind.TOOL,
        "test.graph-patch",
        "1.0.0",
        "sha256:patch-write",
    )
    store.publish_capability(
        CapabilityDefinition(
            ref=reference,
            name="Patch write",
            description="A test write action",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            adapter="test.patch_write",
            side_effect="external",
        ),
        actor_id="test",
    )
    container = build_api_container(config=Config(), store=store)
    submitted = await container.runtime.submit_graph(
        TaskGraphSpec(
            goal="high-risk patch",
            user_id="graph-owner",
            session_id="high-risk-patch",
            tasks=[
                GraphTaskSpec(
                    id="write",
                    prompt="",
                    node_type="capability",
                    capability=reference,
                    capability_input={"value": "before"},
                )
            ],
            aggregate=False,
        )
    )
    operation = GraphPatchOperationCommand(
        op="replace_pending",
        node=GraphTaskCommand(
            id="write",
            prompt="",
            node_type="capability",
            capability=reference,
            capability_input={"value": "after"},
        ),
    )
    unconfirmed = ApplyGraphPatchCommand(
        base_revision_id=submitted.graph_revision_id or "",
        reason="change a side-effecting input",
        operations=(operation,),
    )
    with pytest.raises(ValidationError, match="approve_high_risk"):
        await container.graph_patches.apply(_owner_context(), submitted.run_id, unconfirmed)
    confirmed = ApplyGraphPatchCommand(
        base_revision_id=submitted.graph_revision_id or "",
        reason=unconfirmed.reason,
        operations=(operation,),
        approve_high_risk=True,
    )
    patch, _ = await container.graph_patches.apply(_owner_context(), submitted.run_id, confirmed)
    assert patch.validation["risk"] == "high"
    assert patch.validation["high_risk_approved"] is True
    assert patch.validation["approved_by"] == "user:graph-owner"
    await container.close()


def test_graph_patch_rejects_started_target_and_snapshot_expansion(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "graph-patch-started.db")
    store.create_api_access_token(user_id="graph-owner", actor_id="test", token="owner-token")
    runtime = NativeAgentRuntime(agent=None, store=store, worker_enabled=False)

    async def submit_and_close():  # noqa: ANN202
        record = await runtime.submit_graph(
            TaskGraphSpec(
                goal="started target",
                user_id="graph-owner",
                session_id="started-target",
                tasks=[GraphTaskSpec(id="first", prompt="FIRST")],
                aggregate=False,
            )
        )
        await runtime.close()
        return record

    submitted = asyncio.run(submit_and_close())
    assert submitted.graph_revision_id
    assert store.start_runtime_graph(submitted.run_id)
    claimed = store.claim_runtime_task(worker_id="test-worker", run_id=submitted.run_id)
    assert claimed is not None and claimed.status == "running"

    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    owner = {"Authorization": "Bearer owner-token"}
    with client:
        started = client.post(
            f"/v1/runs/{submitted.run_id}/graph-patches",
            headers=owner,
            json={
                "base_revision_id": submitted.graph_revision_id,
                "reason": "attempt to rewrite active work",
                "operations": [
                    {
                        "op": "replace_pending",
                        "node": {"id": "first", "prompt": "UNSAFE"},
                    }
                ],
            },
        )
        expanded = client.post(
            f"/v1/runs/{submitted.run_id}/graph-patches",
            headers=owner,
            json={
                "base_revision_id": submitted.graph_revision_id,
                "reason": "attempt to expand frozen Agent scope",
                "operations": [
                    {
                        "op": "append",
                        "node": {
                            "id": "foreign-agent",
                            "prompt": "NEW",
                            "agent_id": "not-in-run-snapshot",
                        },
                    }
                ],
            },
        )
    assert started.status_code == 409
    assert "started node" in started.json()["error"]["message"]
    assert expanded.status_code == 422
    assert "outside Run snapshot" in expanded.json()["error"]["message"]
    assert store.list_graph_patches(submitted.run_id, expected_user_id="graph-owner") == []


def test_graph_patch_proposal_requires_independent_resolution_before_activation(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-patch-proposal-api.db")
    store.create_api_access_token(
        user_id="graph-owner", actor_id="test", token="proposal-owner-token"
    )
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    owner = {"Authorization": "Bearer proposal-owner-token"}
    with client:
        submitted = client.post(
            "/v1/runs/graphs",
            headers=owner,
            json={
                "goal": "approve a proposed graph change",
                "session_id": "graph-patch-proposal-api",
                "tasks": [{"id": "first", "prompt": "FIRST"}],
                "aggregate": False,
            },
        )
        assert submitted.status_code == 202, submitted.text
        run = submitted.json()
        body = _patch_body(run["graph_revision_id"])
        proposed = client.post(
            f"/v1/runs/{run['run_id']}/graph-patch-proposals",
            headers=owner,
            json=body,
        )
        assert proposed.status_code == 201, proposed.text
        proposal = proposed.json()["proposal"]
        assert proposal["status"] == "pending"
        assert proposal["validation"]["approval_required"] is True
        assert "candidate_revision" not in proposal
        before = client.get(f"/v1/runs/{run['run_id']}", headers=owner).json()
        before_tasks = {
            item.payload["spec_id"]: item
            for item in store.list_runtime_tasks(run_id=run["run_id"])
        }
        assert set(before_tasks) == {"first"}
        assert before_tasks["first"].payload["prompt"] == "FIRST"
        listed = client.get(
            f"/v1/runs/{run['run_id']}/graph-patch-proposals", headers=owner
        )
        approved = client.post(
            f"/v1/runs/{run['run_id']}/graph-patch-proposals/"
            f"{proposal['proposal_id']}/resolve",
            headers=owner,
            json={"resolution": "approve", "note": "reviewed frozen diff"},
        )
        duplicate = client.post(
            f"/v1/runs/{run['run_id']}/graph-patch-proposals/"
            f"{proposal['proposal_id']}/resolve",
            headers=owner,
            json={"resolution": "approve"},
        )

    assert before["graph_revision_id"] == run["graph_revision_id"]
    activated_tasks = {
        item.payload["spec_id"]: item
        for item in store.list_runtime_tasks(run_id=run["run_id"])
    }
    assert listed.status_code == 200 and listed.json()["items"][0]["status"] == "pending"
    assert approved.status_code == 200, approved.text
    approved_proposal = approved.json()["proposal"]
    assert approved_proposal["status"] == "approved"
    assert approved_proposal["resolution"] == "approve"
    assert approved_proposal["applied_patch_id"].startswith("graphpatch_")
    assert duplicate.status_code == 200
    assert duplicate.json()["proposal"]["status"] == "approved"
    assert set(activated_tasks) == {"first", "second"}
    assert activated_tasks["first"].payload["prompt"] == "FIRST-PATCHED"
    saved = store.get_runtime_run(run["run_id"], expected_user_id="graph-owner")
    assert saved is not None and saved.graph_revision_id != run["graph_revision_id"]
    events = store.list_runtime_events(run["run_id"])
    assert [event.type for event in events].count("graph.patch_proposed") == 1
    assert [event.type for event in events].count("graph.patch_resolved") == 1
    assert [event.type for event in events].count("graph.patched") == 1


@pytest.mark.asyncio
async def test_agent_graph_patch_proposal_can_be_rejected_without_mutating_graph(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-patch-agent-proposal.db")
    container = build_api_container(config=Config(), store=store)
    context = _owner_context()
    submitted = await container.runtime.submit_graph(
        TaskGraphSpec(
            goal="reject an agent-proposed change",
            user_id="graph-owner",
            session_id="agent-patch-proposal",
            tasks=[GraphTaskSpec(id="root", prompt="ROOT")],
            aggregate=False,
        )
    )
    command = ApplyGraphPatchCommand(
        base_revision_id=submitted.graph_revision_id or "",
        reason="agent detected that another evidence step may help",
        operations=(
            GraphPatchOperationCommand(
                op="append",
                node=GraphTaskCommand(
                    id="extra", prompt="EXTRA", dependencies=["root"]
                ),
            ),
        ),
    )
    proposal, _ = await container.graph_patches.propose(
        context,
        submitted.run_id,
        command,
        proposer_type="agent",
        proposer_id="main-coordinator",
    )
    assert proposal.status == "pending"
    assert proposal.proposer_type == "agent"
    assert proposal.proposer_id == "main-coordinator"
    rejected, run = await container.graph_patches.resolve_proposal(
        context,
        submitted.run_id,
        proposal.proposal_id,
        ResolveGraphPatchProposalCommand(
            resolution="reject", note="scope expansion is unnecessary"
        ),
    )
    assert rejected.status == "rejected" and run is None
    saved = store.get_runtime_run(submitted.run_id, expected_user_id="graph-owner")
    assert saved is not None
    assert saved.graph_revision_id == submitted.graph_revision_id
    assert {task.payload["spec_id"] for task in store.list_runtime_tasks(run_id=submitted.run_id)} == {
        "root"
    }
    assert store.list_graph_patches(
        submitted.run_id, expected_user_id="graph-owner"
    ) == []
    await container.close()
