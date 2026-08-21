"""Fail-closed sandbox facade for trusted capability extensions."""

from joyhousebot.sandbox.command_policy import (
    DEFAULT_DENY_PATTERNS,
    guard_shell_command,
)
from joyhousebot.sandbox.docker_backend import (
    is_docker_available as is_sandbox_available,
)
from joyhousebot.sandbox.docker_backend import run_in_container as run_in_sandbox

__all__ = [
    "DEFAULT_DENY_PATTERNS",
    "guard_shell_command",
    "is_sandbox_available",
    "run_in_sandbox",
]
