"""Provider-neutral command guard used before every sandbox execution."""

from __future__ import annotations

import re
from pathlib import Path

DEFAULT_DENY_PATTERNS = (
    r"\brm\s+-[rf]{1,2}\b",
    r"\bdel\s+/[fq]\b",
    r"\brmdir\s+/s\b",
    r"\b(format|mkfs|diskpart)\b",
    r"\bdd\s+if=",
    r">\s*/dev/sd",
    r"\b(shutdown|reboot|poweroff)\b",
    r":\(\)\s*\{.*\};\s*:",
)
_SHELL_METACHARACTERS = re.compile(r"[|&;<>()`$\n\r]")


def guard_shell_command(
    command: str,
    cwd: str,
    *,
    working_dir: str | None,
    restrict_to_workspace: bool = True,
    shell_mode: bool = False,
    deny_patterns: list[str] | tuple[str, ...] | None = None,
    allow_patterns: list[str] | tuple[str, ...] | None = None,
) -> str | None:
    """Return a rejection reason, or ``None`` when the command may enter the sandbox."""
    value = str(command or "").strip()
    lower = value.lower()
    for pattern in deny_patterns or DEFAULT_DENY_PATTERNS:
        if re.search(pattern, lower):
            return "Command blocked by safety guard (dangerous pattern detected)"
    allowed = tuple(allow_patterns or ())
    if allowed and not any(re.search(pattern, lower) for pattern in allowed):
        return "Command blocked by safety guard (not in allowlist)"
    if restrict_to_workspace and not shell_mode and _SHELL_METACHARACTERS.search(value):
        return "Command blocked by safety guard (shell metacharacters are not allowed)"
    if not restrict_to_workspace:
        return None
    if "..\\" in value or "../" in value:
        return "Command blocked by safety guard (path traversal detected)"

    cwd_path = Path(cwd).expanduser().resolve()
    allowed_root = Path(working_dir).expanduser().resolve() if working_dir else cwd_path
    try:
        cwd_path.relative_to(allowed_root)
    except ValueError:
        return "Command blocked by safety guard (working_dir outside allowed root)"

    if re.findall(r"[A-Za-z]:\\[^\\\"']+", value):
        return (
            "Command blocked by safety guard (Windows paths are not visible "
            "inside the Linux execution container)"
        )
    for raw in re.findall(r"(?:^|[\s|>])(/[^\s\"'>]+)", value):
        path = raw.strip().rstrip("/")
        if path and not any(
            path == root or path.startswith(f"{root}/")
            for root in ("/workspace", "/tmp")
        ):
            return (
                "Command blocked by safety guard (absolute path outside /workspace "
                "is not visible inside the execution container)"
            )
    return None


__all__ = ["DEFAULT_DENY_PATTERNS", "guard_shell_command"]
