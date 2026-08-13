"""K4 scale, governance, and recoverable maintenance contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from joyhousebot.application.context import Principal, RequestContext
from joyhousebot.application.knowledge_maintenance import KnowledgeMaintenanceService
from joyhousebot.capabilities.services.context import ContextPort
from joyhousebot.contracts.capabilities import CapabilityContext
from joyhousebot.providers.base import EmbeddingResponse
from joyhousebot.services.retrieval.embedding_execution import (
    EmbeddingAdmissionError,
    execute_embedding_profile,
)
from joyhousebot.services.retrieval.knowledge_repository import KnowledgeRepository
from tests.support.postgres_store import PostgresTestStore


def _provider_configuration() -> dict:
    return {
        "enabled": True,
        "extension_id": "provider-openai-compatible",
        "api_base": "https://models.example.test/v1",
        "api_key_ref": "env://EMBEDDING_TEST_KEY",
        "credential_mode": "api_key",
        "models": [
            {
                "model_id": "openai/text-embedding-test",
                "name": "Test embeddings",
                "kind": "embedding",
                "enabled": True,
                "input_modalities": ["text"],
                "dimensions": 3,
                "input_cost_per_million_tokens": 2.0,
            }
        ],
    }


def _profile_configuration(**overrides) -> dict:  # noqa: ANN003
    return {
        "provider_id": "openai",
        "provider_revision_id": "openai:v1",
        "model_id": "openai/text-embedding-test",
        "dimensions": 3,
        "normalization": "none",
        "batch_size": 2,
        "max_input_tokens": 1000,
        "max_cost_usd": 1,
        "requests_per_minute": 60,
        "tokens_per_minute": 1_000_000,
        "ann_min_rows": 100,
        "hnsw_m": 16,
        "hnsw_ef_construction": 64,
        "hnsw_ef_search": 40,
        "is_default": True,
        **overrides,
    }


def _published_profile(store: PostgresTestStore, **overrides) -> dict:  # noqa: ANN003
    provider = store.save_model_provider_revision(
        "openai",
        name="OpenAI",
        description="test",
        configuration=_provider_configuration(),
        actor_id="admin",
    )
    with store._pool.connection() as connection, connection.transaction():
        connection.execute(
            "UPDATE model_provider_revisions SET status='published' WHERE revision_id=%s",
            (provider["revision_id"],),
        )
        connection.execute(
            "UPDATE model_providers SET current_revision_id=%s WHERE provider_id='openai'",
            (provider["revision_id"],),
        )
    store._verify_pgvector = lambda _connection: None
    profile = store.save_embedding_profile_revision(
        "knowledge-default",
        name="Knowledge default",
        description="test",
        configuration=_profile_configuration(**overrides),
        actor_id="admin",
    )
    return store.publish_embedding_profile_revision(
        "knowledge-default", profile["revision_id"], actor_id="admin"
    )


def _draft_profile(store: PostgresTestStore, **overrides) -> dict:  # noqa: ANN003
    _published_profile(store, **overrides)
    return store.save_embedding_profile_revision(
        "knowledge-default",
        name="Knowledge candidate",
        description="candidate",
        configuration=_profile_configuration(**overrides),
        actor_id="admin",
    )


class _Provider:
    async def embed(
        self, texts: list[str], *, model: str, dimensions: int
    ) -> EmbeddingResponse:
        assert dimensions == 3
        return EmbeddingResponse(
            embeddings=[[float(index + 1), 1.0, 0.0] for index, _ in enumerate(texts)],
            model=model,
            usage={"input_tokens": len(texts) * 2},
        )

    async def close(self) -> None:
        return None


class _InvalidProvider(_Provider):
    async def embed(
        self, texts: list[str], *, model: str, dimensions: int
    ) -> EmbeddingResponse:
        return EmbeddingResponse(
            embeddings=[[1.0, float("nan")]],
            model=model,
            usage={"input_tokens": 2},
        )


@pytest.mark.asyncio
async def test_embedding_budget_fails_closed_and_records_usage(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "k4-budget.db")
    profile = _published_profile(store, max_cost_usd=0.000001)
    repository = KnowledgeRepository(store)

    with pytest.raises(EmbeddingAdmissionError, match="max_cost_usd"):
        await execute_embedding_profile(
            store=store,
            repository=repository,
            provider_resolver=lambda _configuration: _Provider(),
            profile=profile,
            texts=["cost boundary"],
            user_id="owner-a",
            doc_id=None,
            revision_id=None,
            operation_type="query",
        )
    with store._pool.connection() as connection:
        operation = connection.execute(
            "SELECT * FROM knowledge_embedding_operations"
        ).fetchone()
    assert operation["status"] == "failed"
    assert operation["error_code"] == "EmbeddingAdmissionError"


@pytest.mark.asyncio
async def test_provider_bootstrap_failure_is_audited(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "k4-provider-failure.db")
    profile = _published_profile(store)
    repository = KnowledgeRepository(store)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await execute_embedding_profile(
            store=store,
            repository=repository,
            provider_resolver=lambda _configuration: (_ for _ in ()).throw(
                RuntimeError("provider unavailable")
            ),
            profile=profile,
            texts=["audit bootstrap failures"],
            user_id="owner-a",
            doc_id=None,
            revision_id=None,
            operation_type="query",
            run_id="run-provider-failure",
            task_id="task-provider-failure",
            eval_run_id="eval-provider-failure",
            eval_case_id="case-provider-failure",
        )
    with store._pool.connection() as connection:
        operation = connection.execute(
            "SELECT * FROM knowledge_embedding_operations"
        ).fetchone()
    assert operation["status"] == "failed"
    assert operation["error_code"] == "RuntimeError"
    assert operation["run_id"] == "run-provider-failure"
    assert operation["task_id"] == "task-provider-failure"
    assert operation["eval_run_id"] == "eval-provider-failure"
    assert operation["eval_case_id"] == "case-provider-failure"


@pytest.mark.asyncio
async def test_invalid_provider_vectors_fail_closed_and_are_audited(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "k4-invalid-provider-result.db")
    profile = _published_profile(store)
    repository = KnowledgeRepository(store)

    with pytest.raises(RuntimeError, match="invalid dimensions"):
        await execute_embedding_profile(
            store=store,
            repository=repository,
            provider_resolver=lambda _configuration: _InvalidProvider(),
            profile=profile,
            texts=["invalid vector"],
            user_id="owner-a",
            doc_id=None,
            revision_id=None,
            operation_type="query",
        )
    with store._pool.connection() as connection:
        operation = connection.execute(
            "SELECT * FROM knowledge_embedding_operations"
        ).fetchone()
    assert operation["status"] == "failed"
    assert operation["error_code"] == "RuntimeError"


@pytest.mark.asyncio
async def test_draft_profile_requires_exact_running_eval_namespace(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "k4-draft-eval.db")
    draft = _draft_profile(store)
    port = ContextPort(store)
    forged = CapabilityContext(
        user_id="owner-a",
        session_id="main",
        run_id="run-a",
        metadata={
            "evaluation": True,
            "eval_run_id": "forged",
            "eval_case_id": "candidate",
            "embedding_profile_id": draft["revision_id"],
        },
    )
    assert await port._resolve_embedding_profile(forged, draft["revision_id"]) == (
        None,
        False,
    )
    store.save_eval_suite(
        suite={
            "suite_id": "knowledge.retrieval",
            "version": 1,
            "name": "Retrieval",
            "status": "active",
            "target_types": ["embedding_profile"],
            "created_by": "admin",
        },
        cases=[
            {
                "case_id": "candidate",
                "name": "candidate",
                "input": {},
                "scorers": [{"type": "contains", "path": "result", "value": "ok"}],
            }
        ],
    )
    eval_run, _ = store.create_eval_run(
        value={
            "eval_run_id": "evalrun_k4_draft",
            "suite_id": "knowledge.retrieval",
            "suite_version": 1,
            "target_type": "embedding_profile",
            "target_id": draft["profile_id"],
            "target_revision_id": draft["revision_id"],
            "request_hash": "k4-draft-eval",
            "created_by": "admin",
        }
    )
    legitimate = CapabilityContext(
        user_id=f"eval:{eval_run['eval_run_id']}",
        session_id="main",
        run_id="run-eval",
        metadata={
            "eval_run_id": eval_run["eval_run_id"],
            "eval_case_id": "candidate",
        },
    )
    resolved, allowed = await port._resolve_embedding_profile(
        legitimate, draft["revision_id"]
    )
    assert allowed is True
    assert resolved and resolved["revision_id"] == draft["revision_id"]
    assert await port._embedding_eval_scope(legitimate, draft["revision_id"]) == (
        eval_run["eval_run_id"],
        "candidate",
    )


def test_profile_publication_obeys_exact_retrieval_eval_gate(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "k4-profile-gate.db")
    draft = _draft_profile(store)
    store.save_eval_suite(
        suite={
            "suite_id": "knowledge.release",
            "version": 1,
            "name": "Knowledge release",
            "status": "active",
            "target_types": ["embedding_profile"],
            "created_by": "admin",
        },
        cases=[
            {
                "case_id": "recall",
                "name": "Recall",
                "input": {},
                "scorers": [{"type": "contains", "path": "result", "value": "ok"}],
            }
        ],
    )
    store.save_release_gate_policy(
        value={
            "target_type": "embedding_profile",
            "target_id": draft["profile_id"],
            "target_revision_id": draft["revision_id"],
            "required": True,
            "requirements": [
                {
                    "suite_id": "knowledge.release",
                    "suite_version": 1,
                    "min_pass_rate": 1.0,
                    "max_age_hours": 24,
                    "require_automated": True,
                }
            ],
            "created_by": "admin",
        }
    )
    with pytest.raises(ValueError, match="release gate failed"):
        store.publish_embedding_profile_revision(
            draft["profile_id"], draft["revision_id"], actor_id="admin"
        )
    eval_run, _ = store.create_eval_run(
        value={
            "eval_run_id": "evalrun_k4_gate",
            "suite_id": "knowledge.release",
            "suite_version": 1,
            "target_type": "embedding_profile",
            "target_id": draft["profile_id"],
            "target_revision_id": draft["revision_id"],
            "request_hash": "k4-profile-gate",
            "created_by": "admin",
        }
    )
    store.record_eval_case_result(
        eval_run["eval_run_id"],
        result={
            "case_id": "recall",
            "status": "passed",
            "score": 1.0,
            "output": {"result": "ok"},
            "scorer_results": [],
            "metrics": {"execution_mode": "automated"},
        },
    )
    assert store.finalize_eval_run(eval_run["eval_run_id"])["status"] == "passed"
    published = store.publish_embedding_profile_revision(
        draft["profile_id"], draft["revision_id"], actor_id="admin"
    )
    assert published["status"] == "published"


def test_embedding_rate_limit_reservation_is_atomic(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "k4-rate.db")
    assert store.check_embedding_rate_limit(
        "profile:v1",
        requests=1,
        input_tokens=10,
        requests_per_minute=1,
        tokens_per_minute=10,
    )
    assert not store.check_embedding_rate_limit(
        "profile:v1",
        requests=1,
        input_tokens=1,
        requests_per_minute=1,
        tokens_per_minute=10,
    )
    with store._pool.connection() as connection:
        rows = connection.execute(
            "SELECT rate_key,usage_count FROM embedding_rate_limits ORDER BY rate_key"
        ).fetchall()
    assert [int(row["usage_count"]) for row in rows] == [1, 10]


def test_small_profile_selects_exact_vector_strategy(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "k4-ann.db")
    profile = _published_profile(store, ann_min_rows=100)
    repository = KnowledgeRepository(store)
    repository.reconcile_vector_indexes(limit=10)
    state = repository.get_vector_index_state(profile["revision_id"])
    assert state is not None
    assert state["algorithm"] == "exact"
    assert state["status"] == "not_required"
    assert state["row_count"] == 0


def test_large_profile_builds_valid_partial_hnsw_index(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "k4-hnsw.db")
    profile = _published_profile(store, ann_min_rows=100)
    repository = KnowledgeRepository(store)
    repository.index_document(
        doc_id="ann-doc",
        user_id="owner-a",
        agent_id="default",
        source_type="note",
        source_url=None,
        title="ANN corpus",
        chunks=[{"text": f"evidence chunk {index}"} for index in range(100)],
    )
    document = repository.get_document(doc_id="ann-doc", user_id="owner-a")
    assert document is not None
    job = repository.enqueue_reembedding_job(
        user_id="owner-a",
        embedding_profile_id=profile["revision_id"],
        requested_by="test",
        idempotency_key="ann-build",
        doc_id="ann-doc",
    )
    item = repository.claim_reembedding_item(worker_id="worker-ann", lease_seconds=30)
    assert item is not None and item["job_id"] == job["job_id"]
    repository.store_reembedded_revision(
        job_id=item["job_id"],
        user_id="owner-a",
        doc_id="ann-doc",
        revision_id=document["active_revision_id"],
        embedding_profile_id=profile["revision_id"],
        embeddings=[[float(index + 1), 1.0, 0.5] for index in range(100)],
        actor_id="worker:test",
        worker_id="worker-ann",
        lease_version=item["lease_version"],
    )
    repository.reconcile_vector_indexes(limit=10)
    state = repository.get_vector_index_state(profile["revision_id"])
    assert state is not None
    assert state["algorithm"] == "hnsw"
    assert state["status"] == "ready"
    with store._pool.connection() as connection:
        validity = connection.execute(
            """SELECT index.indisvalid,index.indisready FROM pg_index index
               JOIN pg_class relation ON relation.oid=index.indexrelid
               WHERE relation.relname=%s""",
            (state["index_name"],),
        ).fetchone()
    assert validity and validity["indisvalid"] and validity["indisready"]
    results = repository.search_hybrid(
        user_id="owner-a",
        query="evidence chunk 5",
        query_embedding=[6.0, 1.0, 0.5],
        embedding_profile_id=profile["revision_id"],
        top_k=3,
    )
    assert results
    assert results[0]["trace"]["vector_strategy"] == "hnsw"


@pytest.mark.asyncio
async def test_reembedding_job_is_owner_scoped_fenced_and_resumable(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "k4-reembed.db")
    profile = _published_profile(store)
    repository = KnowledgeRepository(store)
    repository.index_document(
        doc_id="doc-a",
        user_id="owner-a",
        agent_id="default",
        source_type="note",
        source_url=None,
        title="A",
        chunks=[{"text": "alpha"}, {"text": "beta"}],
    )
    service = KnowledgeMaintenanceService(
        store, embedding_provider_resolver=lambda _configuration: _Provider()
    )
    context = RequestContext(
        principal=Principal(subject="token:owner-a", user_id="owner-a"),
        request_id="request-a",
        idempotency_key="reembed-a",
    )
    job = await service.enqueue_reembedding(
        context,
        embedding_profile_id=profile["revision_id"],
        knowledge_base_id=None,
        doc_id=None,
    )
    replay = await service.enqueue_reembedding(
        context,
        embedding_profile_id=profile["revision_id"],
        knowledge_base_id=None,
        doc_id=None,
    )
    assert replay["job_id"] == job["job_id"]
    item = repository.claim_reembedding_item(worker_id="worker-a", lease_seconds=30)
    assert item is not None and item["doc_id"] == "doc-a"
    await service.process_item(item, worker_id="worker-a")
    with pytest.raises(RuntimeError, match="fenced"):
        repository.store_reembedded_revision(
            job_id=item["job_id"],
            user_id=item["user_id"],
            doc_id=item["doc_id"],
            revision_id=item["revision_id"],
            embedding_profile_id=item["embedding_profile_id"],
            embeddings=[[1.0, 0.0, 0.0] for _ in item["chunks"]],
            actor_id="worker:stale-worker",
            worker_id="stale-worker",
            lease_version=item["lease_version"],
        )
    assert not repository.complete_reembedding_item(
        item["job_id"],
        item["doc_id"],
        item["revision_id"],
        worker_id="stale-worker",
        lease_version=item["lease_version"],
    )
    assert repository.complete_reembedding_item(
        item["job_id"],
        item["doc_id"],
        item["revision_id"],
        worker_id="worker-a",
        lease_version=item["lease_version"],
    )
    complete = await service.get_job(context, job["job_id"])
    assert complete["status"] == "completed"
    assert complete["completed_items"] == 1
    with store._pool.connection() as connection:
        count = connection.execute(
            """SELECT count(*) AS count FROM knowledge_revision_embeddings
               WHERE embedding_profile_id=%s""",
            (profile["revision_id"],),
        ).fetchone()["count"]
    assert count == 2
    other = RequestContext(
        principal=Principal(subject="token:owner-b", user_id="owner-b"),
        request_id="request-b",
    )
    with pytest.raises(Exception, match="not found"):
        await service.get_job(other, job["job_id"])
