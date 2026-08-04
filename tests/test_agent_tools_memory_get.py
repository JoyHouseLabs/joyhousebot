"""Tests for scoped durable memory_get access."""

import json

import pytest

from joyhousebot.agent.memory import MemoryStore
from joyhousebot.agent.tools.memory_get import MemoryGetTool
from joyhousebot.capabilities.tool_adapter import ToolInvocationError
from joyhousebot.runtime.context import ToolExecutionContext
from tests.support.postgres_store import PostgresTestStore


@pytest.fixture
def durable_memory(tmp_path):
    store = PostgresTestStore(tmp_path / "memory-tool.db")
    scope = "user:user-a:agent:default"
    memory = MemoryStore(store, scope)
    memory.write_long_term("User prefers Python.\nProject X.")
    memory.write_relative("2026-02-25.md", "line 1\nline 2\nline 3")
    context = ToolExecutionContext(
        run_id="run-a",
        session_key="session-a",
        channel="api",
        chat_id="chat-a",
        user_id="user-a",
        memory_scope=scope,
    )
    return MemoryGetTool(store), context


@pytest.mark.asyncio
async def test_memory_get_reads_document(durable_memory) -> None:
    tool, context = durable_memory
    data = json.loads(await tool.execute(path="memory/MEMORY.md", tool_context=context))
    assert "User prefers Python" in data["text"]


@pytest.mark.asyncio
async def test_memory_get_missing_document_is_empty(durable_memory) -> None:
    tool, context = durable_memory
    data = json.loads(await tool.execute(path="memory/missing.md", tool_context=context))
    assert data["text"] == ""


@pytest.mark.asyncio
async def test_memory_get_rejects_traversal(durable_memory) -> None:
    tool, context = durable_memory
    with pytest.raises(ToolInvocationError, match="invalid memory path"):
        await tool.execute(path="memory/../other.md", tool_context=context)


@pytest.mark.asyncio
async def test_memory_get_line_range(durable_memory) -> None:
    tool, context = durable_memory
    data = json.loads(
        await tool.execute(
            path="memory/2026-02-25.md",
            start_line=2,
            num_lines=2,
            tool_context=context,
        )
    )
    assert data["text"] == "line 2\nline 3"


@pytest.mark.asyncio
async def test_memory_get_requires_run_scope(durable_memory) -> None:
    tool, _context = durable_memory
    with pytest.raises(ToolInvocationError, match="run memory scope"):
        await tool.execute(path="memory/MEMORY.md")
