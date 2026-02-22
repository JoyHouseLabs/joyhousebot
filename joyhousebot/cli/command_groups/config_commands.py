"""Config/configure/models/agents command groups."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from joyhousebot.cli.services.protocol_service import ProtocolService
from joyhousebot.config.loader import (
    get_config_path,
    load_config,
    load_config_from_openclaw_file,
    save_config,
)

from joyhousebot.cli.shared.http_utils import (
    deep_get,
    deep_set,
    deep_unset,
    load_config_json,
    parse_value,
    save_config_json,
)


def register_config_commands(app: typer.Typer, console: Console) -> None:
    """Register config/configure/models/agents command groups."""
    protocol = ProtocolService()
    config_app = typer.Typer(help="Config helpers (get/set/unset)")
    app.add_typer(config_app, name="config")

    @config_app.command("get")
    def config_get(
        key: str = typer.Argument(..., help="Dotted key path, e.g. providers.openrouter.apiKey"),
    ) -> None:
        data = load_config_json()
        try:
            value = deep_get(data, key)
        except KeyError:
            console.print(f"[red]Key not found:[/red] {key}")
            raise typer.Exit(1)
        console.print(json.dumps(value, indent=2, ensure_ascii=False))

    @config_app.command("set")
    def config_set(
        key: str = typer.Argument(..., help="Dotted key path"),
        value: str = typer.Argument(..., help="JSON value or plain string"),
    ) -> None:
        data = load_config_json()
        deep_set(data, key, parse_value(value))
        path = save_config_json(data)
        load_config(path)
        console.print(f"[green]✓[/green] Set {key}")

    @config_app.command("unset")
    def config_unset(
        key: str = typer.Argument(..., help="Dotted key path"),
    ) -> None:
        data = load_config_json()
        if not deep_unset(data, key):
            console.print(f"[yellow]Key not found:[/yellow] {key}")
            raise typer.Exit(1)
        save_config_json(data)
        console.print(f"[green]✓[/green] Unset {key}")

    @config_app.command("migrate-from-openclaw")
    def config_migrate_from_openclaw(
        openclaw_dir: str = typer.Argument(
            None,
            help="OpenClaw 状态目录，内含 openclaw.json；默认 ~/.openclaw",
        ),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="仅校验并打印将要写入的配置摘要，不写入 joyhousebot 配置",
        ),
    ) -> None:
        """从 OpenClaw 配置目录读取 openclaw.json，迁移为 joyhousebot 配置并写入 ~/.joyhousebot/config.json。
        会将 OpenClaw 的 models.providers.*.baseUrl 映射到 joyhousebot 的 providers（如 zai -> zhipu.api_base）；
        迁移后请检查并补填各 provider 的 api_key（OpenClaw 的 API 密钥通常在 auth/credentials 中，需手动配置）。"""
        dir_path = (Path(openclaw_dir).expanduser().resolve() if (openclaw_dir and openclaw_dir.strip()) else Path.home() / ".openclaw")
        config_file = dir_path / "openclaw.json"
        if not config_file.exists():
            console.print(f"[red]未找到 OpenClaw 配置文件:[/red] {config_file}")
            console.print("请指定包含 openclaw.json 的目录，例如: joyhousebot config migrate-from-openclaw /Users/xxx/.openclaw")
            raise typer.Exit(1)
        try:
            cfg = load_config_from_openclaw_file(config_file)
        except (json.JSONDecodeError, ValueError, FileNotFoundError) as e:
            console.print(f"[red]解析 OpenClaw 配置失败:[/red] {e}")
            raise typer.Exit(1)
        dest = get_config_path()
        if dry_run:
            console.print(f"[dim]（dry-run）将写入: {dest}[/dim]")
            console.print("[bold]agents.defaults[/bold]:", cfg.agents.defaults.model_dump())
            if cfg.agents.agent_list:
                console.print("[bold]agents.list[/bold]:", [e.model_dump() for e in cfg.agents.agent_list[:5]], "..." if len(cfg.agents.agent_list) > 5 else "")
            if cfg.commands is not None:
                console.print("[bold]commands[/bold]:", cfg.commands.model_dump())
            if cfg.meta is not None:
                console.print("[bold]meta[/bold]:", cfg.meta.model_dump())
            if cfg.wizard is not None:
                console.print("[bold]wizard[/bold]:", cfg.wizard.model_dump())
            if cfg.env is not None:
                console.print("[bold]env[/bold]:", cfg.env.model_dump())
            console.print("[green]✓[/green] 校验通过，未写入文件。去掉 --dry-run 可执行迁移。")
            return
        save_config(cfg)
        console.print(f"[green]✓[/green] 已从 [cyan]{config_file}[/cyan] 迁移到 [cyan]{dest}[/cyan]")

    @app.command("configure")
    def configure() -> None:
        """Interactive prompt for credentials and gateway defaults."""
        cfg = load_config()
        console.print("[bold]Configure joyhousebot[/bold]\n")

        provider = typer.prompt(
            "Default provider (openrouter/anthropic/openai/custom)",
            default=(cfg.agents.defaults.provider or "openrouter"),
        ).strip()
        model = typer.prompt("Default model", default=cfg.agents.defaults.model).strip()
        host = typer.prompt("Gateway host", default=cfg.gateway.host).strip()
        port = typer.prompt("Gateway port", default=str(cfg.gateway.port)).strip()
        api_key = typer.prompt(f"{provider}.api_key (empty to keep current)", default="", show_default=False).strip()

        cfg.agents.defaults.provider = provider
        cfg.agents.defaults.model = model
        cfg.gateway.host = host
        cfg.gateway.port = int(port or cfg.gateway.port)

        provider_cfg = getattr(cfg.providers, provider, None)
        if provider_cfg is not None and api_key:
            provider_cfg.api_key = api_key

        save_config(cfg)
        console.print(f"[green]✓[/green] Saved {get_config_path()}")

    models_app = typer.Typer(help="Model configuration")
    app.add_typer(models_app, name="models")

    @models_app.command("list")
    def models_list() -> None:
        """List default and per-agent model settings."""
        cfg = load_config()
        table = Table(title="Configured Models")
        table.add_column("Scope", style="cyan")
        table.add_column("Provider")
        table.add_column("Model")
        table.add_column("Fallbacks")
        table.add_row(
            "defaults",
            cfg.agents.defaults.provider or "(auto)",
            cfg.agents.defaults.model,
            ", ".join(cfg.agents.defaults.model_fallbacks or []) or "-",
        )
        for entry in cfg.agents.agent_list or []:
            table.add_row(
                f"agent:{entry.id}",
                entry.provider or "(auto)",
                entry.model,
                ", ".join(entry.model_fallbacks or []) or "-",
            )
        console.print(table)

    agents_app = typer.Typer(help="Manage isolated agents metadata")
    app.add_typer(agents_app, name="agents")

    @agents_app.command("list")
    def agents_list() -> None:
        """List configured agents via gateway RPC."""
        payload = protocol.call("agents.list", {})
        agents = payload.get("agents") if isinstance(payload, dict) else []
        default_id = str(payload.get("defaultAgentId") or payload.get("default_id") or "")
        if not isinstance(agents, list):
            agents = []
        table = Table(title="Agents")
        table.add_column("ID", style="cyan")
        table.add_column("Name")
        table.add_column("Workspace")
        table.add_column("Model")
        table.add_column("Default")
        table.add_column("Activated")
        for entry in agents:
            if not isinstance(entry, dict):
                continue
            table.add_row(
                str(entry.get("id") or ""),
                str(entry.get("name") or "-"),
                str(Path(str(entry.get("workspace") or "")).expanduser()),
                str(entry.get("model") or ""),
                "yes" if str(entry.get("id") or "") == default_id else "",
                "yes" if bool(entry.get("activated", True)) else "no",
            )
        console.print(table)

    @agents_app.command("set-default")
    def agents_set_default(
        agent_id: str = typer.Argument(..., help="Agent ID to set as default"),
    ) -> None:
        """Set the default agent id via gateway RPC."""
        payload = protocol.call("agents.list", {})
        agents = payload.get("agents") if isinstance(payload, dict) else []
        if not isinstance(agents, list):
            agents = []
        exists = any(isinstance(e, dict) and str(e.get("id") or "") == agent_id for e in agents)
        if not exists:
            console.print(f"[red]Agent not found:[/red] {agent_id}")
            raise typer.Exit(1)
        protocol.call("config.patch", {"agents": {"default_id": agent_id}})
        console.print(f"[green]✓[/green] Default agent set to {agent_id}")

