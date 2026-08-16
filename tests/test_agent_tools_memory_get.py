"""Tests for scoped durable memory_get capability handler."""

import pytest
from porthouse_capability_context_assets.plugin import MemoryGetHandler

from porthouse.capabilities.services import CapabilityServiceBroker
from porthouse.extension_sdk import CapabilityContext
from porthouse.services.memory.store import MemoryStore
from tests.support.postgres_store import PostgresTestStore


@pytest.fixture
def durable_memory(tmp_path):
    store = PostgresTestStore(tmp_path / "memory-tool.db")
    scope = "user:user-a:agent:default"
    memory = MemoryStore(store, scope)
    memory.write_long_term("User prefers Python.\nProject X.")
    memory.write_relative("2026-02-25.md", "line 1\nline 2\nline 3")
    context = CapabilityContext(
        run_id="run-a",
        session_id="session-a",
        user_id="user-a",
        agent_id="default",
        memory_scope=scope,
        memory_policy={
            "enabled": True,
            "mode": "personalized",
            "read_mode": "tools",
            "write_mode": "none",
            "layers": {"long_term": {"read": True}},
        },
        services=CapabilityServiceBroker(store),
    )
    return MemoryGetHandler(), context


@pytest.mark.asyncio
async def test_memory_get_reads_document(durable_memory) -> None:
    handler, context = durable_memory
    result = await handler.execute(context, {"path": "memory/MEMORY.md"})
    assert result.success is True
    assert "User prefers Python" in result.output["text"]


@pytest.mark.asyncio
async def test_memory_get_missing_document_is_empty(durable_memory) -> None:
    handler, context = durable_memory
    result = await handler.execute(context, {"path": "memory/missing.md"})
    assert result.success is True
    assert result.output["text"] == ""


@pytest.mark.asyncio
async def test_memory_get_rejects_traversal(durable_memory) -> None:
    handler, context = durable_memory
    result = await handler.execute(context, {"path": "memory/../other.md"})
    assert result.success is False
    assert result.error["code"] == "INVALID_PARAMETERS"


@pytest.mark.asyncio
async def test_memory_get_line_range(durable_memory) -> None:
    handler, context = durable_memory
    result = await handler.execute(
        context,
        {
            "path": "memory/2026-02-25.md",
            "start_line": 2,
            "num_lines": 2,
        },
    )
    assert result.success is True
    assert result.output["text"] == "line 2\nline 3"


@pytest.mark.asyncio
async def test_memory_get_requires_run_scope(durable_memory) -> None:
    handler, context = durable_memory
    context = CapabilityContext(
        user_id=context.user_id,
        session_id=context.session_id,
        run_id=context.run_id,
        agent_id=context.agent_id,
        memory_policy=context.memory_policy,
        services=context.services,
    )
    result = await handler.execute(context, {"path": "memory/MEMORY.md"})
    assert result.success is False
    assert result.error["code"] == "CONTEXT_REQUIRED"
