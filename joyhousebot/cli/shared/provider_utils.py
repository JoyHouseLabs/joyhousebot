"""Shared provider creation helpers for CLI commands."""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console


def make_provider(config: Any, console: Console):
    """Create LiteLLMProvider from config. Exits if no API key found."""
    from joyhousebot.providers.litellm_provider import LiteLLMProvider, _mask_api_key

    p = config.get_provider()
    model, _ = config.get_agent_model_and_fallbacks(None)
    if not (p and p.api_key) and not model.startswith("bedrock/"):
        console.print("[red]Error: No API key configured.[/red]")
        console.print("Set one in ~/.joyhousebot/config.json under providers section")
        raise typer.Exit(1)
    provider_name = config.get_provider_name() or "unknown"
    console.print(
        f"[dim]Provider: {provider_name} | api_key: {_mask_api_key(p.api_key if p else None)} | model: {model}[/dim]"
    )
    return LiteLLMProvider(
        api_key=p.api_key if p else None,
        api_base=config.get_api_base(),
        default_model=model,
        extra_headers=p.extra_headers if p else None,
        provider_name=config.get_provider_name(),
    )
