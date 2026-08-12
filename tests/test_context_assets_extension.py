"""Contracts for the optional context-assets capability package."""

from types import SimpleNamespace

import pytest
from joyhousebot_capability_context_assets import plugin as context_assets

from joyhousebot.capabilities import CapabilityPluginRegistry
from joyhousebot.extension_sdk import CapabilityContext


class _FakeContextServices:
    def __init__(self) -> None:
        self.context = self
        self.indexed: dict | None = None

    async def search(self, context, **kwargs):  # noqa: ANN001
        return [{"content": "evidence", "user_id": context.user_id, **kwargs}]

    async def read_memory(self, context, **kwargs):  # noqa: ANN001
        return f"{context.user_id}:{kwargs['relative_path']}"

    async def index_knowledge(self, context, **kwargs):  # noqa: ANN001
        self.indexed = {"user_id": context.user_id, **kwargs}
        return "doc-1"


def _context(**overrides):
    values = {
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_id": "agent-a",
        "memory_scope": "user:user-a:agent:agent-a",
        "memory_policy": {},
        "services": _FakeContextServices(),
        "metadata": {
            "permissions": [
                "context.read",
                "memory.read",
                "network.http.read",
                "knowledge.write",
            ]
        },
    }
    values.update(overrides)
    return CapabilityContext(**values)


def test_context_assets_registers_scoped_versioned_capabilities() -> None:
    registry = CapabilityPluginRegistry()
    registry.register_plugin(context_assets.ContextAssetsPlugin())
    definitions = {item.ref.capability_id: item for item in registry.list_capabilities()}
    assert set(definitions) == {
        "fetch_url_to_knowledgebase",
        "knowledge.index",
        "memory_get",
        "retrieve",
    }
    assert definitions["fetch_url_to_knowledgebase"].side_effect == "write"
    assert definitions["fetch_url_to_knowledgebase"].ref.plugin_id == (
        "capability-context-assets"
    )
    assert registry.manifests()[0].runtime_contract_version == 2


@pytest.mark.asyncio
async def test_knowledge_index_capability_preserves_snapshot_and_run_identity() -> None:
    services = _FakeContextServices()
    result = await context_assets.IndexKnowledgeHandler().execute(
        _context(
            services=services,
            action_id="action-index",
            idempotency_key="knowledge:source-a:2",
        ),
        {
            "source_system": "joyhouse-product",
            "source_id": "source-a",
            "source_version": "2",
            "source_generation": 2,
            "source_status": "active",
            "source_type": "note",
            "title": "Versioned note",
            "content": "A long-lived source snapshot.",
            "source_url": "",
            "attachments": [],
            "tags": ["market"],
            "collection_refs": ["collection-a"],
            "content_sha256": "a" * 64,
            "index_profile_id": "lexical-v1",
        },
    )
    assert result.success is True
    assert result.write_receipt.idempotency_key == "knowledge:source-a:2"
    assert services.indexed["source_system"] == "joyhouse-product"
    assert services.indexed["source_id"] == "source-a"
    assert services.indexed["source_version"] == "2"
    assert services.indexed["source_generation"] == 2
    assert services.indexed["metadata"]["collection_refs"] == ["collection-a"]


@pytest.mark.asyncio
async def test_memory_handler_rejects_traversal_before_runtime_service() -> None:
    result = await context_assets.MemoryGetHandler().execute(
        _context(),
        {"path": "memory/../private.md"},
    )
    assert result.success is False
    assert result.error["code"] == "INVALID_PARAMETERS"


@pytest.mark.asyncio
async def test_retrieve_handler_uses_runtime_scoped_service() -> None:
    result = await context_assets.RetrieveHandler().execute(
        _context(),
        {
            "query": "market",
            "scope": "knowledge",
            "top_k": 3,
            "collection_ref": "collection-market",
        },
    )
    assert result.success is True
    assert result.output["hits"][0]["user_id"] == "user-a"
    assert result.output["hits"][0]["collection_ref"] == "collection-market"


@pytest.mark.asyncio
async def test_knowledge_write_preserves_frozen_action_identity(monkeypatch) -> None:
    async def fake_fetch(url):
        return SimpleNamespace(
            source_type="url",
            source_url=url,
            title="Example",
            chunks=[SimpleNamespace(text="content", page=None)],
            trace={"status": 200},
        )

    monkeypatch.setattr(context_assets, "fetch_and_ingest_url", fake_fetch)
    services = _FakeContextServices()
    result = await context_assets.FetchUrlToKnowledgebaseHandler().execute(
        _context(
            services=services,
            action_id="action-a",
            idempotency_key="action:action-a",
        ),
        {"url": "https://example.com/article"},
    )
    assert result.success is True
    assert result.write_receipt.action_id == "action-a"
    assert result.write_receipt.idempotency_key == "action:action-a"
    assert result.write_receipt.provider_operation_id == "doc-1"
    assert services.indexed["user_id"] == "user-a"


@pytest.mark.asyncio
async def test_knowledge_write_requires_frozen_action_before_fetch(monkeypatch) -> None:
    called = False

    async def fake_fetch(_url):
        nonlocal called
        called = True

    monkeypatch.setattr(context_assets, "fetch_and_ingest_url", fake_fetch)
    result = await context_assets.FetchUrlToKnowledgebaseHandler().execute(
        _context(action_id=None, idempotency_key=None),
        {"url": "https://example.com/article"},
    )
    assert result.success is False
    assert result.error["code"] == "ACTION_IDENTITY_REQUIRED"
    assert called is False
