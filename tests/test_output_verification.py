from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from joyhousebot.agent.executor import NativeAgentExecutor
from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from joyhousebot.providers.base import LLMProvider, LLMResponse
from joyhousebot.runtime.context import RunContext
from joyhousebot.runtime.models import AgentOptions
from joyhousebot.runtime.runner import NativeAgentRuntime
from joyhousebot.runtime.verification import verify_output
from joyhousebot.session.runtime_manager import RuntimeSessionManager
from tests.support.postgres_store import PostgresTestStore

_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


class _SequenceProvider(LLMProvider):
    def __init__(self, responses: list[str]) -> None:
        super().__init__(api_key="test")
        self.responses = responses
        self.calls = 0
        self.messages: list[list[dict[str, Any]]] = []

    def get_default_model(self) -> str:
        return "test/verification"

    async def chat(self, **kwargs: Any) -> LLMResponse:
        self.messages.append(list(kwargs["messages"]))
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return LLMResponse(content=self.responses[index], finish_reason="stop")


def _executor(store: PostgresTestStore, tmp_path: Path, provider: LLMProvider):
    return NativeAgentExecutor(
        provider=provider,
        scratch_root=tmp_path,
        model="test/verification",
        max_iterations=4,
        session_manager=RuntimeSessionManager(store),
    )


@pytest.mark.asyncio
async def test_schema_failure_is_repaired_in_a_new_durable_turn(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "verification-repair.db")
    provider = _SequenceProvider(["not json", '{"answer": 42}'])
    executor = _executor(store, tmp_path, provider)
    runtime = NativeAgentRuntime(agent=executor, store=store)

    submitted = await runtime.submit_run(
        AgentOptions(
            prompt="return the answer",
            user_id="user-verify",
            session_id="repair",
            output_schema=_SCHEMA,
            max_repairs=1,
            max_turns=2,
        )
    )
    finished = await runtime.wait(submitted.run_id, timeout=3)

    assert finished.status == "completed"
    assert finished.result["structured_output"] == {"answer": 42}
    assert provider.calls == 2
    assert "failed required verification" in provider.messages[1][-1]["content"]
    records = store.list_verification_records(submitted.run_id)
    assert [(item.attempt, item.status) for item in records] == [(1, "failed"), (2, "passed")]
    assert [item.stop_reason for item in store.list_runtime_turns(submitted.run_id)] == [
        "verification_failed",
        "final_response",
    ]
    await runtime.close()
    await executor.close_mcp()


@pytest.mark.asyncio
async def test_repair_limit_fails_without_false_completion(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "verification-exhausted.db")
    provider = _SequenceProvider(["invalid", "still invalid"])
    executor = _executor(store, tmp_path, provider)
    runtime = NativeAgentRuntime(agent=executor, store=store)

    submitted = await runtime.submit_run(
        AgentOptions(
            prompt="return the answer",
            user_id="user-verify",
            session_id="exhausted",
            output_schema=_SCHEMA,
            max_repairs=1,
            max_turns=3,
        )
    )
    finished = await runtime.wait(submitted.run_id, timeout=3)

    assert finished.status == "failed"
    assert finished.result["stop_reason"] == "structured_output_error"
    assert provider.calls == 2
    assert [item.status for item in store.list_verification_records(submitted.run_id)] == [
        "failed",
        "failed",
    ]
    assert store.list_runtime_artifacts(submitted.run_id) == []
    await runtime.close()
    await executor.close_mcp()


@pytest.mark.asyncio
async def test_artifact_and_deterministic_verifiers_record_safe_evidence(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "verification-artifact.db")
    store.create_runtime_run(
        run_id="run-artifact-verify",
        user_id="user-verify",
        session_id="artifact",
        agent_id="default",
        kind="agent",
        prompt="build report",
        options={},
    )
    run = store.claim_runtime_run("run-artifact-verify", worker_id="worker-one")
    assert run is not None
    store.add_runtime_artifact(
        artifact_id="artifact-report",
        run_id=run.run_id,
        name="report",
        media_type="application/json",
        content={"ok": True},
    )
    context = RunContext(
        run_id=run.run_id,
        user_id=run.user_id,
        agent_id=run.agent_id,
        session_id=run.session_id,
        session_key="api:user-verify:default:artifact",
        channel="api",
        chat_id="artifact",
        trace_store=store,
        worker_id="worker-one",
        run_lease_version=run.lease_version,
        verification_policy={
            "verifiers": [
                {
                    "id": "report",
                    "type": "artifact",
                    "names": ["report"],
                    "hashes": [sha256(b'{"ok":true}').hexdigest()],
                },
                {"id": "not-empty", "type": "deterministic", "rule": "non_empty"},
            ]
        },
    )

    decision = await verify_output(
        context, "report ready", turn_id="turn-report", attempt=1
    )

    assert decision.passed
    records = store.list_verification_records(run.run_id)
    assert [item.status for item in records] == ["passed", "passed"]
    artifact_record = next(item for item in records if item.verifier_type == "artifact")
    assert artifact_record.evidence["artifacts"][0]["content_hash"]
    assert "content" not in artifact_record.evidence["artifacts"][0]


