"""Static boundary checks for plugin layering."""

from __future__ import annotations

import ast
from pathlib import Path

_FORBIDDEN_IMPORT_PREFIXES = (
    "joyhousebot.plugins.bridge",
    "joyhousebot.plugins.native",
)


def collect_plugin_boundary_violations(package_root: Path) -> list[str]:
    """Return violations where non-plugin modules import bridge/native internals."""
    root = package_root.resolve()
    violations: list[str] = []
    for file_path in root.rglob("*.py"):
        rel_path = file_path.resolve().relative_to(root).as_posix()
        # Plugin package itself can import bridge/native internals by design.
        if rel_path.startswith("plugins/"):
            continue
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        except Exception as exc:
            violations.append(f"{rel_path}:0 parse-error: {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = str(alias.name or "")
                    if name.startswith(_FORBIDDEN_IMPORT_PREFIXES):
                        violations.append(f"{rel_path}:{node.lineno} forbidden-import: {name}")
            elif isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                if module.startswith(_FORBIDDEN_IMPORT_PREFIXES):
                    violations.append(f"{rel_path}:{node.lineno} forbidden-import-from: {module}")
                # Relative form like `from ..plugins.bridge import ...`
                if node.level > 0 and ("plugins.bridge" in module or "plugins.native" in module):
                    violations.append(f"{rel_path}:{node.lineno} forbidden-relative-import-from: {module}")
    return violations

