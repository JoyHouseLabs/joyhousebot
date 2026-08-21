"""Pure Memory document classification shared by runtime and storage."""

from __future__ import annotations

import re

_DAILY_MEMORY_DOCUMENT = re.compile(r"(?:^|/)\d{4}-\d{2}-\d{2}\.md$")


def memory_layer_for_path(path: str) -> str:
    """Map one virtual Memory document path to its durable memory layer."""
    clean = str(path or "").replace("\\", "/").lstrip("/")
    name = clean.rsplit("/", 1)[-1]
    if name == "PROFILE.md":
        return "profile"
    if clean.startswith("agent/"):
        return "agent"
    if name in {"HISTORY.md", ".abstract"} or _DAILY_MEMORY_DOCUMENT.search(clean):
        return "episodic"
    return "long_term"
