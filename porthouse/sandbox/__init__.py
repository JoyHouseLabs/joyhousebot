"""Fail-closed command policy and Docker execution backend."""

from porthouse.sandbox.command_policy import DEFAULT_DENY_PATTERNS, guard_shell_command
from porthouse.sandbox.docker_backend import (
    is_docker_available,
    run_in_container,
)

__all__ = [
    "DEFAULT_DENY_PATTERNS",
    "guard_shell_command",
    "is_docker_available",
    "run_in_container",
]
