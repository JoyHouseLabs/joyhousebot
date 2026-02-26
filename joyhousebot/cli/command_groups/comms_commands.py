"""message/sessions/memory command groups."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from joyhousebot.cli.services.protocol_service import ProtocolService
from joyhousebot.cli.shared.http_utils import get_gateway_base_url, get_http_api_headers, http_json
from joyhousebot.config.loader import load_config
from joyhousebot.utils.helpers import get_workspace_path


def _get_agent_workspace(cfg: Any, agent_id: str) -> Path:
    """
    Get workspace path for a specific agent ID.
    
    Args:
        cfg: Configuration object.
        agent_id: Agent ID to look up.
    
    Returns:
        Path to the agent's workspace directory.
    
    Raises:
        ValueError: If agent_id is not found.
    """
    if not agent_id:
        raise ValueError("agent_id cannot be empty")
    
    agent_id = agent_id.strip()
    for agent in cfg.agents.agent_list:
        if agent.id == agent_id:
            return Path(agent.workspace).expanduser()
    
    available = ", ".join([a.id for a in cfg.agents.agent_list])
    raise ValueError(f"Agent '{agent_id}' not found. Available agents: {available}")


def register_comms_commands(app: typer.Typer, console: Console) -> None:
    """Register message/sessions/memory command groups."""
    protocol = ProtocolService()
    message_app = typer.Typer(help="Send messages and channel actions")
    app.add_typer(message_app, name="message")

    @message_app.command("send")
    def message_send(
        message: str = typer.Option(..., "--message", "-m", help="Message body"),
        session: str = typer.Option("cli:message", "--session", "-s", help="Session key"),
        agent_id: str = typer.Option("", "--agent-id", help="Optional agent id"),
        channel: str = typer.Option("", "--channel", help="Direct outbound channel, e.g. telegram/whatsapp"),
        target: str = typer.Option("", "--target", help="Direct outbound target/chat id"),
        reply_to: str = typer.Option("", "--reply-to", help="Optional reply target/message id"),
    ) -> None:
        """Send a message through gateway chat API or direct outbound API."""
        base = get_gateway_base_url()
        if bool(channel) ^ bool(target):
            raise typer.BadParameter("--channel and --target must be provided together")
        if channel and target:
            payload = {
                "channel": channel,
                "target": target,
                "message": message,
                "reply_to": reply_to or None,
            }
            api_headers = get_http_api_headers()
            try:
                response = http_json(
                    "POST", f"{base}/api/message/send", payload=payload, timeout=15.0, headers=api_headers
                )
            except RuntimeError as exc:
                if "404" in str(exc):
                    console.print("[yellow]Gateway does not support /message/send yet, fallback to /chat.[/yellow]")
                    payload = {"message": message, "session_id": session}
                    if agent_id:
                        payload["agent_id"] = agent_id
                    response = http_json(
                        "POST", f"{base}/api/chat", payload=payload, timeout=30.0, headers=api_headers
                    )
                else:
                    raise
        else:
            payload = {"message": message, "session_id": session}
            if agent_id:
                payload["agent_id"] = agent_id
            response = http_json(
                "POST", f"{base}/api/chat", payload=payload, timeout=30.0, headers=get_http_api_headers()
            )
        console.print(json.dumps(response, indent=2, ensure_ascii=False))

    sessions_app = typer.Typer(help="List stored conversation sessions")
    app.add_typer(sessions_app, name="sessions")

    @sessions_app.command("list")
    def sessions_list() -> None:
        """List session metadata via gateway RPC."""
        payload = protocol.call("sessions.list", {})
        rows = payload.get("sessions") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            rows = []
        table = Table(title="Sessions")
        table.add_column("Key", style="cyan")
        table.add_column("Updated")
        table.add_column("Path")
        for row in rows:
            table.add_row(str(row.get("key", "")), str(row.get("updated_at", "")), str(row.get("path", "")))
        console.print(table if rows else "No sessions.")

    @sessions_app.command("history")
    def sessions_history(
        key: str = typer.Argument(..., help="Session key"),
        limit: int = typer.Option(20, "--limit", help="Last N messages"),
    ) -> None:
        """Print last messages in a session via gateway RPC."""
        payload = protocol.call("sessions.preview", {"key": key, "limit": max(1, limit)})
        messages = payload.get("messages") if isinstance(payload, dict) else []
        if not isinstance(messages, list):
            messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            console.print(f"[cyan]{role}[/cyan]: {content}")

    @sessions_app.command("delete")
    def sessions_delete(
        key: str = typer.Argument(..., help="Session key"),
    ) -> None:
        """Delete a session via gateway RPC."""
        payload = protocol.call("sessions.delete", {"key": key})
        ok = bool(payload.get("deleted")) if isinstance(payload, dict) else False
        if not ok:
            console.print(f"[yellow]Session not found:[/yellow] {key}")
            raise typer.Exit(1)
        console.print(f"[green]✓[/green] Deleted session: {key}")

    memory_app = typer.Typer(help="Memory search tools")
    app.add_typer(memory_app, name="memory")

    @memory_app.command("search")
    def memory_search(
        keyword: str = typer.Argument(..., help="Keyword or phrase to search in memory files"),
        agent_id: str = typer.Option("", "--agent-id", help="Agent ID to search in (default agent if not specified)"),
        workspace: str = typer.Option("", "--workspace", help="Override workspace path (takes precedence over --agent-id)"),
        limit: int = typer.Option(20, "--limit", help="Max results to show"),
        scope_key: str = typer.Option("", "--scope-key", help="Optional memory scope key (per-session/per-user isolation)"),
    ) -> None:
        """Search memory files (MEMORY.md, HISTORY.md, .abstract, and memory/*.md)."""
        if workspace:
            ws = Path(workspace).expanduser()
        elif agent_id:
            cfg = load_config()
            ws = _get_agent_workspace(cfg, agent_id)
        else:
            cfg = load_config()
            ws = get_workspace_path(cfg.agents.defaults.workspace)
        
        memory_dir = ws / "memory"
        if not memory_dir.is_dir():
            console.print(f"[yellow]Memory directory not found:[/yellow] {memory_dir}")
            raise typer.Exit(1)
        
        from joyhousebot.services.retrieval.memory_search import search_memory_files
        
        hits = search_memory_files(
            workspace=ws,
            query=keyword,
            top_k=max(1, limit),
            scope_key=scope_key or None,
        )
        
        if not hits:
            console.print("No matches.")
            return
        
        console.print(f"[green]Found {len(hits)} match(es):[/green]\n")
        for hit in hits:
            file_path = hit.get("file_path", "")
            chunk_index = hit.get("chunk_index", 0)
            content = hit.get("content", "")
            console.print(f"[cyan bold]{file_path}[/cyan bold][dim]:{chunk_index}[/dim]")
            console.print(f"[dim]{content}[/dim]")
            console.print()

    @memory_app.command("janitor")
    def memory_janitor_cmd(
        dry_run: bool = typer.Option(True, "--dry-run/--run", help="Dry-run (default) or execute archive"),
        agent_id: str = typer.Option("", "--agent-id", help="Agent ID to clean (default agent if not specified)"),
        workspace: str = typer.Option("", "--workspace", help="Override workspace path (takes precedence over --agent-id)"),
    ) -> None:
        """Scan MEMORY.md for expired P1/P2 entries; with --run, move them to memory/archive/."""
        if workspace:
            ws = Path(workspace).expanduser()
        elif agent_id:
            cfg = load_config()
            ws = _get_agent_workspace(cfg, agent_id)
        else:
            cfg = load_config()
            ws = get_workspace_path(cfg.agents.defaults.workspace)
        from joyhousebot.agent.memory_janitor import run_janitor
        actions = run_janitor(ws, dry_run=dry_run)
        if not actions:
            console.print("No expired P1/P2 entries.")
            return
        if dry_run:
            console.print(f"[dim]Would archive {len(actions)} entries (use --run to execute):[/dim]")
        else:
            console.print(f"[green]Archived {len(actions)} entries.[/green]")
        for a in actions:
            console.print(f"  [dim]{a['expire']}[/dim] {a['line'][:70]}...")

