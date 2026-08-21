"""Contracts for the optional context-assets capability package."""

from types import SimpleNamespace

import pytest
from joyhousebot_capability_context_assets import extension as context_assets

from joyhousebot.capabilities import CapabilityExtensionRegistry
from joyhousebot.extension_sdk import CapabilityContext


class _FakeContextServices:
    def __init__(self) -> None:
        self.context = self
        self.indexed: dict | None = None
        self.failed: dict | None = None

    async def search(self, context, **kwargs):  # noqa: ANN001
        return [{"content": "evidence", "user_id": context.user_id, **kwargs}]

    async def read_memory(self, context, **kwargs):  # noqa: ANN001
        return f"{context.user_id}:{kwargs['relative_path']}"

    async def index_knowledge(self, context, **kwargs):  # noqa: ANN001
        self.indexed = {"user_id": context.user_id, **kwargs}
        return "doc-1"

    async def fail_knowledge_index(self, context, **kwargs):  # noqa: ANN001
        self.failed = {"user_id": context.user_id, **kwargs}
        return "doc-failed"


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
    registry = CapabilityExtensionRegistry()
    registry.register_extension(context_assets.ContextAssetsExtension())
    definitions = {item.ref.capability_id: item for item in registry.list_capabilities()}
    assert set(definitions) == {
        "fetch_url_to_knowledgebase",
        "knowledge.index",
        "memory_get",
        "retrieve",
    }
    assert definitions["fetch_url_to_knowledgebase"].side_effect == "write"
    assert definitions["fetch_url_to_knowledgebase"].ref.extension_id == ("capability-context-assets")
    assert definitions["knowledge.index"].side_effect == "internal"
    attachment_schema = definitions["knowledge.index"].input_schema["properties"]["attachments"][
        "items"
    ]
    assert "runtime_input" in attachment_schema["properties"]["reference_kind"]["enum"]
    assert "asset_id" in attachment_schema["properties"]
    assert registry.manifests()[0].version == "1.6.0"
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
            "source_system": "joyhousebot-product",
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
            "embedding_profile_id": "knowledge-default:v1",
        },
    )
    assert result.success is True
    assert result.write_receipt.idempotency_key == "knowledge:source-a:2"
    assert services.indexed["source_system"] == "joyhousebot-product"
    assert services.indexed["source_id"] == "source-a"
    assert services.indexed["source_version"] == "2"
    assert services.indexed["source_generation"] == 2
    assert services.indexed["metadata"]["collection_refs"] == ["collection-a"]
    assert services.indexed["parser_id"] == "plain-text"
    assert services.indexed["chunker_version"] == "2"
    assert services.indexed["embedding_profile_id"] == "knowledge-default:v1"


@pytest.mark.asyncio
async def test_knowledge_index_failure_records_current_chunker_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = _FakeContextServices()

    async def fail_parse(*args, **kwargs):  # noqa: ANN002, ANN003
        raise context_assets.SourceParseError(
            "REFERENCE_READ_FAILED",
            "input asset object is unavailable",
            parser_id="unresolved",
            parser_version="1",
            retryable=True,
        )

    monkeypatch.setattr(context_assets.DEFAULT_SOURCE_PARSERS, "parse_snapshot", fail_parse)
    result = await context_assets.IndexKnowledgeHandler().execute(
        _context(
            services=services,
            action_id="action-failed-index",
            idempotency_key="knowledge:source-failed:1",
        ),
        {
            "source_system": "joyhousebot-product",
            "source_id": "source-failed",
            "source_version": "1",
            "source_generation": 1,
            "source_type": "file",
            "title": "Unavailable PDF",
            "content_sha256": "a" * 64,
            "attachments": [],
        },
    )

    assert result.success is False
    assert services.failed["chunker_id"] == "semantic-text-v1"
    assert services.failed["chunker_version"] == "2"


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


@pytest.mark.asyncio
async def test_knowledge_index_records_parser_failure_on_the_runtime_port() -> None:
    services = _FakeContextServices()
    result = await context_assets.IndexKnowledgeHandler().execute(
        _context(
            services=services,
            action_id="action-index",
            idempotency_key="knowledge:source-file:3",
        ),
        {
            "source_system": "joyhousebot-product",
            "source_id": "source-file",
            "source_version": "3",
            "source_generation": 3,
            "source_status": "active",
            "source_type": "file",
            "title": "Private file",
            "content": "",
            "source_url": "",
            "attachments": [
                {
                    "reference_kind": "local_vault",
                    "uri": "joyhousebot-local://vault/private.docx",
                    "display_name": "private.docx",
                }
            ],
            "content_sha256": "b" * 64,
        },
    )
    assert result.success is False
    assert result.error["code"] == "REFERENCE_RESOLVER_UNAVAILABLE"
    assert services.indexed is None
    assert services.failed["source_id"] == "source-file"
    assert services.failed["source_generation"] == 3
    assert services.failed["error_code"] == "REFERENCE_RESOLVER_UNAVAILABLE"
