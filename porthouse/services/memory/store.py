"""Durable scoped Agent memory backed by the shared PostgreSQL repository set."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from porthouse.services.memory.repository import MemoryRepository

L0_ABSTRACT_FILENAME = ".abstract"


class MemoryStore:
    """Document-oriented memory facade over the normalized shared repository."""

    def __init__(self, runtime_store: Any, scope_key: str | None = None) -> None:
        if runtime_store is None:
            raise ValueError("MemoryStore requires a durable runtime_store")
        self.scope_key = scope_key or "shared"
        self.runtime_store = runtime_store
        repository = getattr(runtime_store, "_memory_repository", None)
        if repository is None:
            repository = MemoryRepository(runtime_store)
            runtime_store._memory_repository = repository
        self.repository: MemoryRepository = repository

    def _read_document(self, relative_path: str) -> str:
        return self.repository.read(self.scope_key, relative_path)

    def _write_document(self, relative_path: str, content: str) -> None:
        self.repository.write(self.scope_key, relative_path, content)

    def _append_document(self, relative_path: str, content: str) -> None:
        self.repository.append(self.scope_key, relative_path, content.rstrip() + "\n\n")

    @staticmethod
    def _clean_path(relative_path: str) -> str:
        clean = str(relative_path or "").strip().replace("\\", "/").lstrip("/")
        if not clean or any(part in {"", ".", ".."} for part in clean.split("/")):
            return ""
        return clean

    def read_relative(self, relative_path: str) -> str:
        clean = self._clean_path(relative_path)
        return self._read_document(clean) if clean else ""

    def write_relative(self, relative_path: str, content: str) -> bool:
        clean = self._clean_path(relative_path)
        if not clean:
            return False
        self._write_document(clean, content)
        return True

    def list_relative(self, relative_path: str = "") -> list[tuple[str, bool]]:
        """List one virtual directory as name/directory rows."""
        prefix = self._clean_path(relative_path) if relative_path else ""
        prefix = f"{prefix}/" if prefix else ""
        items: dict[str, bool] = {}
        for document_path in self.repository.list_documents(self.scope_key):
            if not document_path.startswith(prefix):
                continue
            remainder = document_path[len(prefix) :]
            if not remainder:
                continue
            head, separator, _tail = remainder.partition("/")
            items[head] = items.get(head, False) or bool(separator)
        return sorted(items.items())

    def ensure_memory_structure(self) -> None:
        if not self._read_document(L0_ABSTRACT_FILENAME):
            self._write_document(
                L0_ABSTRACT_FILENAME,
                "# memory index\n\n## active topics\n(none)\n\n"
                "## retrieval hints\n(none)\n\n## recency\n(last updated: —)\n",
            )

    def read_long_term(self) -> str:
        return self._read_document("MEMORY.md")

    def write_long_term(self, content: str, updated_at: str | None = None) -> None:
        if updated_at:
            content = f"<!-- updated_at={updated_at} -->\n{content}"
        self._write_document("MEMORY.md", content)

    def read_profile(self) -> str:
        """Read durable personal attributes, kept separate from long-term facts."""
        return self._read_document("PROFILE.md")

    def write_profile(self, content: str, updated_at: str | None = None) -> None:
        if updated_at:
            content = f"<!-- updated_at={updated_at} -->\n{content}"
        self._write_document("PROFILE.md", content)

    def append_history(self, entry: str, max_entries: int = 0) -> None:
        self._append_document("HISTORY.md", entry)
        if max_entries > 0:
            self._trim_history_to_last_n(max_entries)

    def _trim_history_to_last_n(self, count: int) -> None:
        if count <= 0:
            return
        blocks = [
            block.strip()
            for block in self._read_document("HISTORY.md").split("\n\n")
            if block.strip()
        ]
        if len(blocks) > count:
            self._write_document("HISTORY.md", "\n\n".join(blocks[-count:]) + "\n")

    def read_l0_abstract(self) -> str:
        return self._read_document(L0_ABSTRACT_FILENAME)

    def update_l0_abstract(self, content: str) -> None:
        self._write_document(L0_ABSTRACT_FILENAME, content)

    def append_l2_daily(self, date_str: str, content: str) -> None:
        self._append_document(f"{date_str}.md", content)

    def get_memory_context(self) -> str:
        parts: list[str] = []
        profile = self.read_profile()
        long_term = self.read_long_term()
        if profile:
            parts.append(f"## User Profile\n{profile}")
        if long_term:
            parts.append(f"## Long-term Memory\n{long_term}")
        return "\n\n".join(parts)

    def read_daily_logs_today_yesterday(self) -> str:
        parts: list[str] = []
        for delta in (0, 1):
            path = f"{(date.today() - timedelta(days=delta)).isoformat()}.md"
            content = self._read_document(path).strip()
            if content:
                parts.append(content)
        return "\n\n".join(parts)
