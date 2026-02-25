"""Builtin memory search: grep-style search over memory/*.md, MEMORY.md, HISTORY.md, .abstract."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from joyhousebot.agent.memory import safe_scope_key

MEMORY_REL = "memory"
SNIPPET_MAX_CHARS = 700
CONTEXT_LINES = 2


def search_memory_files(
    workspace: Path,
    query: str,
    top_k: int = 10,
    scope_key: str | None = None,
) -> list[dict[str, Any]]:
    """
    Search memory directory: MEMORY.md, HISTORY.md, .abstract, and memory/YYYY-MM-DD.md.
    When scope_key is set, search under memory/<safe_scope_key>/ (per-session/per-user).
    Returns hits with content, file_path, trace (compatible with retrieve tool).
    """
    query = (query or "").strip()
    if not query:
        return []

    base = workspace / MEMORY_REL
    safe = safe_scope_key(scope_key) if scope_key else ""
    memory_dir = (base / safe) if safe else base
    if not memory_dir.is_dir():
        return []

    rel_prefix = f"{MEMORY_REL}/{safe}/" if safe else f"{MEMORY_REL}/"
    # Files to search (workspace-relative path -> Path)
    candidates: list[tuple[str, Path]] = []
    if (memory_dir / "MEMORY.md").exists():
        candidates.append((f"{rel_prefix}MEMORY.md", memory_dir / "MEMORY.md"))
    if (memory_dir / "HISTORY.md").exists():
        candidates.append((f"{rel_prefix}HISTORY.md", memory_dir / "HISTORY.md"))
    abstract_file = memory_dir / ".abstract"
    if abstract_file.exists():
        candidates.append((f"{rel_prefix}.abstract", abstract_file))
    for p in memory_dir.glob("*.md"):
        if p.name not in ("MEMORY.md", "HISTORY.md"):
            candidates.append((f"{rel_prefix}{p.name}", p))
    for sub in ("insights", "lessons"):
        subdir = memory_dir / sub
        if subdir.is_dir():
            for p in subdir.glob("*.md"):
                candidates.append((f"{rel_prefix}{sub}/{p.name}", p))

    pattern = re.compile(re.escape(query), re.IGNORECASE)
    hits: list[dict[str, Any]] = []

    for rel_path, path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not pattern.search(line):
                continue
            start = max(0, i - CONTEXT_LINES)
            end = min(len(lines), i + CONTEXT_LINES + 1)
            snippet_lines = lines[start:end]
            content = "\n".join(snippet_lines)
            if len(content) > SNIPPET_MAX_CHARS:
                content = content[: SNIPPET_MAX_CHARS - 3] + "..."
            hits.append({
                "doc_id": rel_path,
                "source_type": "memory",
                "source_url": "",
                "file_path": rel_path,
                "title": path.name,
                "chunk_index": i,
                "page": None,
                "content": content,
                "trace": {"doc_id": rel_path, "source": rel_path, "page": None},
            })
            if len(hits) >= top_k:
                return hits[:top_k]

    return hits[:top_k]
