"""Tests for memory janitor: P1/P2 expiry and archive (dry-run and run)."""

from datetime import date

import pytest
from pathlib import Path

from joyhousebot.agent.memory import MemoryStore
from joyhousebot.agent.memory_janitor import run_janitor, P_EXPIRE_PATTERN


def test_janitor_dry_run_returns_actions_without_writing(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.ensure_memory_structure()
    store.write_long_term("""<!-- updated_at=2026-01-01T00:00:00Z -->
- [P0] Permanent fact.
- [P1|expire:2020-01-01] Old project (expired).
- [P2|expire:2025-01-01] Old temp (expired).
""")
    today = date(2026, 2, 22)
    actions = run_janitor(tmp_path, dry_run=True, today=today)
    assert len(actions) == 2
    assert all(a["expire"] in ("2020-01-01", "2025-01-01") for a in actions)
    assert all(not a["archived"] for a in actions)
    # MEMORY.md unchanged
    raw = store.read_long_term()
    assert "Old project" in raw
    assert "Old temp" in raw


def test_janitor_run_archives_expired_and_removes_from_memory(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.ensure_memory_structure()
    store.write_long_term("""<!-- updated_at=2026-01-01T00:00:00Z -->
- [P0] Permanent fact.
- [P1|expire:2020-01-01] Old project (expired).
""")
    today = date(2026, 2, 22)
    actions = run_janitor(tmp_path, dry_run=False, today=today)
    assert len(actions) == 1
    assert actions[0]["archived"] is True
    archive_dir = store.memory_dir / "archive"
    assert archive_dir.is_dir()
    archive_file = archive_dir / "expired-2026-02-22.md"
    assert archive_file.is_file()
    assert "Old project" in archive_file.read_text()
    raw = store.read_long_term()
    assert "Old project" not in raw
    assert "[P0] Permanent fact" in raw


def test_expire_pattern_matches() -> None:
    assert P_EXPIRE_PATTERN.search("- [P1|expire:2026-05-01] Some text")
    assert P_EXPIRE_PATTERN.search("[P2|expire:2025-01-15]")
    assert not P_EXPIRE_PATTERN.search("- [P0] Permanent")
    assert not P_EXPIRE_PATTERN.search("- [P1] No date")
