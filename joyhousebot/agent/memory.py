"""Memory system for persistent agent memory.

在整体架构中：MemoryStore 提供双层记忆（MEMORY.md + HISTORY.md）及 L0/L1/L2/archive 扩展，
供 ContextBuilder 与 AgentLoop 使用。

Layering (L0/L1/L2) for pluggable memory:
- L0: memory/.abstract (directory index, ~100–300 tokens, routing entry).
- L1: memory/insights/*.md, memory/lessons/ (summaries, operational lessons).
- L2: memory/YYYY-MM-DD.md (daily logs), HISTORY.md (legacy append log).
- Long-term: MEMORY.md (facts with optional P0/P1/P2 tags).
- archive/: expired P1/P2 moved here by janitor.
"""

from pathlib import Path

from joyhousebot.utils.helpers import ensure_dir


L0_ABSTRACT_FILENAME = ".abstract"
INSIGHTS_DIR = "insights"
LESSONS_DIR = "lessons"
ARCHIVE_DIR = "archive"


class MemoryStore:
    """Two-layer memory: MEMORY.md (long-term facts) + HISTORY.md (grep-searchable log).
    Extensions: L0 (.abstract), L2 daily logs (YYYY-MM-DD.md), insights/lessons/archive structure.
    """

    def __init__(self, workspace: Path):
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "HISTORY.md"
        self._l0_file = self.memory_dir / L0_ABSTRACT_FILENAME

    def ensure_memory_structure(self) -> None:
        """Create L0/L1/L2/archive directories and empty .abstract if missing."""
        ensure_dir(self.memory_dir / INSIGHTS_DIR)
        ensure_dir(self.memory_dir / LESSONS_DIR)
        ensure_dir(self.memory_dir / ARCHIVE_DIR)
        if not self._l0_file.exists():
            self._l0_file.write_text("# memory index\n\n## active topics\n(none)\n\n## retrieval hints\n(none)\n\n## recency\n(last updated: —)\n", encoding="utf-8")

    def read_long_term(self) -> str:
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    def write_long_term(self, content: str, updated_at: str | None = None) -> None:
        """Write long-term memory. If updated_at (e.g. ISO8601) is given, prepend a comment line for traceability."""
        if updated_at:
            content = f"<!-- updated_at={updated_at} -->\n{content}"
        self.memory_file.write_text(content, encoding="utf-8")

    def append_history(self, entry: str) -> None:
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")

    def read_l0_abstract(self) -> str:
        """Read L0 directory index (routing entry for retrieval)."""
        if self._l0_file.exists():
            return self._l0_file.read_text(encoding="utf-8")
        return ""

    def update_l0_abstract(self, content: str) -> None:
        """Write L0 directory index."""
        self._l0_file.write_text(content, encoding="utf-8")

    def get_l2_path(self, date_str: str) -> Path:
        """Return path for L2 daily log file (date_str e.g. YYYY-MM-DD)."""
        return self.memory_dir / f"{date_str}.md"

    def append_l2_daily(self, date_str: str, content: str) -> None:
        """Append content to L2 daily log (memory/YYYY-MM-DD.md)."""
        path = self.get_l2_path(date_str)
        with open(path, "a", encoding="utf-8") as f:
            f.write(content.rstrip() + "\n\n")

    def get_memory_context(self) -> str:
        long_term = self.read_long_term()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    def get_memory_context_with_l0(self, max_l0_chars: int = 1500) -> str:
        """Build memory context: optional L0 abstract first (for routing), then long-term.
        If L0 is longer than max_l0_chars it is truncated to avoid blowing context.
        """
        parts = []
        l0 = self.read_l0_abstract().strip()
        if l0:
            if len(l0) > max_l0_chars:
                l0 = l0[:max_l0_chars] + "\n...(truncated)"
            parts.append(f"## Memory index (L0)\n{l0}")
        long_term = self.read_long_term()
        if long_term:
            parts.append(f"## Long-term Memory\n{long_term}")
        return "\n\n".join(parts) if parts else ""
