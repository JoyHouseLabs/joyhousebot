"""Owner-scoped Knowledge asset control-plane contracts."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from porthouse.api.app import create_app
from porthouse.application.context import Principal, RequestContext
from porthouse.application.knowledge_assets import KnowledgeAssetService
from porthouse.bootstrap.container import build_api_container
from porthouse.capabilities.services.context import ContextPort
from porthouse.config.schema import Config
from porthouse.domain.capabilities.models import CapabilityKind, CapabilityRef
from porthouse.extension_sdk import CapabilityContext
from porthouse.services.retrieval.knowledge_repository import KnowledgeRepository
from tests.support.postgres_store import PostgresTestStore


def test_runtime_input_asset_upload_is_owner_scoped_and_integrity_checked(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(
        tmp_path / "input-assets.db",
        input_asset_directory=str(tmp_path / "runtime-input-assets"),
    )
    store.create_api_access_token(user_id="owner-a", actor_id="test", token="owner-a-token")
    store.create_api_access_token(user_id="owner-b", actor_id="test", token="owner-b-token")
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    body = b"private source body"
    digest = hashlib.sha256(body).hexdigest()
    headers = {
        "Authorization": "Bearer owner-a-token",
        "Idempotency-Key": "file-object-a-v1",
        "X-Content-SHA256": digest,
        "Content-Type": "text/plain",
    }

    with client:
        uploaded = client.post(
            "/v1/input-assets?file_name=private.txt", content=body, headers=headers
        )
        repeated = client.post(
            "/v1/input-assets?file_name=private.txt", content=body, headers=headers
        )
        owner_detail = client.get(
            f"/v1/input-assets/{uploaded.json()['asset_id']}",
            headers={"Authorization": "Bearer owner-a-token"},
        )
        foreign_detail = client.get(
            f"/v1/input-assets/{uploaded.json()['asset_id']}",
            headers={"Authorization": "Bearer owner-b-token"},
        )
        invalid = client.post(
            "/v1/input-assets?file_name=invalid.txt",
            content=body,
            headers={**headers, "Idempotency-Key": "invalid", "X-Content-SHA256": "0" * 64},
        )
        graph = client.post(
            "/v1/runs/graphs",
            headers={
                "Authorization": "Bearer owner-a-token",
                "Idempotency-Key": "asset-graph-a",
            },
            json={
                "goal": "consume immutable input",
                "session_id": "asset-graph-a",
                "input_asset_ids": [uploaded.json()["asset_id"]],
                "tasks": [{"id": "read", "prompt": "read the input"}],
            },
        )
        run = client.post(
            "/v1/runs",
            headers={
                "Authorization": "Bearer owner-a-token",
                "Idempotency-Key": "asset-run-a",
            },
            json={
                "execution": {"mode": "agent", "agent_id": "default"},
                "session_id": "asset-run-a",
                "input": {"type": "message", "content": "read the immutable input"},
                "input_asset_ids": [uploaded.json()["asset_id"]],
            },
        )
        foreign_graph = client.post(
            "/v1/runs/graphs",
            headers={
                "Authorization": "Bearer owner-b-token",
                "Idempotency-Key": "asset-graph-b",
            },
            json={
                "goal": "steal immutable input",
                "session_id": "asset-graph-b",
                "input_asset_ids": [uploaded.json()["asset_id"]],
                "tasks": [{"id": "read", "prompt": "read the input"}],
            },
        )

    assert uploaded.status_code == 201
    assert uploaded.json()["created"] is True
    assert "storage_uri" not in uploaded.json()
    assert repeated.status_code == 201
    assert repeated.json()["asset_id"] == uploaded.json()["asset_id"]
    assert repeated.json()["created"] is False
    assert owner_detail.status_code == 200
    assert foreign_detail.status_code == 404
    assert invalid.status_code == 422
    assert graph.status_code == 202, graph.text
    assert run.status_code == 202, run.text
    assert foreign_graph.status_code == 422
    assert [
        item.asset_id
        for item in store.list_run_input_assets(graph.json()["run_id"], expected_user_id="owner-a")
    ] == [uploaded.json()["asset_id"]]
    assert [
        item.asset_id
        for item in store.list_run_input_assets(run.json()["run_id"], expected_user_id="owner-a")
    ] == [uploaded.json()["asset_id"]]


@pytest.mark.asyncio
async def test_context_port_reads_only_assets_bound_to_current_run(tmp_path: Path) -> None:
    store = PostgresTestStore(
        tmp_path / "input-assets-port.db",
        input_asset_directory=str(tmp_path / "runtime-input-assets-port"),
    )
    body = b"bound evidence"
    digest = hashlib.sha256(body).hexdigest()

    async def chunks():
        yield body

    stored = await store.input_asset_store.put_stream(
        chunks(),
        expected_sha256=digest,
        expected_size=len(body),
        max_bytes=1024,
    )
    asset, _ = store.create_input_asset(
        asset_id="input_" + "a" * 32,
        user_id="owner-a",
        original_name="bound.txt",
        media_type="text/plain",
        content_sha256=digest,
        byte_size=len(body),
        storage_uri=stored.uri,
        object_version=stored.object_version,
        idempotency_key="asset-a",
    )
    store.create_runtime_run(
        run_id="run-bound",
        user_id="owner-a",
        session_id="session-a",
        agent_id="default",
        kind="agent",
        prompt="read",
        options={},
        input_asset_ids=[asset.asset_id],
    )
    store.create_runtime_run(
        run_id="run-unbound",
        user_id="owner-a",
        session_id="session-b",
        agent_id="default",
        kind="agent",
        prompt="read",
        options={},
    )
    port = ContextPort(store)
    resolved = await port.read_input_asset(
        CapabilityContext(user_id="owner-a", session_id="session-a", run_id="run-bound"),
        asset_id=asset.asset_id,
        max_bytes=1024,
    )
    with pytest.raises(PermissionError):
        await port.read_input_asset(
            CapabilityContext(user_id="owner-a", session_id="session-b", run_id="run-unbound"),
            asset_id=asset.asset_id,
            max_bytes=1024,
        )
    assert resolved["body"] == body
    with store._pool.connection() as connection:
        events = connection.execute(
            """SELECT event_type FROM runtime_input_asset_events
               WHERE asset_id=%s ORDER BY event_id""",
            (asset.asset_id,),
        ).fetchall()
    assert [event["event_type"] for event in events] == ["created", "bound", "read"]
    assert any(
        log.stage == "store.input_asset.read" for log in store.list_runtime_logs("run-bound")
    )


def test_knowledge_asset_api_lists_details_and_deletes_with_owner_scope(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "knowledge-assets.db")
    store.create_api_access_token(user_id="owner-a", actor_id="test", token="owner-a-token")
    store.create_api_access_token(user_id="owner-b", actor_id="test", token="owner-b-token")
    repository = KnowledgeRepository(store)
    repository.index_document(
        doc_id="doc-owner-a",
        user_id="owner-a",
        agent_id="research-agent",
        source_type="url",
        source_url="https://example.com/private-source",
        title="Private operating notes",
        chunks=[
            {
                "text": "Evidence-backed first chunk",
                "page": 1,
                "section_path": ["Operations", "Evidence"],
                "block_type": "paragraph",
                "char_start": 12,
                "char_end": 39,
            },
            {"text": "Second indexed chunk", "page": 2},
        ],
        metadata={
            "trace": {"parser": "readability"},
            "collection_refs": ["collection-private"],
        },
    )
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    owner = {"Authorization": "Bearer owner-a-token"}
    foreign = {"Authorization": "Bearer owner-b-token"}

    with client:
        listed = client.get("/v1/knowledge/documents", headers=owner)
        foreign_list = client.get("/v1/knowledge/documents", headers=foreign)
        detail = client.get("/v1/knowledge/documents/doc-owner-a", headers=owner)
        revisions = client.get("/v1/knowledge/documents/doc-owner-a/revisions", headers=owner)
        search = client.get(
            "/v1/knowledge/search",
            headers=owner,
            params={"q": "Evidence-backed", "collection_ref": "collection-private"},
        )
        excluded_search = client.get(
            "/v1/knowledge/search",
            headers=owner,
            params={"q": "Evidence-backed", "collection_ref": "another-collection"},
        )
        foreign_search = client.get(
            "/v1/knowledge/search",
            headers=foreign,
            params={"q": "Evidence-backed"},
        )
        source_state = client.get(
            "/v1/knowledge/source-state",
            headers=owner,
            params={"source_system": "runtime", "source_id": "doc-owner-a"},
        )
        health = client.get("/v1/knowledge/health", headers=owner)
        foreign_health = client.get("/v1/knowledge/health", headers=foreign)
        foreign_source_state = client.get(
            "/v1/knowledge/source-state",
            headers=foreign,
            params={"source_system": "runtime", "source_id": "doc-owner-a"},
        )
        foreign_revisions = client.get(
            "/v1/knowledge/documents/doc-owner-a/revisions", headers=foreign
        )
        foreign_detail = client.get("/v1/knowledge/documents/doc-owner-a", headers=foreign)
        foreign_delete = client.delete("/v1/knowledge/documents/doc-owner-a", headers=foreign)
        deleted = client.delete("/v1/knowledge/documents/doc-owner-a", headers=owner)
        after_delete = client.get("/v1/knowledge/documents", headers=owner)

    assert listed.status_code == 200
    assert listed.json()["summary"] == {
        "bases": 0,
        "total": 1,
        "chunks": 2,
        "size_bytes": len("Evidence-backed first chunkSecond indexed chunk"),
        "by_source": {"url": 1},
    }
    assert listed.json()["items"][0]["agent_id"] == "research-agent"
    assert foreign_list.status_code == 200 and foreign_list.json()["items"] == []
    assert detail.status_code == 200
    assert detail.json()["index_status"] == "ready"
    assert detail.json()["active_revision_id"].startswith("krev_")
    assert [item["page"] for item in detail.json()["chunks"]] == [1, 2]
    assert detail.json()["chunks"][0]["section_path"] == ["Operations", "Evidence"]
    assert detail.json()["chunks"][0]["block_type"] == "paragraph"
    assert detail.json()["chunks"][0]["char_start"] == 12
    assert detail.json()["chunks"][0]["char_end"] == 39
    assert revisions.status_code == 200
    assert revisions.json()["items"][0]["status"] == "active"
    assert revisions.json()["items"][0]["chunk_count"] == 2
    assert search.status_code == 200
    assert search.json()["items"][0]["source_id"] == "doc-owner-a"
    assert search.json()["items"][0]["section_path"] == ["Operations", "Evidence"]
    assert search.json()["items"][0]["trace"]["char_start"] == 12
    assert excluded_search.json()["items"] == []
    assert foreign_search.json()["items"] == []
    assert source_state.status_code == 200
    assert source_state.json()["document"]["doc_id"] == "doc-owner-a"
    assert source_state.json()["revisions"][0]["status"] == "active"
    assert health.status_code == 200
    assert health.json()["documents"] | {"last_ready_at_ms": None} == {
        "total": 1,
        "ready": 1,
        "indexing": 0,
        "failed": 0,
        "archived": 0,
        "last_ready_at_ms": None,
    }
    assert health.json()["documents"]["last_ready_at_ms"] is not None
    assert health.json()["revisions"] == {
        "total": 1,
        "succeeded": 1,
        "failed": 0,
        "queue_depth": 0,
    }
    assert health.json()["window"]["success_rate"] == 1.0
    assert foreign_health.status_code == 200
    assert foreign_health.json()["documents"]["total"] == 0
    assert foreign_health.json()["revisions"]["total"] == 0
    assert foreign_source_state.status_code == 404
    assert foreign_revisions.status_code == 404
    assert foreign_detail.status_code == 404
    assert foreign_delete.status_code == 404
    assert deleted.status_code == 204
    assert after_delete.json()["items"] == []

    with store._pool.connection() as connection:
        audit = connection.execute(
            """SELECT event_type,actor_id,data FROM knowledge_asset_events
                WHERE user_id=%s AND doc_id=%s ORDER BY created_at_ms DESC""",
            ("owner-a", "doc-owner-a"),
        ).fetchall()
    assert {row["event_type"] for row in audit} == {
        "revision_staged",
        "revision_ready",
        "revision_activated",
        "indexed",
        "deleted",
    }
    deleted_event = next(row for row in audit if row["event_type"] == "deleted")
    assert deleted_event["actor_id"].startswith("token:tok_")
    assert deleted_event["data"]["title"] == "Private operating notes"


def test_knowledge_bases_manage_collections_without_deleting_sources(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "knowledge-bases.db")
    store.create_api_access_token(user_id="owner-a", actor_id="test", token="owner-a-token")
    store.create_api_access_token(user_id="owner-b", actor_id="test", token="owner-b-token")
    repository = KnowledgeRepository(store)
    repository.index_document(
        doc_id="doc-a",
        user_id="owner-a",
        agent_id="default",
        source_type="note",
        source_url=None,
        title="Agent Runtime notes",
        chunks=[{"text": "Run and Task share one durable state machine."}],
    )
    repository.index_document(
        doc_id="doc-b",
        user_id="owner-b",
        agent_id="default",
        source_type="note",
        source_url=None,
        title="Foreign notes",
        chunks=[{"text": "Must remain foreign."}],
    )
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    owner = {"Authorization": "Bearer owner-a-token"}
    foreign = {"Authorization": "Bearer owner-b-token"}

    with client:
        created = client.post(
            "/v1/knowledge/bases",
            headers=owner,
            json={"name": "Runtime Architecture", "description": "Verified design notes"},
        )
        assert created.status_code == 201
        base_id = created.json()["knowledge_base_id"]
        duplicate = client.post(
            "/v1/knowledge/bases",
            headers=owner,
            json={"name": "Runtime Architecture"},
        )
        foreign_list = client.get("/v1/knowledge/bases", headers=foreign)
        foreign_bind = client.put(f"/v1/knowledge/bases/{base_id}/documents/doc-a", headers=foreign)
        foreign_document_bind = client.put(
            f"/v1/knowledge/bases/{base_id}/documents/doc-b", headers=owner
        )
        bound = client.put(f"/v1/knowledge/bases/{base_id}/documents/doc-a", headers=owner)
        bound_again = client.put(f"/v1/knowledge/bases/{base_id}/documents/doc-a", headers=owner)
        scoped = client.get(
            "/v1/knowledge/documents",
            headers=owner,
            params={"knowledge_base_id": base_id},
        )
        detail = client.get("/v1/knowledge/documents/doc-a", headers=owner)
        archived = client.patch(
            f"/v1/knowledge/bases/{base_id}",
            headers=owner,
            json={"status": "archived", "description": "Frozen reference"},
        )
        deleted = client.delete(f"/v1/knowledge/bases/{base_id}", headers=owner)
        sources_after_delete = client.get("/v1/knowledge/documents", headers=owner)
        bases_after_delete = client.get("/v1/knowledge/bases", headers=owner)

    assert duplicate.status_code == 409
    assert foreign_list.status_code == 200 and foreign_list.json()["items"] == []
    assert foreign_bind.status_code == 404
    assert foreign_document_bind.status_code == 404
    assert bound.status_code == 200 and bound.json()["created"] is True
    assert bound_again.status_code == 200 and bound_again.json()["created"] is False
    assert [item["doc_id"] for item in scoped.json()["items"]] == ["doc-a"]
    assert detail.json()["knowledge_base_ids"] == [base_id]
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert archived.json()["description"] == "Frozen reference"
    assert deleted.status_code == 204
    assert [item["doc_id"] for item in sources_after_delete.json()["items"]] == ["doc-a"]
    assert bases_after_delete.json()["items"] == []

    with store._pool.connection() as connection:
        events = connection.execute(
            """SELECT event_type FROM knowledge_base_events
                WHERE user_id=%s AND knowledge_base_id=%s""",
            ("owner-a", base_id),
        ).fetchall()
        bindings = connection.execute(
            "SELECT 1 FROM knowledge_base_documents WHERE knowledge_base_id=%s",
            (base_id,),
        ).fetchall()
    assert {row["event_type"] for row in events} == {
        "created",
        "document_added",
        "updated",
        "deleted",
    }
    assert bindings == []


def test_knowledge_revision_failure_preserves_previous_active_index(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "knowledge-revisions.db")
    repository = KnowledgeRepository(store)
    repository.index_document(
        doc_id="doc-versioned",
        user_id="owner-a",
        agent_id="default",
        source_type="note",
        source_url=None,
        title="Versioned notes",
        chunks=[{"text": "stable searchable knowledge", "page": 1}],
    )
    active_before = repository.get_document(user_id="owner-a", doc_id="doc-versioned")[
        "active_revision_id"
    ]
    failed_revision = repository.stage_index_revision(
        doc_id="doc-versioned",
        user_id="owner-a",
        agent_id="default",
        source_type="note",
        source_url=None,
        title="Versioned notes",
        chunks=[{"text": "replacement that must not activate", "page": 2}],
        source_system="porthouse-product",
        source_id="source-versioned",
        source_version="2",
        run_id="run-index-failed",
    )
    repository.fail_index_revision(
        user_id="owner-a",
        doc_id="doc-versioned",
        revision_id=failed_revision,
        actor_id="worker:test",
        error_code="PARSER_FAILED",
        error_message="synthetic parser failure",
    )

    document = repository.get_document(user_id="owner-a", doc_id="doc-versioned")
    hits = repository.search(user_id="owner-a", query="stable", top_k=10)
    replacement_hits = repository.search(user_id="owner-a", query="replacement", top_k=10)
    revisions = repository.list_index_revisions(user_id="owner-a", doc_id="doc-versioned")
    health = repository.index_health(user_id="owner-a", since_ms=0)
    assert document["active_revision_id"] == active_before
    assert document["index_status"] == "ready"
    assert [hit["doc_id"] for hit in hits] == ["doc-versioned"]
    assert replacement_hits == []
    assert revisions[0]["status"] == "failed"
    assert revisions[0]["run_id"] == "run-index-failed"
    assert health["revisions"]["failed"] == 1
    assert health["failure_codes"][0]["error_code"] == "PARSER_FAILED"


def test_knowledge_revision_rejects_stale_generation_after_newer_activation(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "knowledge-generation-order.db")
    repository = KnowledgeRepository(store)
    older = repository.stage_index_revision(
        doc_id="doc-ordered",
        user_id="owner-a",
        agent_id="default",
        source_type="note",
        source_url=None,
        title="Older snapshot",
        chunks=[{"text": "old generation content"}],
        source_system="porthouse-product",
        source_id="source-ordered",
        source_version="1",
        source_generation=1,
    )
    repository.mark_index_revision_ready(
        user_id="owner-a", doc_id="doc-ordered", revision_id=older, actor_id="test"
    )
    newer = repository.stage_index_revision(
        doc_id="doc-ordered",
        user_id="owner-a",
        agent_id="default",
        source_type="note",
        source_url=None,
        title="Newer snapshot",
        chunks=[{"text": "new generation content"}],
        source_system="porthouse-product",
        source_id="source-ordered",
        source_version="1",
        source_generation=2,
    )
    repository.mark_index_revision_ready(
        user_id="owner-a", doc_id="doc-ordered", revision_id=newer, actor_id="test"
    )
    assert (
        repository.activate_index_revision(
            user_id="owner-a", doc_id="doc-ordered", revision_id=newer, actor_id="test"
        )
        is True
    )
    assert (
        repository.activate_index_revision(
            user_id="owner-a", doc_id="doc-ordered", revision_id=older, actor_id="test"
        )
        is False
    )

    document = repository.get_document(user_id="owner-a", doc_id="doc-ordered")
    assert document["source_generation"] == 2
    assert document["title"] == "Newer snapshot"
    assert repository.search(user_id="owner-a", query="new generation", top_k=5)
    assert repository.search(user_id="owner-a", query="old generation", top_k=5) == []
    revisions = repository.list_index_revisions(user_id="owner-a", doc_id="doc-ordered")
    assert {item["revision_id"]: item["status"] for item in revisions} == {
        newer: "active",
        older: "superseded",
    }


def test_knowledge_revision_activation_atomically_switches_search_projection(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "knowledge-activation.db")
    repository = KnowledgeRepository(store)
    repository.index_document(
        doc_id="doc-switch",
        user_id="owner-a",
        agent_id="default",
        source_type="note",
        source_url=None,
        title="Switchable notes",
        chunks=[{"text": "old indexed phrase"}],
    )
    revision_id = repository.stage_index_revision(
        doc_id="doc-switch",
        user_id="owner-a",
        agent_id="default",
        source_type="note",
        source_url=None,
        title="Switchable notes",
        chunks=[{"text": "new indexed phrase", "section_path": ["Chapter 1"]}],
        source_version="2",
    )
    assert repository.search(user_id="owner-a", query="old", top_k=10)
    assert repository.search(user_id="owner-a", query="new", top_k=10) == []
    repository.mark_index_revision_ready(
        user_id="owner-a",
        doc_id="doc-switch",
        revision_id=revision_id,
        actor_id="worker:test",
    )
    repository.activate_index_revision(
        user_id="owner-a",
        doc_id="doc-switch",
        revision_id=revision_id,
        actor_id="worker:test",
    )
    assert repository.search(user_id="owner-a", query="old", top_k=10) == []
    hits = repository.search(user_id="owner-a", query="new", top_k=10)
    assert hits[0]["section_path"] == ["Chapter 1"]
    assert hits[0]["revision_id"] == revision_id


@pytest.mark.asyncio
async def test_context_port_failed_parse_attempt_preserves_active_projection(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "knowledge-context-failure.db")
    repository = KnowledgeRepository(store)
    port = ContextPort(store)
    context = CapabilityContext(
        user_id="owner-a",
        session_id="session-index",
        run_id="run-index-failed",
        agent_id="default",
    )
    await port.index_knowledge(
        context,
        source_type="note",
        source_url="",
        title="Stable source",
        chunks=[{"text": "stable searchable projection"}],
        source_system="porthouse-product",
        source_id="source-failure",
        source_version="1",
        source_generation=1,
    )
    doc_id = await port.fail_knowledge_index(
        context,
        source_type="file",
        source_url="porthouse-local://vault/source-failure.pdf",
        title="Broken replacement",
        source_system="porthouse-product",
        source_id="source-failure",
        source_version="2",
        source_generation=2,
        parser_id="pdf-pypdf",
        chunker_id="semantic-text-v1",
        chunker_version="2",
        error_code="PARSER_FAILED",
        error_message="encrypted document cannot be parsed",
    )

    document = repository.get_document(user_id="owner-a", doc_id=doc_id)
    revisions = repository.list_index_revisions(user_id="owner-a", doc_id=doc_id)
    assert document["title"] == "Stable source"
    assert document["source_generation"] == 2
    assert document["index_status"] == "ready"
    assert repository.search(user_id="owner-a", query="stable", top_k=5)
    assert revisions[0]["status"] == "failed"
    assert revisions[0]["source_version"] == "2"
    assert revisions[0]["error_code"] == "PARSER_FAILED"
    assert revisions[0]["run_id"] == "run-index-failed"
    assert revisions[0]["chunker_id"] == "semantic-text-v1"
    assert revisions[0]["chunker_version"] == "2"


@pytest.mark.asyncio
async def test_context_port_first_failed_parse_remains_visible_for_retry(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "knowledge-first-failure.db")
    repository = KnowledgeRepository(store)
    port = ContextPort(store)
    context = CapabilityContext(
        user_id="owner-a",
        session_id="session-index",
        run_id="run-first-failure",
        agent_id="default",
    )
    doc_id = await port.fail_knowledge_index(
        context,
        source_type="file",
        source_url="porthouse-cloud://vault/report.pdf",
        title="Quarterly report",
        source_system="porthouse-product",
        source_id="source-first-failure",
        source_version="4",
        source_generation=7,
        error_code="REFERENCE_RESOLVER_UNAVAILABLE",
        error_message="cloud vault resolver is not installed",
    )
    document = repository.get_document(user_id="owner-a", doc_id=doc_id)
    assert document["title"] == "Quarterly report"
    assert document["source_version"] == "4"
    assert document["source_generation"] == 7
    assert document["index_status"] == "failed"
    assert document["active_revision_id"] is None


class _KnowledgeSubmissionStore:
    def __init__(self) -> None:
        self.definition = type(
            "Definition",
            (),
            {
                "ref": CapabilityRef(
                    "knowledge.index",
                    "1.0.0",
                    CapabilityKind.TOOL,
                    "capability-context-assets",
                    "1.0.0",
                    "sha256:" + "a" * 64,
                ),
                "permissions": ("knowledge.write",),
            },
        )()

    def list_capability_definitions(self):
        return [self.definition]

    def get_agent_profile(self, _agent_id=None):
        return type("Profile", (), {"definition": type("Agent", (), {"agent_id": "default"})()})()


class _KnowledgeSubmissionRuntime:
    def __init__(self) -> None:
        self.spec = None

    async def submit_graph(self, spec):  # noqa: ANN001
        self.spec = spec
        return type("Run", (), {"run_id": "run-index", "status": "queued"})()


@pytest.mark.asyncio
async def test_knowledge_index_request_compiles_to_capability_graph() -> None:
    store = _KnowledgeSubmissionStore()
    runtime = _KnowledgeSubmissionRuntime()
    service = object.__new__(KnowledgeAssetService)
    service.store = store
    service.runtime = runtime
    context = RequestContext(
        principal=Principal(subject="token:owner", user_id="owner-a", role="user"),
        request_id="request-index",
        tracker_id="tracker-index",
        idempotency_key="knowledge:source-a:2",
    )
    record = await service.submit_index_request(
        context,
        {
            "source_system": "porthouse-product",
            "source_id": "source-a",
            "source_version": "2",
            "source_generation": 2,
            "source_status": "active",
            "source_type": "note",
            "title": "Source A",
            "content": "snapshot",
            "source_url": "",
            "attachments": [],
            "tags": [],
            "collection_refs": [],
            "content_sha256": "a" * 64,
            "index_profile_id": "lexical-v1",
        },
    )
    assert record.run_id == "run-index"
    assert runtime.spec.idempotency_key == "knowledge:source-a:2"
    assert runtime.spec.tasks[0].node_type == "capability"
    assert runtime.spec.tasks[0].capability.capability_id == "knowledge.index"
    assert runtime.spec.tasks[0].capability_input["source_version"] == "2"
    assert runtime.spec.authority_permissions == ["knowledge.write"]


@pytest.mark.asyncio
async def test_knowledge_index_request_freezes_runtime_input_assets() -> None:
    store = _KnowledgeSubmissionStore()
    runtime = _KnowledgeSubmissionRuntime()
    service = object.__new__(KnowledgeAssetService)
    service.store = store
    service.runtime = runtime
    context = RequestContext(
        principal=Principal(subject="token:owner", user_id="owner-a", role="user"),
        request_id="request-index-file",
        idempotency_key="knowledge:file-a:1",
    )
    asset_id = "input_" + "a" * 32
    await service.submit_index_request(
        context,
        {
            "source_system": "porthouse-product",
            "source_id": "source-file-a",
            "source_version": "1",
            "source_generation": 1,
            "source_status": "active",
            "source_type": "file",
            "title": "File A",
            "content": "",
            "source_url": "",
            "attachments": [
                {
                    "reference_kind": "runtime_input",
                    "asset_id": asset_id,
                    "display_name": "file-a.txt",
                    "media_type": "text/plain",
                }
            ],
            "tags": [],
            "collection_refs": [],
            "content_sha256": "a" * 64,
            "index_profile_id": "lexical-v1",
        },
    )
    assert runtime.spec.input_asset_ids == [asset_id]


def test_app_namespace_shares_source_id_with_personal_library(tmp_path: Path) -> None:
    """Same user, same source_id, two namespaces: both live, ids differ, isolated."""

    store = PostgresTestStore(tmp_path / "knowledge-app-namespace.db")
    repository = KnowledgeRepository(store)
    for namespace, title, body in (
        (None, "Personal digest", "personal evidence chunk"),
        ("appinst-radar", "App digest", "app evidence chunk"),
        ("appinst-panel", "Other app digest", "other app evidence chunk"),
    ):
        repository.index_document(
            doc_id=f"doc-{namespace or 'personal'}",
            user_id="owner-a",
            agent_id="default",
            source_type="article",
            source_url="https://example.com/same-source",
            title=title,
            chunks=[{"text": body}],
            app_installation_id=namespace,
        )

    # 个人检索只看见 NULL namespace；App 检索只看见自己的 installation。
    personal = repository.search(
        user_id="owner-a", query="evidence", top_k=10
    )
    assert {row["title"] for row in personal} == {"Personal digest"}
    app_radar = repository.search(
        user_id="owner-a", query="evidence", top_k=10, app_installation_id="appinst-radar"
    )
    assert {row["title"] for row in app_radar} == {"App digest"}
    app_panel = repository.search(
        user_id="owner-a", query="evidence", top_k=10, app_installation_id="appinst-panel"
    )
    assert {row["title"] for row in app_panel} == {"Other app digest"}

    # 文档列表同样按 namespace 隔离
    assert [row["doc_id"] for row in repository.list_documents(user_id="owner-a")] == [
        "doc-personal"
    ]
    assert [
        row["doc_id"]
        for row in repository.list_documents(
            user_id="owner-a", app_installation_id="appinst-radar"
        )
    ] == ["doc-appinst-radar"]

    # source-state 按 namespace 解析；doc_id 不同（身份哈希含 installation）
    personal_state = repository.get_document_by_source(
        user_id="owner-a", source_system="runtime", source_id="doc-personal"
    )
    app_state = repository.get_document_by_source(
        user_id="owner-a",
        source_system="runtime",
        source_id="doc-appinst-radar",
        app_installation_id="appinst-radar",
    )
    assert personal_state is not None and app_state is not None
    assert personal_state["doc_id"] != app_state["doc_id"]

    # 删除只作用于本 namespace
    repository.delete_document(
        user_id="owner-a",
        doc_id="doc-appinst-radar",
        actor_id="test",
        app_installation_id="appinst-radar",
    )
    assert (
        repository.get_document_by_source(
            user_id="owner-a",
            source_system="runtime",
            source_id="doc-appinst-radar",
            app_installation_id="appinst-radar",
        )
        is None
    )
    assert repository.get_document(user_id="owner-a", doc_id="doc-personal") is not None


@pytest.mark.asyncio
async def test_app_knowledge_router_scopes_and_isolation(tmp_path: Path) -> None:
    from tests.test_app_pack_control_plane import _manifest as base_manifest

    store = PostgresTestStore(tmp_path / "knowledge-app-router.db")
    repository = KnowledgeRepository(store)
    repository.index_document(
        doc_id="doc-personal-lib",
        user_id="owner-a",
        agent_id="default",
        source_type="article",
        source_url="https://example.com/personal-lib",
        title="Personal note",
        chunks=[{"text": "personal library evidence"}],
    )

    # 安装一个声明 knowledge 权限的 App
    from porthouse.application.app_packs import AppPackService

    service = AppPackService(store)
    manifest = {
        **base_manifest(),
        "permissions": ["runs.submit", "knowledge.read", "knowledge.write"],
    }
    async def _prepare() -> str:
        await service.save_draft(manifest, actor_id="admin")
        await service.publish(
            "app.market-radar", "1.0.0", actor_id="admin", user_id="owner-a"
        )
        installed = await service.install(
            "app.market-radar",
            "1.0.0",
            user_id="owner-a",
            actor_id="admin",
            configuration={},
            granted_permissions=["runs.submit", "knowledge.read", "knowledge.write"],
        )
        installation = await service.transition(
            installed["installation_id"],
            user_id="owner-a",
            actor_id="admin",
            action="activate",
        )
        return str(installation["installation_id"])

    installation_id = await _prepare()
    repository.index_document(
        doc_id="doc-app-lib",
        user_id="owner-a",
        agent_id="default",
        source_type="article",
        source_url="https://example.com/app-lib",
        title="App library note",
        chunks=[{"text": "app library evidence"}],
        app_installation_id=installation_id,
    )
    app_client, app_secret = store.create_app_client(
        app_id="app.market-radar",
        name="Market Radar SaaS",
        allowed_scopes=["knowledge.read", "knowledge.write"],
        actor_id="admin",
    )
    from datetime import datetime, timedelta, timezone

    grant = store.create_app_delegation_grant(
        client_id=app_client["client_id"],
        installation_id=installation_id,
        user_id="owner-a",
        scopes=["knowledge.read", "knowledge.write"],
        expires_at=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        actor_id="token:owner",
    )
    store.create_api_access_token(
        user_id="owner-a", actor_id="test", token="owner-a-token"
    )
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    owner = {"Authorization": "Bearer owner-a-token"}
    with client:
        exchanged = client.post(
            "/v1/app-auth/token",
            json={
                "client_id": app_client["client_id"],
                "client_secret": app_secret,
                "grant_id": grant["grant_id"],
                "scopes": ["knowledge.read", "knowledge.write"],
            },
        )
        assert exchanged.status_code == 200, exchanged.text
        delegated = {"Authorization": f"Bearer {exchanged.json()['access_token']}"}

        # 委托 token 只看到 App namespace
        listed = client.get(
            f"/v1/apps/{installation_id}/knowledge/documents", headers=delegated
        )
        assert listed.status_code == 200, listed.text
        assert [item["doc_id"] for item in listed.json()["items"]] == ["doc-app-lib"]

        # 个人库对委托 token 不可达（scope 只有 knowledge.*，公共 /v1/knowledge 需要 api.read）
        personal_surface = client.get("/v1/knowledge/documents", headers=delegated)
        assert personal_surface.status_code == 403

        # 隔离：App 检索搜不到个人文档
        search = client.get(
            f"/v1/apps/{installation_id}/knowledge/search",
            headers=delegated,
            params={"q": "evidence"},
        )
        assert search.status_code == 200, search.text
        titles = {row["title"] for row in search.json()["items"]}
        assert titles == {"App library note"}

        # 别的 installation 路径 → 404
        other = client.get(
            "/v1/apps/appinst-someone-else/knowledge/documents", headers=delegated
        )
        assert other.status_code == 404

        # owner token（scope=*）可以跨 namespace 读取 App 库
        owner_listed = client.get(
            f"/v1/apps/{installation_id}/knowledge/documents", headers=owner
        )
        assert owner_listed.status_code == 200
        assert [item["doc_id"] for item in owner_listed.json()["items"]] == [
            "doc-app-lib"
        ]
