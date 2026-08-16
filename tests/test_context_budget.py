"""Full-input context priority, overflow, compression, and admission tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from porthouse.agent.context import ContextBuilder
from porthouse.agent.context_budget import allocate_context, context_candidate
from porthouse.agent.context_manifest import source_entry, stable_hash
from porthouse.agent.executor import NativeAgentExecutor
from porthouse.contracts.tools import Tool
from porthouse.domain.agents import AgentRevision
from porthouse.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from porthouse.runtime.context import RunContext
from porthouse.runtime.models import AgentOptions
from porthouse.runtime.runner import NativeAgentRuntime
from porthouse.services.memory.store import MemoryStore
from porthouse.session.runtime_manager import RuntimeSessionManager
from tests.support.capabilities import register_tool_fixture
from tests.support.postgres_store import PostgresTestStore


def _candidate(
    source_id: str,
    content: Any,
    *,
    target: str,
    priority: int,
    required: bool,
    order: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = source_entry(
        source_kind="test_context",
        source_id=source_id,
        content=content,
        classification="internal",
        authority="test",
        freshness="test",
        priority=priority,
        included_reason="test_candidate",
    )
    candidate = context_candidate(
        candidate_id=source_id,
        target=target,
        content=content,
        source_keys=[("test_context", source_id)],
        priority=priority,
        required=required,
        order=order,
        separator="\n\n" if target == "system" else "",
    )
    return candidate, source


def test_priority_budget_keeps_required_and_high_priority_sources() -> None:
    system = _candidate(
        "system", "system contract", target="system", priority=100, required=True, order=0
    )
    current = _candidate(
        "current",
        {"role": "user", "content": "current request"},
        target="message",
        priority=100,
        required=True,
        order=100,
    )
    recent = _candidate(
        "recent",
        {"role": "assistant", "content": "recent evidence " * 20},
        target="message",
        priority=80,
        required=False,
        order=90,
    )
    catalog = _candidate(
        "catalog",
        "low priority catalog " * 100,
        target="system",
        priority=40,
        required=False,
        order=1,
    )
    required_and_recent = [system[0], current[0], recent[0]]
    budget = allocate_context(
        base_candidates=required_and_recent,
        base_sources=[system[1], current[1], recent[1]],
    ).estimated_tokens

    prepared = allocate_context(
        base_candidates=[system[0], catalog[0], recent[0], current[0]],
        base_sources=[system[1], catalog[1], recent[1], current[1]],
        budget_tokens=budget,
    )

    by_id = {item["source_id"]: item for item in prepared.entries}
    assert prepared.estimated_tokens <= budget
    assert by_id["system"]["included"] is True
    assert by_id["current"]["included"] is True
    assert by_id["recent"]["included"] is True
    assert by_id["catalog"]["included"] is False
    assert by_id["catalog"]["excluded_reason"] == "lower_priority_context_budget"


def test_actual_memory_is_removed_from_messages_when_it_loses_budget(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "context-memory-budget.db")
    MemoryStore(store).write_long_term("MEMORY-BUDGET-MARKER " * 500)
    builder = ContextBuilder(
        tmp_path,
        store,
        agent_revision=AgentRevision(
            revision_id="memory-budget:v1",
            agent_id="memory-budget",
            version=1,
            model_policy={"primary": "test/model"},
            memory_policy={
                "enabled": True,
                "mode": "personalized",
                "read_mode": "auto",
                "write_mode": "none",
                "layers": {"long_term": {"read": True, "write": False}},
            },
        ),
    )
    _messages, sources, candidates = builder.build_messages_with_candidates(
        history=[], current_message="current request"
    )
    required_budget = allocate_context(
        base_candidates=[item for item in candidates if item["required"]],
        base_sources=sources,
    ).estimated_tokens

    messages, budgeted_sources, _candidates = builder.build_messages_with_candidates(
        history=[],
        current_message="current request",
        max_context_tokens=required_budget,
    )

    memory = next(item for item in budgeted_sources if item["source_kind"] == "memory_long_term")
    assert memory["included"] is False
    assert memory["excluded_reason"] == "lower_priority_context_budget"
    assert "MEMORY-BUDGET-MARKER" not in str(messages)


def test_required_tool_result_is_compressed_with_auditable_hash() -> None:
    system = _candidate(
        "system", "system contract", target="system", priority=100, required=True, order=0
    )
    current = _candidate(
        "current",
        {"role": "user", "content": "current request"},
        target="message",
        priority=100,
        required=True,
        order=100,
    )
    original_tool_message = {
        "role": "tool",
        "tool_call_id": "call-1",
        "name": "large_result",
        "content": "large evidence " * 4_000,
    }
    dynamic = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "large_result", "arguments": "{}"},
                }
            ],
        },
        original_tool_message,
        {"role": "user", "content": "Summarize the result."},
    ]

    prepared = allocate_context(
        base_candidates=[system[0], current[0]],
        base_sources=[system[1], current[1]],
        dynamic_messages=dynamic,
        budget_tokens=800,
    )

    compressed = next(item for item in prepared.messages if item.get("role") == "tool")
    tool_entry = next(item for item in prepared.entries if item["source_kind"] == "tool_result")
    assert prepared.estimated_tokens <= 800
    assert "compressed for context budget" in compressed["content"]
    assert tool_entry["content_hash"] == stable_hash(original_tool_message)
    assert tool_entry["metadata"]["compression"]["method"] == "head_tail_v1"
    assert tool_entry["included_reason"] == "compressed_for_context_budget"


class _CountingProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(api_key="test")
        self.calls = 0
        self.tools: list[dict[str, Any]] = []

    def get_default_model(self) -> str:
        return "test/context-budget"

    async def chat(self, tools: list[dict[str, Any]] | None = None, **_kwargs: Any) -> LLMResponse:
        self.calls += 1
        self.tools = list(tools or [])
        return LLMResponse(content="done", finish_reason="stop")


@pytest.mark.asyncio
async def test_required_context_overflow_fails_before_provider_call(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "context-required-overflow.db")
    provider = _CountingProvider()
    executor = NativeAgentExecutor(
        provider=provider,
        scratch_root=tmp_path,
        max_context_tokens=10,
        session_manager=RuntimeSessionManager(store),
    )
    runtime = NativeAgentRuntime(agent=executor, store=store)
    submitted = await runtime.submit_run(
        AgentOptions(
            prompt="this request cannot fit",
            user_id="budget-user",
            session_id="budget-overflow",
        )
    )

    failed = await runtime.wait(submitted.run_id, timeout=5)

    assert failed.status == "failed"
    assert failed.result["stop_reason"] == "budget_exceeded"
    assert "required model context exceeds budget" in failed.error["message"]
    assert provider.calls == 0
    assert store.list_context_manifests(submitted.run_id) == []
    await runtime.close()


@pytest.mark.asyncio
async def test_disallowed_tool_schema_is_not_admitted_to_model_context(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "context-tool-admission.db")
    store.create_runtime_run(
        run_id="context-tool-admission",
        user_id="budget-user",
        session_id="tool-admission",
        agent_id="default",
        kind="agent",
        prompt="do not expose denied tools",
        options={},
        initial_status="running",
    )
    provider = _CountingProvider()
    executor = NativeAgentExecutor(
        provider=provider,
        scratch_root=tmp_path,
        session_manager=RuntimeSessionManager(store),
    )
    tool = _LargeResultTool()
    register_tool_fixture(executor.capabilities, tool)
    context = RunContext(
        run_id="context-tool-admission",
        user_id="budget-user",
        agent_id="default",
        session_id="tool-admission",
        session_key="api:budget-user:default:tool-admission",
        channel="api",
        chat_id="tool-admission",
        trace_store=store,
        disallowed_tools=frozenset({"large_result"}),
    )

    result = await executor.process_direct(
        "do not expose denied tools",
        session_key=context.session_key,
        run_context=context,
    )

    assert result == "done"
    names = {str(dict(item.get("function") or {}).get("name")) for item in provider.tools}
    assert "large_result" not in names
    manifest = store.list_context_manifests(context.run_id)[0]
    assert all(item.source_id != "tool:large_result" for item in manifest.entries)
    await executor.close_tool_connectors()


class _LargeResultTool(Tool):
    name = "large_result"
    description = "Return a large deterministic result"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **_kwargs: Any) -> str:
        return "large tool evidence " * 12_000


class _ToolLoopProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(api_key="test")
        self.calls: list[list[dict[str, Any]]] = []

    def get_default_model(self) -> str:
        return "test/context-budget"

    async def chat(self, messages: list[dict[str, Any]], **_kwargs: Any) -> LLMResponse:
        self.calls.append(messages)
        if len(self.calls) == 1:
            return LLMResponse(
                content=None,
                finish_reason="tool_calls",
                tool_calls=[ToolCallRequest(id="large-call", name="large_result", arguments={})],
            )
        return LLMResponse(content="compressed result accepted", finish_reason="stop")


@pytest.mark.asyncio
async def test_second_turn_provider_receives_budgeted_tool_result(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "context-tool-compression.db")
    store.create_runtime_run(
        run_id="context-tool-compression",
        user_id="budget-user",
        session_id="tool-compression",
        agent_id="default",
        kind="agent",
        prompt="use the large result",
        options={},
        initial_status="running",
    )
    provider = _ToolLoopProvider()
    executor = NativeAgentExecutor(
        provider=provider,
        scratch_root=tmp_path,
        model="test/context-budget",
        max_iterations=2,
        max_context_tokens=15_000,
        session_manager=RuntimeSessionManager(store),
    )
    tool = _LargeResultTool()
    register_tool_fixture(executor.capabilities, tool)
    context = RunContext(
        run_id="context-tool-compression",
        user_id="budget-user",
        agent_id="default",
        session_id="tool-compression",
        session_key="api:budget-user:default:tool-compression",
        channel="api",
        chat_id="tool-compression",
        trace_store=store,
    )

    result = await executor.process_direct(
        "use the large result", session_key=context.session_key, run_context=context
    )

    assert result == "compressed result accepted"
    assert len(provider.calls) == 2
    tool_message = next(item for item in provider.calls[1] if item.get("role") == "tool")
    assert "compressed for context budget" in tool_message["content"]
    manifests = store.list_context_manifests(context.run_id)
    assert len(manifests) == 2
    assert manifests[1].estimated_tokens <= 15_000
    compressed_entry = next(
        item for item in manifests[1].entries if item.source_kind == "tool_result"
    )
    assert compressed_entry.metadata["compression"]["method"] == "head_tail_v1"
    await executor.close_tool_connectors()


def test_unavailable_skill_is_recorded_as_not_admitted(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "context-skill-admission.db")

    messages, sources = ContextBuilder(tmp_path, store).build_messages_with_sources(
        history=[],
        current_message="hello",
        skill_names=["missing-skill"],
    )

    denied = next(item for item in sources if item["source_id"] == "skill:missing-skill:unadmitted")
    assert denied["included"] is False
    assert denied["excluded_reason"] == "skill_not_admitted"
    assert "missing-skill" not in str(messages)
