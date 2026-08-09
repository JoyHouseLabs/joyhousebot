"""Owner-scoped Knowledge asset control-plane contracts."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from joyhousebot.services.retrieval.knowledge_repository import KnowledgeRepository
from tests.support.postgres_store import PostgresTestStore


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
            {"text": "Evidence-backed first chunk", "page": 1},
            {"text": "Second indexed chunk", "page": 2},
        ],
        metadata={"trace": {"parser": "readability"}},
    )
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    owner = {"Authorization": "Bearer owner-a-token"}
    foreign = {"Authorization": "Bearer owner-b-token"}

    with client:
        listed = client.get("/v1/knowledge/documents", headers=owner)
        foreign_list = client.get("/v1/knowledge/documents", headers=foreign)
        detail = client.get("/v1/knowledge/documents/doc-owner-a", headers=owner)
        foreign_detail = client.get(
            "/v1/knowledge/documents/doc-owner-a", headers=foreign
        )
        foreign_delete = client.delete(
            "/v1/knowledge/documents/doc-owner-a", headers=foreign
        )
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
    assert [item["page"] for item in detail.json()["chunks"]] == [1, 2]
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
    assert {row["event_type"] for row in audit} == {"indexed", "deleted"}
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
        foreign_bind = client.put(
            f"/v1/knowledge/bases/{base_id}/documents/doc-a", headers=foreign
        )
        foreign_document_bind = client.put(
            f"/v1/knowledge/bases/{base_id}/documents/doc-b", headers=owner
        )
        bound = client.put(
            f"/v1/knowledge/bases/{base_id}/documents/doc-a", headers=owner
        )
        bound_again = client.put(
            f"/v1/knowledge/bases/{base_id}/documents/doc-a", headers=owner
        )
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
