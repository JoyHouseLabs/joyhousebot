"""Tests for memory compaction (L2->L1->L0 scheduled task)."""

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from joyhousebot.agent.memory import MemoryStore
from joyhousebot.agent.memory_compaction import run_memory_compaction


@pytest.fixture
def workspace_with_old_daily(tmp_path: Path) -> Path:
    """Create workspace with old daily log and MEMORY.md."""
    store = MemoryStore(tmp_path)
    store.ensure_memory_structure()
    old_date = (date.today() - timedelta(days=5)).isoformat()
    store.append_l2_daily(old_date, "[2026-02-20 10:00] Discussed API design. Decided to use REST.")
    store.write_long_term("- [P0] User prefers Python.")
    return tmp_path


@pytest.mark.asyncio
async def test_run_memory_compaction_updates_l0(workspace_with_old_daily: Path) -> None:
    """Compaction runs and refreshes L0 from MEMORY + L1."""
    provider = AsyncMock()
    provider.chat = AsyncMock(
        return_value=AsyncMock(
            content='## active topics\n- API design\n- Python\n\n## retrieval hints\n- REST, API\n\n## recency\n(last updated: 2026-02-25)'
        )
    )
    result = await run_memory_compaction(
        workspace_with_old_daily,
        provider,
        "test-model",
        older_than_days=2,
        max_daily_logs=5,
    )
    assert "ok" in result or "L0" in result
    store = MemoryStore(workspace_with_old_daily)
    l0 = store.read_l0_abstract()
    assert "active topics" in l0 or "recency" in l0


@pytest.mark.asyncio
async def test_run_memory_compaction_with_old_daily_logs(workspace_with_old_daily: Path) -> None:
    """When old daily logs exist and LLM returns valid JSON + L0 text, compaction completes and updates L0."""
    from unittest.mock import MagicMock
    resp1 = MagicMock()
    resp1.content = '{"insights": ["API design decided"], "lessons": ["Use REST for new APIs"]}'
    resp2 = MagicMock()
    resp2.content = '## active topics\n- API\n\n## retrieval hints\n- REST\n\n## recency\n(last updated: 2026-02-25)'
    provider = AsyncMock()
    provider.chat = AsyncMock(side_effect=[resp1, resp2])
    result = await run_memory_compaction(
        workspace_with_old_daily,
        provider,
        "test-model",
        older_than_days=2,
        max_daily_logs=5,
    )
    assert "ok" in result
    store = MemoryStore(workspace_with_old_daily)
    l0 = store.read_l0_abstract()
    assert "active topics" in l0 or "recency" in l0
