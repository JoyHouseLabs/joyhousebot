"""Governed Memory candidate creation, merge, conflicts, and owner API."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from joyhousebot.agent.executor import NativeAgentExecutor
from joyhousebot.agent.memory import MemoryStore
from joyhousebot.agent.memory_candidates import MemoryWriteController
from joyhousebot.agent.memory_policy import EffectiveMemoryPolicy
from joyhousebot.agent.tools.filesystem import WriteFileTool
from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from joyhousebot.domain.agents import AgentRevision
from joyhousebot.providers.base import LLMProvider, LLMResponse
from joyhousebot.runtime.context import RunContext, ToolExecutionContext
from joyhousebot.session.models import Session
from joyhousebot.session.runtime_manager import RuntimeSessionManager
from tests.support.postgres_store import PostgresTestStore


def _candidate_policy(**retrieval: Any) -> dict[str, Any]:
    return {
        "enabled": True,
        "mode": "personalized",
        "read_mode": "auto",
        "write_mode": "candidate",
        "layers": {
            "profile": {"read": True, "write": True},
            "long_term": {"read": True, "write": True},
            "episodic": {"read": True, "write": True},
        },
        "retrieval": retrieval,
    }


def _tool_context(user_id: str = "memory-owner") -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id="run-memory-candidate",
        task_id="task-memory-candidate",
        turn_id="turn-memory-candidate",
        action_id="action-memory-candidate",
        session_key="api:memory-owner:default:session",
        session_id="session",
        channel="api",
        chat_id="runtime",
        user_id=user_id,
        agent_id="default",
        memory_scope=f"user:{user_id}:agent:default",
        memory_policy=_candidate_policy(candidate_confidence=0.8),
    )


@pytest.mark.asyncio
async def test_candidate_tool_write_does_not_mutate_memory_until_accepted(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "candidate-tool.db")
    context = _tool_context()
    writer = WriteFileTool(
        allowed_dir=tmp_path,
        workspace=tmp_path,
        runtime_store=store,
    )

    output = await writer.execute(
        path="memory/MEMORY.md",
        content="The owner prefers concise weekly reports.",
        tool_context=context,
    )

    assert "Memory update candidate created" in output
    assert MemoryStore(store, context.memory_scope).read_long_term() == ""
    candidates = store.list_memory_candidates(user_id=context.user_id)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source_action_id == context.action_id
    assert candidate.fact_type == "long_term"
    assert candidate.confidence == 0.8
    assert candidate.base_document_version == 0
    assert len(candidate.content_hash) == 64

    merged, outcome = store.resolve_memory_candidate(
        candidate_id=candidate.candidate_id,
        user_id=context.user_id,
        resolution="accept",
        actor_id="test:owner",
    )

    assert outcome == "merged"
    assert merged is not None and merged.status == "merged"
    assert "concise weekly reports" in MemoryStore(
        store, context.memory_scope
    ).read_long_term()


def test_replace_candidate_detects_document_change_instead_of_overwriting(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "candidate-conflict.db")
    context = _tool_context()
    scope = str(context.memory_scope)
    memory = MemoryStore(store, scope)
    memory.write_long_term("version one")
    controller = MemoryWriteController(
        store,
        scope_key=scope,
        policy=EffectiveMemoryPolicy.from_dict(context.memory_policy),
        context=context,
    )
    receipt = controller.replace(
        "MEMORY.md", "candidate version", source_kind="test.replace"
    )
    memory.write_long_term("concurrent version")

    candidate, outcome = store.resolve_memory_candidate(
        candidate_id=str(receipt.candidate_id),
        user_id=context.user_id,
        resolution="accept",
        actor_id="test:owner",
    )

    assert outcome == "document_conflict"
    assert candidate is not None and candidate.status == "conflicted"
    assert memory.read_long_term() == "concurrent version"
    rejected, outcome = store.resolve_memory_candidate(
        candidate_id=str(receipt.candidate_id),
        user_id=context.user_id,
        resolution="reject",
        actor_id="test:owner",
    )
    assert outcome == "rejected"
    assert rejected is not None and rejected.status == "rejected"


def test_candidate_writer_rejects_a_foreign_canonical_scope(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "candidate-scope.db")
    context = _tool_context()

    with pytest.raises(PermissionError, match="authenticated Run owner"):
        MemoryWriteController(
            store,
            scope_key="user:another-owner:agent:default",
            policy=EffectiveMemoryPolicy.from_dict(context.memory_policy),
            context=context,
        )


def test_concurrent_accept_appends_exactly_once(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "candidate-concurrent.db")
    context = _tool_context()
    controller = MemoryWriteController(
        store,
        scope_key=str(context.memory_scope),
        policy=EffectiveMemoryPolicy.from_dict(context.memory_policy),
        context=context,
    )
    receipt = controller.append(
        "HISTORY.md",
        "[2026-08-08] one durable episode",
        source_kind="test.append",
        source_fingerprint="episode-1",
    )
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def accept(actor_id: str) -> None:
        barrier.wait()
        _candidate, outcome = store.resolve_memory_candidate(
            candidate_id=str(receipt.candidate_id),
            user_id=context.user_id,
            resolution="accept",
            actor_id=actor_id,
        )
        outcomes.append(outcome)

    workers = [
        threading.Thread(target=accept, args=("worker:a",)),
        threading.Thread(target=accept, args=("worker:b",)),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert sorted(outcomes) == ["idempotent", "merged"]
    history = MemoryStore(store, context.memory_scope).read_relative("HISTORY.md")
    assert history.count("one durable episode") == 1


def test_expired_candidate_cannot_merge(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "candidate-expired.db")
    context = _tool_context()
    receipt = MemoryWriteController(
        store,
        scope_key=str(context.memory_scope),
        policy=EffectiveMemoryPolicy.from_dict(context.memory_policy),
        context=context,
    ).replace("PROFILE.md", "expired profile", source_kind="test.expiry")
    with store._pool.connection() as connection, connection.transaction():
        connection.execute(
            "UPDATE memory_candidates SET expires_at=clock_timestamp()-interval '1 second' "
            "WHERE candidate_id=%s",
            (receipt.candidate_id,),
        )

    candidate, outcome = store.resolve_memory_candidate(
        candidate_id=str(receipt.candidate_id),
        user_id=context.user_id,
        resolution="accept",
        actor_id="test:owner",
    )

    assert outcome == "expired"
    assert candidate is not None and candidate.status == "expired"
    assert MemoryStore(store, context.memory_scope).read_profile() == ""


class _ConsolidationProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(api_key="test")

    def get_default_model(self) -> str:
        return "test/memory-candidate"

    async def chat(self, **_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {
                    "history_entry": "[2026-08-08] User selected concise reports.",
                    "memory_update": "User prefers concise reports.",
                    "profile_update": "Communication: concise.",
                    "l0_update": "Active preference: concise reports.",
                }
            )
        )


@pytest.mark.asyncio
async def test_consolidation_stages_each_durable_layer_without_direct_write(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "candidate-consolidation.db")
    revision = AgentRevision(
        revision_id="default:candidate-v1",
        agent_id="default",
        version=1,
        model_policy={"primary": "test/memory-candidate"},
        memory_policy=_candidate_policy(),
        status="published",
    )
    executor = NativeAgentExecutor(
        provider=_ConsolidationProvider(),
        scratch_root=tmp_path,
        agent_revision=revision,
        session_manager=RuntimeSessionManager(store),
    )
    session = Session(key="api:memory-owner:default:session")
    session.add_message("user", "Please keep reports concise.")
    session.add_message("assistant", "Understood.")
    context = RunContext(
        run_id="run-consolidation-candidate",
        user_id="memory-owner",
        agent_id="default",
        session_id="session",
        session_key=session.key,
        channel="api",
        chat_id="runtime",
        memory_scope="user:memory-owner:agent:default",
        memory_policy=_candidate_policy(),
        context_timestamp="2026-08-08T11:00:00+00:00",
    )

    await executor._consolidate_memory(session, archive_all=True, run_context=context)

    candidates = store.list_memory_candidates(user_id="memory-owner")
    assert {item.document_path for item in candidates} == {
        ".abstract",
        "HISTORY.md",
        "MEMORY.md",
        "PROFILE.md",
        "2026-08-08.md",
    }
    memory = MemoryStore(store, context.memory_scope)
    assert memory.list_relative() == []
    await executor.close_mcp()


def test_memory_candidate_api_is_owner_scoped_and_resolution_is_idempotent(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "candidate-api.db")
    store.create_api_access_token(user_id="memory-owner", actor_id="test", token="owner-token")
    store.create_api_access_token(user_id="other-owner", actor_id="test", token="other-token")
    context = _tool_context()
    receipt = MemoryWriteController(
        store,
        scope_key=str(context.memory_scope),
        policy=EffectiveMemoryPolicy.from_dict(context.memory_policy),
        context=context,
    ).replace("PROFILE.md", "Owner profile", source_kind="test.api")
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    owner = {"Authorization": "Bearer owner-token"}
    other = {"Authorization": "Bearer other-token"}

    with client:
        listed = client.get("/v1/memory/candidates", headers=owner)
        foreign_list = client.get("/v1/memory/candidates", headers=other)
        foreign_resolve = client.post(
            f"/v1/memory/candidates/{receipt.candidate_id}/resolve",
            headers=other,
            json={"resolution": "accept"},
        )
        accepted = client.post(
            f"/v1/memory/candidates/{receipt.candidate_id}/resolve",
            headers=owner,
            json={"resolution": "accept", "note": "confirmed"},
        )
        accepted_again = client.post(
            f"/v1/memory/candidates/{receipt.candidate_id}/resolve",
            headers=owner,
            json={"resolution": "accept"},
        )

    assert listed.status_code == 200
    assert listed.json()["items"][0]["content"] == "Owner profile"
    assert foreign_list.status_code == 200 and foreign_list.json()["items"] == []
    assert foreign_resolve.status_code == 404
    assert accepted.status_code == 200 and accepted.json()["status"] == "merged"
    assert accepted_again.status_code == 200
    assert MemoryStore(store, context.memory_scope).read_profile() == "Owner profile"


def test_memory_document_api_lists_layers_and_keeps_full_content_owner_scoped(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "memory-document-api.db")
    store.create_api_access_token(user_id="memory-owner", actor_id="test", token="owner-token")
    store.create_api_access_token(user_id="other-owner", actor_id="test", token="other-token")
    owner_scope = "user:memory-owner:agent:default"
    owner_memory = MemoryStore(store, owner_scope)
    owner_memory.write_profile("Owner prefers concise answers.")
    owner_memory.write_long_term("Project Atlas is active.")
    owner_memory.append_history("Completed the onboarding run.")
    owner_memory.write_relative("agent/lessons.md", "Always verify external writes.")
    MemoryStore(store, "user:other-owner:agent:default").write_profile("Foreign profile")

    context = _tool_context()
    MemoryWriteController(
        store,
        scope_key=owner_scope,
        policy=EffectiveMemoryPolicy.from_dict(context.memory_policy),
        context=context,
    ).replace("PROFILE.md", "Candidate profile", source_kind="test.viewer")

    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    owner = {"Authorization": "Bearer owner-token"}
    other = {"Authorization": "Bearer other-token"}
    query = {"agent_id": "default"}
    with client:
        listed = client.get("/v1/memory/documents", headers=owner, params=query)
        episodic = client.get(
            "/v1/memory/documents",
            headers=owner,
            params={**query, "layer": "episodic"},
        )
        searched = client.get(
            "/v1/memory/documents",
            headers=owner,
            params={**query, "search": "Atlas"},
        )
        detail = client.get(
            "/v1/memory/documents/PROFILE.md",
            headers=owner,
            params={**query, "scope_key": owner_scope},
        )
        foreign_detail = client.get(
            "/v1/memory/documents/PROFILE.md",
            headers=other,
            params={**query, "scope_key": owner_scope},
        )
        candidates = client.get(
            "/v1/memory/candidates",
            headers=owner,
            params={"agent_id": "default", "status": "all"},
        )
        other_agent_candidates = client.get(
            "/v1/memory/candidates",
            headers=owner,
            params={"agent_id": "other-agent", "status": "all"},
        )

    assert listed.status_code == 200
    assert listed.json()["summary"] == {
        "total": 4,
        "by_layer": {"profile": 1, "long_term": 1, "episodic": 1, "agent": 1},
    }
    assert {item["layer"] for item in listed.json()["items"]} == {
        "profile",
        "long_term",
        "episodic",
        "agent",
    }
    assert [item["document_path"] for item in episodic.json()["items"]] == ["HISTORY.md"]
    assert [item["document_path"] for item in searched.json()["items"]] == ["MEMORY.md"]
    assert detail.status_code == 200
    assert detail.json()["content"] == "Owner prefers concise answers."
    assert foreign_detail.status_code == 404
    assert len(candidates.json()["items"]) == 1
    assert other_agent_candidates.json()["items"] == []
