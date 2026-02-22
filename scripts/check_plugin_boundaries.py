"""Check plugin layering boundaries for the joyhousebot package."""

from __future__ import annotations

import sys
from pathlib import Path

from joyhousebot.plugins.architecture_guard import collect_plugin_boundary_violations


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    package_root = repo_root / "joyhousebot"
    violations = collect_plugin_boundary_violations(package_root=package_root)
    if not violations:
        print("plugin-boundary-check: ok")
        return 0
    print("plugin-boundary-check: violations detected")
    for row in violations:
        print(f"- {row}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

