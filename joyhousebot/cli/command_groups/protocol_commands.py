"""Protocol-compatible command group entrypoint."""

from __future__ import annotations

import typer
from rich.console import Console

from .protocol_commands_impl import register_protocol_commands_impl


def register_protocol_commands(
    app: typer.Typer,
    console: Console,
    gateway_command,
) -> None:
    """Register protocol-compatible command groups."""
    register_protocol_commands_impl(
        app=app,
        console=console,
        gateway_command=gateway_command,
    )