def test_run_lease_fences_stale_verification_worker(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "verification-fence.db")
    store.create_runtime_run(
        run_id="run-verification-fence",
        user_id="user-verify",
        session_id="fence",
        agent_id="default",
        kind="agent",
        prompt="verify",
        options={},
    )
    first = store.claim_runtime_run("run-verification-fence", worker_id="worker-one")
    assert first is not None
    with store._pool.connection() as conn, conn.transaction():
        conn.execute(
            "UPDATE runtime_runs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE run_id=%s",
            (first.run_id,),
        )
    second = store.claim_runtime_run(first.run_id, worker_id="worker-two")
    assert second is not None
    values = {
        "verification_id": "ver-fenced",
        "run_id": first.run_id,
        "task_id": None,
        "turn_id": "turn-fenced",
        "user_id": first.user_id,
        "attempt": 1,
        "verifier_id": "not-empty",
        "verifier_type": "deterministic",
        "policy": {"type": "deterministic", "rule": "non_empty"},
        "input_hash": "sha256:input",
    }

    assert store.begin_verification(
        **values, worker_id="worker-one", run_lease_version=first.lease_version
    ) is None
    claimed = store.begin_verification(
        **values, worker_id="worker-two", run_lease_version=second.lease_version
    )
    assert claimed is not None
    assert store.complete_verification(
        claimed.verification_id,
        status="passed",
        evidence={},
        worker_id="worker-one",
        run_lease_version=first.lease_version,
    ) is None
    assert store.complete_verification(
        claimed.verification_id,
        status="passed",
        evidence={"ok": True},
        worker_id="worker-two",
        run_lease_version=second.lease_version,
    ).status == "passed"


@pytest.mark.asyncio
async def test_verified_turn_replay_reuses_response_and_record(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "verification-replay.db")
    store.create_runtime_run(
        run_id="run-verification-replay",
        user_id="user-verify",
        session_id="replay",
        agent_id="default",
        kind="agent",
        prompt="answer",
        options={},
    )
    run = store.claim_runtime_run("run-verification-replay", worker_id="worker-replay")
    assert run is not None
    provider = _SequenceProvider(['{"answer": 42}'])
    executor = _executor(store, tmp_path, provider)
    context = RunContext(
        run_id=run.run_id,
        user_id=run.user_id,
        agent_id=run.agent_id,
        session_id=run.session_id,
        session_key="api:user-verify:default:replay",
        channel="api",
        chat_id="replay",
        trace_store=store,
        worker_id="worker-replay",
        run_lease_version=run.lease_version,
        output_schema=_SCHEMA,
        max_turns=1,
    )

    first = await executor.process_direct("answer", session_key=context.session_key, run_context=context)
    replayed = await executor.process_direct(
        "answer", session_key=context.session_key, run_context=context
    )

    assert first == replayed == '{"answer": 42}'
    assert provider.calls == 1
    assert len(store.list_verification_records(run.run_id)) == 1
    await executor.close_mcp()


def test_verification_api_is_owner_scoped_and_omits_policy(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "verification-api.db")
    store.create_api_access_token(user_id="user-a", actor_id="test", token="token-a")
    store.create_api_access_token(user_id="user-b", actor_id="test", token="token-b")
    store.create_runtime_run(
        run_id="run-verification-api",
        user_id="user-a",
        session_id="api",
        agent_id="default",
        kind="agent",
        prompt="verify",
        options={},
    )
    run = store.claim_runtime_run("run-verification-api", worker_id="worker-api")
    assert run is not None
    record = store.begin_verification(
        verification_id="ver-api",
        run_id=run.run_id,
        task_id=None,
        turn_id="turn-api",
        user_id=run.user_id,
        attempt=1,
        verifier_id="secret-policy",
        verifier_type="deterministic",
        policy={"type": "deterministic", "rule": "contains", "value": "private"},
        input_hash="sha256:api",
        worker_id="worker-api",
        run_lease_version=run.lease_version,
    )
    assert record is not None
    store.complete_verification(
        record.verification_id,
        status="passed",
        evidence={"input_hash": "sha256:api"},
        worker_id="worker-api",
        run_lease_version=run.lease_version,
    )
    container = build_api_container(config=Config(), store=store)
    client = TestClient(create_app(container))

    with client:
        own = client.get(
            f"/v1/runs/{run.run_id}/verifications",
            headers={"Authorization": "Bearer token-a"},
        )
        other = client.get(
            f"/v1/runs/{run.run_id}/verifications",
            headers={"Authorization": "Bearer token-b"},
        )

    assert own.status_code == 200
    assert own.json()["items"][0]["status"] == "passed"
    assert "policy" not in own.json()["items"][0]
    assert "worker_id" not in own.json()["items"][0]
    assert other.status_code == 404


def test_run_api_persists_verification_contract_for_worker(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "verification-submit.db")
    store.create_api_access_token(user_id="user-a", actor_id="test", token="token-a")
    container = build_api_container(config=Config(), store=store)
    client = TestClient(create_app(container))
    policy = {
        "verifiers": [
            {"id": "answer-present", "type": "deterministic", "rule": "non_empty"}
        ]
    }

    with client:
        response = client.post(
            "/v1/runs",
            headers={"Authorization": "Bearer token-a"},
            json={
                "agent_id": "default",
                "input": {"type": "message", "content": "answer"},
                "output_schema": _SCHEMA,
                "verification_policy": policy,
                "max_repairs": 2,
                "max_replans": 3,
            },
        )
        run = store.get_runtime_run(response.json()["run_id"])

    assert response.status_code == 202
    assert run.options["output_schema"] == _SCHEMA
    assert run.options["verification_policy"] == policy
    assert run.options["max_repairs"] == 2
    assert run.options["max_replans"] == 3
