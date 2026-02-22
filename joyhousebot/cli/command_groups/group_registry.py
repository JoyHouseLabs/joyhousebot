"""Registry for grouped CLI command modules."""

from __future__ import annotations

import typer
from rich.console import Console

from .comms_commands import register_comms_commands
from .config_commands import register_config_commands
from .protocol_commands import register_protocol_commands
from .runtime_commands import register_runtime_commands


def register_command_groups(app: typer.Typer, console: Console, gateway_command) -> None:
    """Attach grouped command modules to the main app."""
    register_config_commands(app=app, console=console)
    register_runtime_commands(app=app, console=console, gateway_command=gateway_command)
    register_comms_commands(app=app, console=console)
    register_protocol_commands(app=app, console=console, gateway_command=gateway_command)

