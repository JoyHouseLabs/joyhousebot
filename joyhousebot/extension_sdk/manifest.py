"""Helpers for immutable extension release identity."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path


def source_tree_digest(module_file: str) -> str:
    """Hash the installed Python package tree containing ``module_file``.

    This is deterministic for an unpacked wheel and local editable install.
    Release automation may replace it with a signed wheel digest later, but an
    empty or mutable identity is never accepted by Core.
    """
    root = Path(module_file).resolve().parent
    digest = sha256()
    files = sorted(path for path in root.rglob("*.py") if path.is_file())
    if not files:
        raise ValueError(f"extension package has no Python sources: {root}")
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"


__all__ = ["source_tree_digest"]
