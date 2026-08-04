"""ContextBuilder reads memory only from the shared runtime store."""

import pytest

from joyhousebot.agent.context import ContextBuilder
from joyhousebot.agent.memory import MemoryStore
from joyhousebot.domain.agents import AgentRevision
from tests.support.postgres_store import PostgresTestStore


@pytest.fixture
def durable_context(tmp_path):
    store = PostgresTestStore(tmp_path / "context.db")
    MemoryStore(store).write_long_term("Shared memory fact.")
    MemoryStore(store, "session_1").write_long_term("Session 1 only fact.")
    return tmp_path, store


def test_memory_scope_isolates_content(durable_context) -> None:
    _workspace, store = durable_context
    assert "Shared memory fact" in MemoryStore(store).get_memory_context()
    assert "Session 1 only" in MemoryStore(store, "session_1").get_memory_context()
    assert "Session 1 only" not in MemoryStore(store).get_memory_context()


def test_system_prompt_includes_shared_memory(durable_context) -> None:
    scratch_root, store = durable_context
    prompt = ContextBuilder(scratch_root, runtime_store=store).build_system_prompt(
        scope_key=None
    )
    assert "Shared memory fact" in prompt


def test_system_prompt_includes_scoped_memory(durable_context) -> None:
    scratch_root, store = durable_context
    prompt = ContextBuilder(scratch_root, runtime_store=store).build_system_prompt(
        scope_key="session_1"
    )
    assert "Session 1 only fact" in prompt


def test_agent_task_only_policy_does_not_inject_persistent_memory(durable_context) -> None:
    scratch_root, store = durable_context
    revision = AgentRevision(
        revision_id="search:v1",
        agent_id="search",
        version=1,
        model_policy={"primary": "test/model"},
        memory_policy={"enabled": False, "mode": "task_only", "write_mode": "none"},
    )
    prompt = ContextBuilder(
        scratch_root, runtime_store=store, agent_revision=revision
    ).build_system_prompt(scope_key=None)
    assert "Shared memory fact" not in prompt
    assert "personal memory is disabled" in prompt
