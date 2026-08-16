"""Tests for durable scoped Agent memory."""

from datetime import date, timedelta

import pytest

from porthouse.services.memory.store import L0_ABSTRACT_FILENAME, MemoryStore
from tests.support.postgres_store import PostgresTestStore


@pytest.fixture
def runtime_store(tmp_path):
    return PostgresTestStore(tmp_path / "memory.db")


def test_ensure_memory_structure_creates_abstract(runtime_store) -> None:
    memory = MemoryStore(runtime_store)
    memory.ensure_memory_structure()
    assert "memory index" in memory.read_l0_abstract()
    assert (L0_ABSTRACT_FILENAME, False) in memory.list_relative()


def test_read_l0_abstract_until_updated(runtime_store) -> None:
    memory = MemoryStore(runtime_store)
    memory.ensure_memory_structure()
    memory.update_l0_abstract("# custom index\n\n## topics\n- foo")
    assert "foo" in memory.read_l0_abstract()


def test_append_daily_log(runtime_store) -> None:
    memory = MemoryStore(runtime_store)
    memory.append_l2_daily("2026-02-22", "Event: deployed v2.")
    memory.append_l2_daily("2026-02-22", "Event: user asked about X.")
    text = memory.read_relative("2026-02-22.md")
    assert "deployed v2" in text and "user asked about X" in text


def test_append_history_trims_atomically(runtime_store) -> None:
    memory = MemoryStore(runtime_store)
    for index in range(6):
        memory.append_history(f"Entry {index + 1}.", max_entries=3 if index == 5 else 0)
    content = memory.read_relative("HISTORY.md")
    assert "Entry 4" in content and "Entry 6" in content
    assert "Entry 1" not in content


def test_daily_logs_today_and_yesterday(runtime_store) -> None:
    memory = MemoryStore(runtime_store)
    memory.append_l2_daily(date.today().isoformat(), "Today")
    memory.append_l2_daily((date.today() - timedelta(days=1)).isoformat(), "Yesterday")
    assert memory.read_daily_logs_today_yesterday() == "Today\n\nYesterday"


def test_scopes_are_isolated(runtime_store) -> None:
    first = MemoryStore(runtime_store, "user:a:agent:default")
    second = MemoryStore(runtime_store, "user:b:agent:default")
    first.write_long_term("private-a")
    assert first.read_long_term() == "private-a"
    assert second.read_long_term() == ""


def test_write_long_term_with_timestamp(runtime_store) -> None:
    memory = MemoryStore(runtime_store)
    memory.write_long_term("Fact", updated_at="2026-02-25T12:00:00Z")
    assert "updated_at=2026-02-25" in memory.read_long_term()


def test_virtual_directory_listing(runtime_store) -> None:
    memory = MemoryStore(runtime_store)
    memory.write_relative("insights/one.md", "one")
    memory.write_relative("MEMORY.md", "root")
    assert memory.list_relative() == [("MEMORY.md", False), ("insights", True)]
    assert memory.list_relative("insights") == [("one.md", False)]
