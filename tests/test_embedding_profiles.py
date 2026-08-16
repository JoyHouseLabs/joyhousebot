"""Embedding profile governance and revision completeness contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from porthouse.api.app import create_app
from porthouse.bootstrap.container import build_api_container
from porthouse.capabilities.services.context import ContextPort
from porthouse.config.schema import Config
from porthouse.contracts.capabilities import CapabilityContext
from porthouse.domain.embedding_profiles import normalize_embedding_profile
from porthouse.providers.base import EmbeddingResponse
from porthouse.services.retrieval.knowledge_repository import KnowledgeRepository
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
                "model_id": "openai/gpt-test",
                "name": "Test chat",
                "kind": "llm",
                "enabled": True,
                "input_modalities": ["text"],
            },
            {
                "model_id": "openai/text-embedding-test",
                "name": "Test embeddings",
                "kind": "embedding",
                "enabled": True,
                "input_modalities": ["text"],
                "dimensions": 3,
                "input_cost_per_million_tokens": 0,
            },
        ],
    }


def _profile_configuration() -> dict:
    return {
        "provider_id": "openai",
        "provider_revision_id": "openai:v1",
        "model_id": "openai/text-embedding-test",
        "dimensions": 3,
        "normalization": "l2",
        "batch_size": 16,
        "max_input_tokens": 8192,
        "max_cost_usd": 1,
        "is_default": True,
    }


def _mark_provider_published(store: PostgresTestStore, revision_id: str) -> None:
    with store._pool.connection() as connection, connection.transaction():
        connection.execute(
            """UPDATE model_provider_revisions SET status='published'
               WHERE provider_id='openai' AND revision_id=%s""",
            (revision_id,),
        )
        connection.execute(
            """UPDATE model_providers SET current_revision_id=%s
               WHERE provider_id='openai'""",
            (revision_id,),
        )


class _EmbeddingProvider:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.closed = False

    async def embed(
        self, texts: list[str], *, model: str, dimensions: int
    ) -> EmbeddingResponse:
        self.calls.append(list(texts))
        vectors = [
            [float(index + 1), float(len(text)), 1.0]
            for index, text in enumerate(texts)
        ]
        assert dimensions == 3
        return EmbeddingResponse(embeddings=vectors, model=model, usage={"input_tokens": 4})

    async def close(self) -> None:
        self.closed = True


class _FailingEmbeddingProvider:
    async def embed(
        self, texts: list[str], *, model: str, dimensions: int
    ) -> EmbeddingResponse:
        raise RuntimeError("synthetic embedding outage")

    async def close(self) -> None:
        return None


def test_embedding_profile_requires_exact_provider_revision() -> None:
    configuration = {key: value for key, value in _profile_configuration().items() if key != "is_default"}
    with pytest.raises(ValueError, match="exact provider revision"):
        normalize_embedding_profile(
            "knowledge-default",
            {**configuration, "provider_revision_id": "openai:latest"},
        )


def test_embedding_profile_draft_resolves_embedding_catalog(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "embedding-profile.db")
    store.save_model_provider_revision(
        "openai",
        name="OpenAI",
        description="test",
        configuration=_provider_configuration(),
        actor_id="admin",
    )
    profile = store.save_embedding_profile_revision(
        "knowledge-default",
        name="Knowledge default",
        description="test",
        configuration=_profile_configuration(),
        actor_id="admin",
    )
    assert profile["revision_id"] == "knowledge-default:v1"
    assert profile["configuration"]["dimensions"] == 3
    assert "is_default" not in profile["configuration"]
    assert profile["make_default"] is True
    readiness = store.knowledge_vector_readiness()
    assert readiness["ready"] is False
    assert "no published default embedding profile" in readiness["blockers"]


def test_vector_revision_cannot_be_ready_until_all_embeddings_exist(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "embedding-completeness.db")
    provider = store.save_model_provider_revision(
        "openai",
        name="OpenAI",
        description="test",
        configuration=_provider_configuration(),
        actor_id="admin",
    )
    _mark_provider_published(store, provider["revision_id"])
    store._verify_pgvector = lambda _connection: None
    profile = store.save_embedding_profile_revision(
        "knowledge-default",
        name="Knowledge default",
        description="test",
        configuration=_profile_configuration(),
        actor_id="admin",
    )
    store.publish_embedding_profile_revision(
        "knowledge-default", profile["revision_id"], actor_id="admin"
    )
    repository = KnowledgeRepository(store)
    revision_id = repository.stage_index_revision(
        doc_id="doc-vector",
        user_id="owner-a",
        agent_id="default",
        source_type="note",
        source_url=None,
        title="Vector notes",
        chunks=[{"text": "alpha"}, {"text": "beta"}],
        embedding_profile_id=profile["revision_id"],
    )
    with pytest.raises(ValueError, match="embeddings are incomplete"):
        repository.mark_index_revision_ready(
            user_id="owner-a",
            doc_id="doc-vector",
            revision_id=revision_id,
            actor_id="worker:test",
        )
    repository.stage_revision_embeddings(
        user_id="owner-a",
        doc_id="doc-vector",
        revision_id=revision_id,
        embedding_profile_id=profile["revision_id"],
        embeddings=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        actor_id="worker:test",
    )
    assert repository.mark_index_revision_ready(
        user_id="owner-a",
        doc_id="doc-vector",
        revision_id=revision_id,
        actor_id="worker:test",
    )


def test_embedding_profile_admin_api_exposes_governance_readiness(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "embedding-profile-api.db")
    store.create_api_access_token(
        user_id="operator", actor_id="test", token="embedding-profile-token"
    )
    store.upsert_platform_admin(user_id="operator", permissions=["*"], actor_id="test")
    store.save_model_provider_revision(
        "openai",
        name="OpenAI",
        description="test",
        configuration=_provider_configuration(),
        actor_id="admin",
    )
    headers = {"Authorization": "Bearer embedding-profile-token"}
    body = {
        "profile_id": "knowledge-default",
        "name": "Knowledge default",
        "description": "Default retrieval embeddings",
        **_profile_configuration(),
    }
    with TestClient(create_app(build_api_container(config=Config(), store=store))) as client:
        created = client.post(
            "/v1/admin/embedding-profiles", headers=headers, json=body
        )
        assert created.status_code == 201
        assert created.json()["revision_id"] == "knowledge-default:v1"

        profile = client.get(
            "/v1/admin/embedding-profiles/knowledge-default", headers=headers
        )
        assert profile.status_code == 200
        assert profile.json()["revisions"][0]["status"] == "draft"

        readiness = client.get(
            "/v1/admin/embedding-profiles/readiness", headers=headers
        )
        assert readiness.status_code == 200
        assert readiness.json()["ready"] is False
        assert "no published default embedding profile" in readiness.json()["blockers"]

        publish = client.post(
            "/v1/admin/embedding-profiles/knowledge-default/revisions/"
            "knowledge-default:v1/publish",
            headers=headers,
        )
        assert publish.status_code == 409
        assert "provider revision is not published" in publish.json()["detail"]


def test_profile_default_switch_preserves_immutable_retired_revision(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "embedding-profile-switch.db")
    provider = store.save_model_provider_revision(
        "openai",
        name="OpenAI",
        description="test",
        configuration=_provider_configuration(),
        actor_id="admin",
    )
    _mark_provider_published(store, provider["revision_id"])
    store._verify_pgvector = lambda _connection: None

    first = store.save_embedding_profile_revision(
        "knowledge-default",
        name="Knowledge default",
        description="first",
        configuration=_profile_configuration(),
        actor_id="admin",
    )
    first_fingerprint = first["fingerprint"]
    store.publish_embedding_profile_revision(
        "knowledge-default", first["revision_id"], actor_id="admin"
    )
    second = store.save_embedding_profile_revision(
        "knowledge-default",
        name="Knowledge default",
        description="second",
        configuration={**_profile_configuration(), "batch_size": 8},
        actor_id="admin",
    )
    store.publish_embedding_profile_revision(
        "knowledge-default", second["revision_id"], actor_id="admin"
    )

    default = store.get_published_embedding_profile()
    assert store.get_published_embedding_profile(first["revision_id"]) is None
    frozen = store.get_published_embedding_profile(
        first["revision_id"], allow_retired=True
    )
    assert default is not None and default["revision_id"] == second["revision_id"]
    assert frozen is not None and frozen["status"] == "retired"
    assert frozen["fingerprint"] == first_fingerprint
    assert "is_default" not in frozen["configuration"]


@pytest.mark.asyncio
async def test_context_port_embeds_all_chunks_before_atomic_activation(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "embedding-context.db")
    provider_revision = store.save_model_provider_revision(
        "openai",
        name="OpenAI",
        description="test",
        configuration=_provider_configuration(),
        actor_id="admin",
    )
    _mark_provider_published(store, provider_revision["revision_id"])
    store._verify_pgvector = lambda _connection: None
    profile = store.save_embedding_profile_revision(
        "knowledge-default",
        name="Knowledge default",
        description="test",
        configuration={**_profile_configuration(), "batch_size": 1},
        actor_id="admin",
    )
    store.publish_embedding_profile_revision(
        "knowledge-default", profile["revision_id"], actor_id="admin"
    )
    embedding_provider = _EmbeddingProvider()
    port = ContextPort(store, embedding_provider_resolver=lambda _profile: embedding_provider)
    doc_id = await port.index_knowledge(
        CapabilityContext(
            user_id="owner-a",
            session_id="session-vector",
            run_id="run-vector",
            agent_id="default",
        ),
        source_type="note",
        source_url="",
        title="Embedded notes",
        chunks=[{"text": "alpha"}, {"text": "beta"}],
        source_system="test",
        source_id="embedded-notes",
        embedding_profile_id=profile["revision_id"],
    )

    repository = KnowledgeRepository(store)
    document = repository.get_document(user_id="owner-a", doc_id=doc_id)
    revisions = repository.list_index_revisions(user_id="owner-a", doc_id=doc_id)
    with store._pool.connection() as connection:
        embedding_count = connection.execute(
            """SELECT count(*) AS value FROM knowledge_revision_embeddings
               WHERE user_id='owner-a' AND doc_id=%s""",
            (doc_id,),
        ).fetchone()["value"]
    assert document["index_status"] == "ready"
    assert revisions[0]["status"] == "active"
    assert revisions[0]["embedding_profile_id"] == profile["revision_id"]
    assert embedding_count == 2
    assert embedding_provider.calls == [["alpha"], ["beta"]]
    assert embedding_provider.closed is True


@pytest.mark.asyncio
async def test_embedding_failure_preserves_previous_active_projection(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "embedding-failure.db")
    provider_revision = store.save_model_provider_revision(
        "openai",
        name="OpenAI",
        description="test",
        configuration=_provider_configuration(),
        actor_id="admin",
    )
    _mark_provider_published(store, provider_revision["revision_id"])
    store._verify_pgvector = lambda _connection: None
    profile = store.save_embedding_profile_revision(
        "knowledge-default",
        name="Knowledge default",
        description="test",
        configuration=_profile_configuration(),
        actor_id="admin",
    )
    store.publish_embedding_profile_revision(
        "knowledge-default", profile["revision_id"], actor_id="admin"
    )
    port = ContextPort(
        store, embedding_provider_resolver=lambda _profile: _FailingEmbeddingProvider()
    )
    context = CapabilityContext(
        user_id="owner-a",
        session_id="session-vector",
        run_id="run-vector-failure",
        agent_id="default",
    )
    doc_id = await port.index_knowledge(
        context,
        source_type="note",
        source_url="",
        title="Stable notes",
        chunks=[{"text": "stable searchable knowledge"}],
        source_system="test",
        source_id="stable-notes",
        source_generation=1,
    )
    repository = KnowledgeRepository(store)
    active_before = repository.get_document(user_id="owner-a", doc_id=doc_id)[
        "active_revision_id"
    ]

    with pytest.raises(RuntimeError, match="synthetic embedding outage"):
        await port.index_knowledge(
            context,
            source_type="note",
            source_url="",
            title="Broken replacement",
            chunks=[{"text": "replacement must not activate"}],
            source_system="test",
            source_id="stable-notes",
            source_version="2",
            source_generation=2,
            embedding_profile_id=profile["revision_id"],
        )

    document = repository.get_document(user_id="owner-a", doc_id=doc_id)
    revisions = repository.list_index_revisions(user_id="owner-a", doc_id=doc_id)
    assert document["active_revision_id"] == active_before
    assert document["title"] == "Stable notes"
    assert repository.search(user_id="owner-a", query="stable", top_k=5)
    assert repository.search(user_id="owner-a", query="replacement", top_k=5) == []
    assert revisions[0]["status"] == "failed"
