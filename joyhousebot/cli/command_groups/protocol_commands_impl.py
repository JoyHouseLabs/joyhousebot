"""Protocol command group shims for joyhousebot CLI."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any, Callable

import typer
from rich.console import Console
from rich.table import Table

from joyhousebot.cli.services.approval_service import ApprovalService
from joyhousebot.cli.services.automation_service import HooksService, WebhooksService
from joyhousebot.cli.services.browser_service import BrowserService
from joyhousebot.cli.services.device_service import DeviceService
from joyhousebot.cli.services.directory_service import DirectoryService
from joyhousebot.cli.services.node_service import NodeService
from joyhousebot.cli.services.protocol_service import ProtocolService
from joyhousebot.cli.services.runtime_service import RuntimeService
from joyhousebot.cli.services.security_service import (
    AcpService,
    DnsService,
    SandboxService,
    SecurityService,
)
from joyhousebot.cli.services.state_service import StateService


def _print_json(console: Console, payload: Any) -> None:
    console.print(json.dumps(payload, ensure_ascii=False, indent=2))


def _rpc_error_with_code(message: str) -> str:
    raw = message.strip() or "unknown error"
    if raw.startswith("E_"):
        return raw
    lower = raw.lower()
    if "timeout" in lower:
        code = "E_RPC_TIMEOUT"
    elif "unauthorized" in lower or "forbidden" in lower or "permission denied" in lower:
        code = "E_RPC_AUTH"
    elif "not found" in lower or "no known" in lower:
        code = "E_RPC_NOT_FOUND"
    elif "conflict" in lower or "mismatch" in lower:
        code = "E_RPC_CONFLICT"
    else:
        code = "E_RPC_ERROR"
    return f"{code}: {raw}"


def _call_service(console: Console, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        console.print(f"[red]Error:[/red] {_rpc_error_with_code(str(exc))}")
        raise typer.Exit(1) from exc


def _severity_weight(severity: str) -> int:
    if severity == "critical":
        return 0
    if severity == "warn":
        return 1
    return 2


def _format_time_ago(ts_ms: int) -> str:
    try:
        delta = int(datetime.now().timestamp() * 1000) - int(ts_ms)
        if delta < 0:
            delta = 0
    except Exception:
        return "unknown"
    if delta < 60_000:
        return "just now"
    if delta < 3_600_000:
        return f"{delta // 60_000}m ago"
    if delta < 86_400_000:
        return f"{delta // 3_600_000}h ago"
    return f"{delta // 86_400_000}d ago"


def _format_duration(ms: int) -> str:
    value = max(0, int(ms))
    if value < 60_000:
        return f"{value // 1000}s"
    if value < 3_600_000:
        return f"{value // 60_000}m"
    if value < 86_400_000:
        return f"{value // 3_600_000}h"
    return f"{value // 86_400_000}d"


def _print_approvals_snapshot(console: Console, payload: dict[str, Any]) -> None:
    file_obj = payload.get("file") if isinstance(payload.get("file"), dict) else {}
    defaults = file_obj.get("defaults") if isinstance(file_obj.get("defaults"), dict) else {}
    agents = file_obj.get("agents") if isinstance(file_obj.get("agents"), dict) else {}

    defaults_parts = []
    for key in ("security", "ask", "askFallback"):
        value = str(defaults.get(key, "")).strip()
        if value:
            defaults_parts.append(f"{key}={value}")
    if isinstance(defaults.get("autoAllowSkills"), bool):
        defaults_parts.append(
            f"autoAllowSkills={'on' if bool(defaults.get('autoAllowSkills')) else 'off'}"
        )

    summary = Table(title="Approvals")
    summary.add_column("Field", style="cyan")
    summary.add_column("Value")
    summary.add_row("Target", str(payload.get("target", "local")))
    summary.add_row("Path", str(payload.get("path", "-")))
    summary.add_row("Exists", "yes" if bool(payload.get("exists")) else "no")
    summary.add_row("Hash", str(payload.get("hash", "")))
    summary.add_row("Version", str(file_obj.get("version", 1)))
    summary.add_row("Defaults", ", ".join(defaults_parts) if defaults_parts else "none")
    summary.add_row("Agents", str(len(agents)))
    console.print(summary)

    allowlist_rows: list[tuple[str, str, str]] = []
    for agent_id, agent_obj in agents.items():
        if not isinstance(agent_obj, dict):
            continue
        rows = agent_obj.get("allowlist") if isinstance(agent_obj.get("allowlist"), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            pattern = str(row.get("pattern", "")).strip()
            if not pattern:
                continue
            last_used_at = row.get("lastUsedAt")
            if isinstance(last_used_at, (int, float)):
                last_used = _format_time_ago(int(last_used_at))
            else:
                last_used = "unknown"
            allowlist_rows.append((str(agent_id), pattern, last_used))

    if not allowlist_rows:
        console.print("[dim]No allowlist entries.[/dim]")
        return

    table = Table(title="Allowlist")
    table.add_column("Agent", style="cyan")
    table.add_column("Pattern")
    table.add_column("Last Used")
    for agent_id, pattern, last_used in allowlist_rows:
        table.add_row(agent_id, pattern, last_used)
    console.print(table)


def _print_approvals_allowlist(console: Console, payload: dict[str, Any]) -> None:
    table = Table(title="Approvals Allowlist")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Target", str(payload.get("target", "local")))
    table.add_row("Node", str(payload.get("nodeId", "-")))
    table.add_row("Agent", str(payload.get("agent", "*")))
    table.add_row("baseHash", str(payload.get("baseHash", "")))
    console.print(table)
    entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
    if not entries:
        console.print("[dim]No allowlist entries.[/dim]")
        return
    entries_table = Table(title="Entries")
    entries_table.add_column("Pattern", style="cyan")
    entries_table.add_column("Last Used")
    for item in entries:
        if not isinstance(item, dict):
            continue
        pattern = str(item.get("pattern", "")).strip()
        last_used_at = item.get("lastUsedAt")
        if isinstance(last_used_at, (int, float)):
            last_used = _format_time_ago(int(last_used_at))
        else:
            last_used = "unknown"
        entries_table.add_row(pattern, last_used)
    console.print(entries_table)


def _print_sandbox_explain(console: Console, payload: dict[str, Any]) -> None:
    console.print("[bold]Effective sandbox:[/bold]")
    console.print(f"  agentId: {payload.get('agentId', payload.get('agent', '-'))}")
    console.print(f"  sessionKey: {payload.get('sessionKey', payload.get('session', '-'))}")
    if "mainSessionKey" in payload:
        console.print(f"  mainSessionKey: {payload.get('mainSessionKey')}")

    sandbox = payload.get("sandbox")
    if isinstance(sandbox, dict):
        mode = sandbox.get("mode", "-")
        scope = sandbox.get("scope", "-")
        per_session = bool(sandbox.get("perSession", False))
        session_is_sandboxed = bool(sandbox.get("sessionIsSandboxed", False))
        console.print(
            f"  runtime: {'sandboxed' if session_is_sandboxed else 'direct'}"
        )
        console.print(f"  mode: {mode}  scope: {scope}  perSession: {per_session}")
        console.print(
            f"  workspaceAccess: {sandbox.get('workspaceAccess', '-')}  "
            f"workspaceRoot: {sandbox.get('workspaceRoot', '-')}"
        )
        tools = sandbox.get("tools")
        if isinstance(tools, dict):
            allow = tools.get("allow") if isinstance(tools.get("allow"), list) else []
            deny = tools.get("deny") if isinstance(tools.get("deny"), list) else []
            console.print("")
            console.print("[bold]Sandbox tool policy:[/bold]")
            console.print(f"  allow: {', '.join(str(x) for x in allow) if allow else '(empty)'}")
            console.print(f"  deny:  {', '.join(str(x) for x in deny) if deny else '(empty)'}")
    elif isinstance(payload.get("policy"), dict):
        policy = payload.get("policy", {})
        console.print(
            "  runtime: "
            f"{'sandboxed' if bool(policy.get('restrict_to_workspace')) else 'direct'}"
        )
        console.print(f"  restrict_to_workspace: {bool(policy.get('restrict_to_workspace'))}")
        console.print(f"  exec_timeout: {policy.get('exec_timeout')}")
        console.print(f"  exec_shell_mode: {bool(policy.get('exec_shell_mode'))}")

    elevated = payload.get("elevated")
    if isinstance(elevated, dict):
        console.print("")
        console.print("[bold]Elevated:[/bold]")
        console.print(f"  enabled: {bool(elevated.get('enabled'))}")
        console.print(f"  channel: {elevated.get('channel', '(unknown)')}")
        console.print(f"  allowedByConfig: {bool(elevated.get('allowedByConfig'))}")
        failures = elevated.get("failures") if isinstance(elevated.get("failures"), list) else []
        if failures:
            text = ", ".join(
                f"{str(item.get('gate', '?'))} ({str(item.get('key', '?'))})"
                for item in failures
                if isinstance(item, dict)
            )
            if text:
                console.print(f"  failing gates: {text}")


def register_protocol_commands_impl(
    app: typer.Typer,
    console: Console,
    gateway_command: Callable[..., None],
) -> None:
    """Register protocol command groups."""
    protocol = ProtocolService()
    state = StateService(namespace="compat")

    devices = DeviceService(protocol)
    runtime = RuntimeService(protocol)
    hooks = HooksService(state)
    webhooks = WebhooksService(state)
    directories = DirectoryService(state)
    browser = BrowserService(protocol)
    nodes = NodeService(protocol, state)
    approvals = ApprovalService(protocol, state)
    sandbox = SandboxService(state, protocol)
    security = SecurityService(protocol)
    acp = AcpService(protocol)
    dns = DnsService()

    _register_phase0_commands(app, console, gateway_command, devices, runtime)
    _register_phase1_commands(app, console, hooks, webhooks, directories, browser, runtime)
    _register_phase2_commands(app, console, nodes, approvals, sandbox, security, acp, dns)


def _register_phase0_commands(
    app: typer.Typer,
    console: Console,
    gateway_command: Callable[..., None],
    device_service: DeviceService,
    runtime_service: RuntimeService,
) -> None:
    devices_app = typer.Typer(help="Manage paired devices and tokens")
    app.add_typer(devices_app, name="devices")

    @devices_app.command("list")
    @devices_app.command("status")
    def devices_list() -> None:
        payload = _call_service(console, device_service.list_pairs)
        pending = payload.get("pending") if isinstance(payload, dict) else []
        paired = payload.get("paired") if isinstance(payload, dict) else []

        pending_table = Table(title="Pending Device Pair Requests")
        pending_table.add_column("Request ID", style="cyan")
        pending_table.add_column("Device ID")
        pending_table.add_column("Display Name")
        for row in pending or []:
            pending_table.add_row(
                str(row.get("requestId", "")),
                str(row.get("deviceId", "")),
                str(row.get("displayName", "")),
            )
        console.print(pending_table if pending else "No pending requests.")

        paired_table = Table(title="Paired Devices")
        paired_table.add_column("Device ID", style="cyan")
        paired_table.add_column("Display Name")
        paired_table.add_column("Role")
        paired_table.add_column("Scopes")
        for row in paired or []:
            paired_table.add_row(
                str(row.get("deviceId", "")),
                str(row.get("displayName", "")),
                str(row.get("role", row.get("roles", ["-"])[0] if row.get("roles") else "-")),
                ", ".join(row.get("scopes", []) or []),
            )
        console.print(paired_table if paired else "No paired devices.")

    @devices_app.command("approve")
    def devices_approve(request_id: str = typer.Argument(..., help="Pair request ID")) -> None:
        _call_service(console, device_service.approve, request_id)
        console.print(f"[green]✓[/green] Approved request: {request_id}")

    @devices_app.command("reject")
    def devices_reject(request_id: str = typer.Argument(..., help="Pair request ID")) -> None:
        _call_service(console, device_service.reject, request_id)
        console.print(f"[green]✓[/green] Rejected request: {request_id}")

    @devices_app.command("token-rotate")
    def devices_token_rotate(
        device_id: str = typer.Option("", "--device-id", help="Target device id"),
        role: str = typer.Option("operator", "--role", help="Device role"),
        scopes: str = typer.Option("operator.read,operator.write", "--scopes", help="Comma-separated scopes"),
    ) -> None:
        payload = _call_service(console, device_service.rotate_token, device_id, role, scopes)
        _print_json(console, payload)

    @devices_app.command("token-revoke")
    def devices_token_revoke(
        device_id: str = typer.Option("", "--device-id", help="Target device id"),
    ) -> None:
        _call_service(console, device_service.revoke_token, device_id)
        console.print("[green]✓[/green] Token revoked")

    pairing_app = typer.Typer(help="Pairing aliases")
    app.add_typer(pairing_app, name="pairing")

    @pairing_app.command("list")
    def pairing_list() -> None:
        devices_list()

    @pairing_app.command("approve")
    def pairing_approve(request_id: str = typer.Argument(..., help="Pair request ID")) -> None:
        devices_approve(request_id=request_id)

    @pairing_app.command("reject")
    def pairing_reject(request_id: str = typer.Argument(..., help="Pair request ID")) -> None:
        devices_reject(request_id=request_id)

    @pairing_app.command("login")
    def pairing_login() -> None:
        console.print("Pairing login delegates to [cyan]channels login[/cyan].")
        raise typer.Exit(_call_service(console, runtime_service.run_channels, "login"))

    system_app = typer.Typer(help="Gateway/runtime system control")
    app.add_typer(system_app, name="system")

    @system_app.command("status")
    def system_status() -> None:
        info = _call_service(console, runtime_service.system_status_data)
        table = Table(title="System Status")
        table.add_column("Check", style="cyan")
        table.add_column("Value")
        if info.get("gateway_ok"):
            health = info.get("health", {})
            table.add_row("gateway", "running")
            table.add_row("service", str(health.get("service", "joyhousebot")))
        else:
            table.add_row("gateway", f"not reachable ({info.get('gateway_error')})")
        table.add_row(
            "presence entries",
            "unavailable" if info.get("presence_entries") is None else str(info.get("presence_entries")),
        )
        table.add_row(
            "last heartbeat",
            "unavailable" if info.get("last_heartbeat") is None else str(info.get("last_heartbeat")),
        )
        console.print(table)

    @system_app.command("presence")
    def system_presence() -> None:
        _print_json(console, _call_service(console, runtime_service.system_presence))

    @system_app.command("start")
    def system_start(
        host: str = typer.Option("127.0.0.1", "--host", "-h"),
        port: int = typer.Option(18790, "--port", "-p"),
        verbose: bool = typer.Option(False, "--verbose", "-v"),
        wallet_unlock: bool = typer.Option(False, "--wallet-unlock"),
    ) -> None:
        gateway_command(host=host, port=port, verbose=verbose, wallet_unlock=wallet_unlock)

    @system_app.command("stop")
    def system_stop() -> None:
        console.print(
            "[yellow]No background stop endpoint yet.[/yellow] "
            "If gateway is running in terminal, press Ctrl+C there."
        )

    @system_app.command("logs")
    def system_logs(limit: int = typer.Option(100, "--limit", "-n", help="Tail event rows")) -> None:
        _print_json(console, _call_service(console, runtime_service.system_logs, limit))

    @app.command("docs")
    def docs_open(topic: str = typer.Option("", "--topic", help="Optional docs topic")) -> None:
        """Open docs homepage or local docs folder."""
        ok, value = _call_service(console, runtime_service.docs_target, topic)
        if ok:
            console.print(f"[green]✓[/green] Opened {value}")
        else:
            console.print(f"Unable to open browser automatically. Docs path: {value}")

    @app.command("update")
    def update(
        run: bool = typer.Option(False, "--run", help="Trigger update workflow in gateway"),
        status: bool = typer.Option(False, "--status", help="Show update channel/version status"),
        channel: str = typer.Option("", "--channel", help="Persist update channel"),
        tag: str = typer.Option("", "--tag", help="One-off update tag/version"),
        wizard: bool = typer.Option(False, "--wizard", help="Show interactive-like wizard steps"),
    ) -> None:
        """Show version and optionally trigger gateway update flow."""
        payload = _call_service(console, runtime_service.update_info, run, status, channel, tag, wizard)
        table = Table(title="CLI Update")
        table.add_column("Field", style="cyan")
        table.add_column("Value")
        table.add_row("installed version", str(payload.get("installed_version")))
        table.add_row("recommended command", str(payload.get("recommended_command")))
        if "channel" in payload:
            table.add_row("channel", str(payload.get("channel")))
        if "tag" in payload:
            table.add_row("tag", str(payload.get("tag")))
        if "status" in payload:
            table.add_row("status", json.dumps(payload.get("status"), ensure_ascii=False))
        if "wizard" in payload:
            table.add_row("wizard", json.dumps(payload.get("wizard"), ensure_ascii=False))
        if "update_run" in payload:
            table.add_row("update.run", json.dumps(payload["update_run"], ensure_ascii=False))
        console.print(table)

    @app.command("uninstall")
    def uninstall(
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
        keep_config: bool = typer.Option(False, "--keep-config", help="Keep config.json"),
        scope: str = typer.Option("all", "--scope", help="all|state|config|workspace"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Preview without deleting"),
        non_interactive: bool = typer.Option(False, "--non-interactive", help="No prompt; requires --yes"),
    ) -> None:
        """Uninstall local runtime data while keeping CLI package."""
        targets = _call_service(console, runtime_service.uninstall_targets, keep_config, scope)
        if non_interactive and not yes:
            raise typer.BadParameter("--non-interactive requires --yes")
        if not yes and not non_interactive:
            items = "\n".join(f"- {x}" for x in targets)
            if not typer.confirm(f"This will remove:\n{items}\nContinue?"):
                raise typer.Exit(0)
        result = _call_service(console, runtime_service.perform_uninstall, targets, keep_config, dry_run)
        _print_json(console, result)


def _register_phase1_commands(
    app: typer.Typer,
    console: Console,
    hooks_service: HooksService,
    webhooks_service: WebhooksService,
    directory_service: DirectoryService,
    browser_service: BrowserService,
    runtime_service: RuntimeService,
) -> None:
    hooks_app = typer.Typer(help="Lightweight CLI hooks")
    app.add_typer(hooks_app, name="hooks")

    @hooks_app.command("list")
    def hooks_list() -> None:
        _print_json(console, _call_service(console, hooks_service.list))

    @hooks_app.command("add")
    def hooks_add(
        stage: str = typer.Option(..., "--stage", help="before or after"),
        command: str = typer.Option(..., "--command", help="Shell command to run"),
    ) -> None:
        stage_key = stage.strip().lower()
        if stage_key not in {"before", "after"}:
            raise typer.BadParameter("--stage must be before or after")
        _call_service(console, hooks_service.add, stage_key, command)
        console.print(f"[green]✓[/green] Hook added to {stage_key}")

    @hooks_app.command("remove")
    def hooks_remove(
        stage: str = typer.Option(..., "--stage", help="before or after"),
        index: int = typer.Option(..., "--index", help="Hook index from hooks list"),
    ) -> None:
        stage_key = stage.strip().lower()
        if stage_key not in {"before", "after"}:
            raise typer.BadParameter("--stage must be before or after")
        removed = _call_service(console, hooks_service.remove, stage_key, index)
        console.print(f"[green]✓[/green] Removed hook: {removed}")

    @hooks_app.command("run")
    def hooks_run(stage: str = typer.Option(..., "--stage", help="before or after")) -> None:
        stage_key = stage.strip().lower()
        if stage_key not in {"before", "after"}:
            raise typer.BadParameter("--stage must be before or after")
        cmds = _call_service(console, hooks_service.run, stage_key)
        if not cmds:
            console.print("No hooks.")
            return
        for cmd in cmds:
            console.print(f"[cyan]$[/cyan] {cmd}")

    @hooks_app.command("check")
    def hooks_check() -> None:
        _print_json(console, _call_service(console, hooks_service.check))

    @hooks_app.command("install")
    def hooks_install(
        source: str = typer.Argument(..., help="Hook pack source path/spec"),
        link: bool = typer.Option(False, "--link", help="Link local path"),
    ) -> None:
        _print_json(console, _call_service(console, hooks_service.install, source, link))

    @hooks_app.command("update")
    def hooks_update(
        hook_id: str = typer.Argument("", help="Hook pack id"),
        all_items: bool = typer.Option(False, "--all"),
        dry_run: bool = typer.Option(False, "--dry-run"),
    ) -> None:
        _print_json(console, _call_service(console, hooks_service.update, all_items, hook_id, dry_run))

    webhooks_app = typer.Typer(help="Manage outbound webhooks")
    app.add_typer(webhooks_app, name="webhooks")

    @webhooks_app.command("list")
    def webhooks_list() -> None:
        _print_json(console, _call_service(console, webhooks_service.list))

    @webhooks_app.command("add")
    def webhooks_add(
        name: str = typer.Option(..., "--name"),
        url: str = typer.Option(..., "--url"),
        event: str = typer.Option("message", "--event"),
    ) -> None:
        _call_service(console, webhooks_service.add, name, url, event)
        console.print(f"[green]✓[/green] Webhook added: {name}")

    @webhooks_app.command("remove")
    def webhooks_remove(name: str = typer.Argument(..., help="Webhook name")) -> None:
        _call_service(console, webhooks_service.remove, name)
        console.print(f"[green]✓[/green] Webhook removed: {name}")

    @webhooks_app.command("test")
    def webhooks_test(
        name: str = typer.Argument(..., help="Webhook name"),
        payload_json: str = typer.Option('{"ok":true}', "--payload", help="JSON payload"),
    ) -> None:
        response = _call_service(console, webhooks_service.test, name, payload_json)
        _print_json(console, {"ok": True, "response": response})

    gmail_app = typer.Typer(help="Gmail webhook integration")
    webhooks_app.add_typer(gmail_app, name="gmail")

    @gmail_app.command("setup")
    def webhooks_gmail_setup(
        account: str = typer.Option(..., "--account"),
        hook_url: str = typer.Option(..., "--hook-url"),
        push_token: str = typer.Option(..., "--push-token"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        _print_json(
            console,
            _call_service(console, webhooks_service.gmail_setup, account, hook_url, push_token, json_out),
        )

    @gmail_app.command("run")
    def webhooks_gmail_run(
        account: str = typer.Option(..., "--account"),
        payload_json: str = typer.Option("", "--payload", help="Optional JSON payload"),
    ) -> None:
        _print_json(console, _call_service(console, webhooks_service.gmail_run, account, payload_json))

    directory_app = typer.Typer(help="Directory navigation helpers")
    app.add_typer(directory_app, name="directory")

    @directory_app.command("list")
    def directory_list(path: str = typer.Option("", "--path", help="Directory path")) -> None:
        base, rows = _call_service(console, directory_service.list, path)
        table = Table(title=f"Directory: {base}")
        table.add_column("Name", style="cyan")
        table.add_column("Type")
        table.add_column("Size")
        for row in rows:
            table.add_row(row["name"], row["type"], row["size"])
        console.print(table)

    @directory_app.command("agents")
    def directory_agents() -> None:
        rows = _call_service(console, directory_service.agent_workspaces)
        table = Table(title="Agent Workspaces")
        table.add_column("Agent", style="cyan")
        table.add_column("Workspace")
        table.add_column("Exists")
        for row in rows:
            table.add_row(row["agent"], row["workspace"], row["exists"])
        console.print(table)

    @directory_app.command("self")
    def directory_self() -> None:
        _print_json(console, _call_service(console, directory_service.self_entry))

    @directory_app.command("peers")
    def directory_peers(
        query: str = typer.Option("", "--query"),
        limit: int = typer.Option(50, "--limit"),
    ) -> None:
        _print_json(console, _call_service(console, directory_service.list_peers, query, limit))

    @directory_app.command("groups")
    def directory_groups(
        query: str = typer.Option("", "--query"),
        limit: int = typer.Option(50, "--limit"),
    ) -> None:
        _print_json(console, _call_service(console, directory_service.list_groups, query, limit))

    @directory_app.command("members")
    def directory_members(
        group_id: str = typer.Option(..., "--group-id"),
        limit: int = typer.Option(200, "--limit"),
    ) -> None:
        _print_json(console, _call_service(console, directory_service.list_group_members, group_id, limit))

    browser_app = typer.Typer(help="Browser manage/inspect/action/debug/state")
    app.add_typer(browser_app, name="browser")

    @browser_app.callback(invoke_without_command=True)
    def browser_callback(
        ctx: typer.Context,
        method: str = typer.Option("GET", "--method"),
        path: str = typer.Option("/status", "--path"),
        node_id: str = typer.Option("", "--node-id"),
        timeout_ms: int = typer.Option(10000, "--timeout-ms"),
    ) -> None:
        if ctx.invoked_subcommand is None:
            _print_json(console, _call_service(console, browser_service.request, method, path, node_id, timeout_ms))

    @browser_app.command("request")
    def browser_request(
        method: str = typer.Option("GET", "--method"),
        path: str = typer.Option("/status", "--path"),
        node_id: str = typer.Option("", "--node-id"),
        timeout_ms: int = typer.Option(10000, "--timeout-ms"),
    ) -> None:
        _print_json(console, _call_service(console, browser_service.request, method, path, node_id, timeout_ms))

    @browser_app.command("status")
    def browser_status(
        node_id: str = typer.Option("", "--node-id"),
        timeout_ms: int = typer.Option(10000, "--timeout-ms"),
    ) -> None:
        _print_json(console, _call_service(console, browser_service.status, node_id, timeout_ms))

    @browser_app.command("inspect")
    def browser_inspect(
        path: str = typer.Option("/status", "--path"),
        node_id: str = typer.Option("", "--node-id"),
        timeout_ms: int = typer.Option(10000, "--timeout-ms"),
    ) -> None:
        _print_json(console, _call_service(console, browser_service.inspect, path, node_id, timeout_ms))

    @browser_app.command("action")
    def browser_action(
        action: str = typer.Option(..., "--action"),
        payload_json: str = typer.Option("{}", "--payload"),
        node_id: str = typer.Option("", "--node-id"),
        timeout_ms: int = typer.Option(10000, "--timeout-ms"),
    ) -> None:
        _print_json(
            console,
            _call_service(console, browser_service.action, action, payload_json, node_id, timeout_ms),
        )

    @browser_app.command("debug")
    def browser_debug(
        enabled: bool = typer.Option(True, "--enabled/--disabled"),
        node_id: str = typer.Option("", "--node-id"),
        timeout_ms: int = typer.Option(10000, "--timeout-ms"),
    ) -> None:
        _print_json(console, _call_service(console, browser_service.debug, enabled, node_id, timeout_ms))

    @browser_app.command("state")
    def browser_state(
        key: str = typer.Option(..., "--key"),
        value_json: str = typer.Option("", "--value"),
        node_id: str = typer.Option("", "--node-id"),
        timeout_ms: int = typer.Option(10000, "--timeout-ms"),
    ) -> None:
        _print_json(
            console,
            _call_service(console, browser_service.state, key, value_json, node_id, timeout_ms),
        )

def _register_phase2_commands(
    app: typer.Typer,
    console: Console,
    node_service: NodeService,
    approval_service: ApprovalService,
    sandbox_service: SandboxService,
    security_service: SecurityService,
    acp_service: AcpService,
    dns_service: DnsService,
) -> None:
    node_app = typer.Typer(help="Node host lifecycle commands")
    app.add_typer(node_app, name="node")

    nodes_app = typer.Typer(help="Paired node control commands")
    app.add_typer(nodes_app, name="nodes")

    def _resolve_node_ref(node_ref: str) -> str:
        return _call_service(console, node_service.resolve_node_id, node_ref)

    def _normalize_approvals_target(value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            return ""
        if normalized not in {"local", "gateway", "node"}:
            raise typer.BadParameter("target must be local|gateway|node")
        return normalized

    @node_app.command("run")
    def node_run(
        host: str = typer.Option("127.0.0.1", "--host"),
        port: int = typer.Option(18789, "--port"),
        tls: bool = typer.Option(False, "--tls"),
        node_id: str = typer.Option("", "--node-id"),
        display_name: str = typer.Option("", "--display-name"),
    ) -> None:
        _print_json(console, _call_service(console, node_service.run_host, host, port, tls, node_id, display_name))

    @node_app.command("install")
    def node_install(
        runtime: str = typer.Option("node", "--runtime"),
        force: bool = typer.Option(False, "--force"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        del json_out
        _print_json(console, _call_service(console, node_service.install_host, runtime, force))

    @node_app.command("status")
    def node_status(json_out: bool = typer.Option(False, "--json")) -> None:
        del json_out
        _print_json(console, _call_service(console, node_service.host_status))

    @node_app.command("stop")
    def node_stop(json_out: bool = typer.Option(False, "--json")) -> None:
        del json_out
        _print_json(console, _call_service(console, node_service.stop_host))

    @node_app.command("restart")
    def node_restart(json_out: bool = typer.Option(False, "--json")) -> None:
        del json_out
        _print_json(console, _call_service(console, node_service.restart_host))

    @node_app.command("uninstall")
    def node_uninstall(json_out: bool = typer.Option(False, "--json")) -> None:
        del json_out
        _print_json(console, _call_service(console, node_service.uninstall_host))

    @nodes_app.command("list")
    @nodes_app.command("status")
    def nodes_list() -> None:
        payload = _call_service(console, node_service.list)
        nodes = payload.get("nodes") if isinstance(payload, dict) else []
        table = Table(title="Nodes")
        table.add_column("Node ID", style="cyan")
        table.add_column("Name")
        table.add_column("Connected")
        table.add_column("Paired")
        table.add_column("Platform")
        for n in nodes or []:
            table.add_row(
                str(n.get("nodeId", "")),
                str(n.get("displayName", "")),
                "yes" if n.get("connected") else "no",
                "yes" if n.get("paired") else "no",
                str(n.get("platform", "")),
            )
        console.print(table if nodes else "No nodes.")

    @nodes_app.command("describe")
    def nodes_describe(
        node: str = typer.Option("", "--node", help="Node ID/name/IP"),
        node_id: str = typer.Argument("", help="Node ID (legacy)"),
    ) -> None:
        resolved = node.strip() or node_id.strip()
        if not resolved:
            raise typer.BadParameter("--node is required")
        _print_json(console, _call_service(console, node_service.describe, _resolve_node_ref(resolved)))

    @nodes_app.command("rename")
    def nodes_rename(
        node: str = typer.Option("", "--node", help="Node ID/name/IP"),
        name: str = typer.Option("", "--name", help="Display name"),
        node_id: str = typer.Argument("", help="Node ID (legacy)"),
        legacy_name: str = typer.Argument("", help="Display name (legacy)"),
    ) -> None:
        resolved_node = node.strip() or node_id.strip()
        resolved_name = name.strip() or legacy_name.strip()
        if not resolved_node or not resolved_name:
            raise typer.BadParameter("--node and --name are required")
        _print_json(
            console,
            _call_service(console, node_service.rename, _resolve_node_ref(resolved_node), resolved_name),
        )

    @nodes_app.command("invoke")
    def nodes_invoke(
        node_id: str = typer.Option("", "--node-id", "--node"),
        command: str = typer.Option(..., "--command"),
        params_json: str = typer.Option("{}", "--params", help="JSON params"),
        timeout_ms: int = typer.Option(30000, "--timeout-ms", "--invoke-timeout"),
    ) -> None:
        if not node_id.strip():
            raise typer.BadParameter("--node is required")
        _print_json(
            console,
            _call_service(
                console,
                node_service.invoke,
                _resolve_node_ref(node_id.strip()),
                command,
                params_json,
                timeout_ms,
            ),
        )

    @nodes_app.command("run")
    def nodes_run(
        node_id: str = typer.Option("", "--node-id", "--node"),
        cwd: str = typer.Option("", "--cwd"),
        raw: str = typer.Option("", "--raw"),
        command_timeout: int = typer.Option(0, "--command-timeout"),
        invoke_timeout: int = typer.Option(30000, "--invoke-timeout"),
        command_argv: list[str] | None = typer.Argument(None),
    ) -> None:
        resolved_node = node_id.strip()
        if not resolved_node:
            raise typer.BadParameter("--node is required")
        resolved_node_id = _resolve_node_ref(resolved_node)
        argv = [x for x in (command_argv or []) if str(x).strip()]
        if raw.strip() and argv:
            raise typer.BadParameter("use --raw or argv, not both")
        if not raw.strip() and not argv:
            raise typer.BadParameter("command required")
        if raw.strip():
            argv = ["/bin/sh", "-lc", raw.strip()]
        payload: dict[str, Any] = {"command": argv}
        if cwd.strip():
            payload["cwd"] = cwd.strip()
        if command_timeout > 0:
            payload["timeoutMs"] = command_timeout
        _print_json(
            console,
            _call_service(
                console,
                node_service.invoke,
                resolved_node_id,
                "system.run",
                json.dumps(payload, ensure_ascii=False),
                invoke_timeout,
            ),
        )

    @nodes_app.command("pending")
    @nodes_app.command("pair-list")
    def node_pair_list() -> None:
        _print_json(console, _call_service(console, node_service.pair_list))

    @nodes_app.command("approve")
    @nodes_app.command("pair-approve")
    def node_pair_approve(request_id: str = typer.Argument(..., help="Node pair request ID")) -> None:
        _print_json(console, _call_service(console, node_service.pair_approve, request_id))

    @nodes_app.command("reject")
    @nodes_app.command("pair-reject")
    def node_pair_reject(request_id: str = typer.Argument(..., help="Node pair request ID")) -> None:
        _print_json(console, _call_service(console, node_service.pair_reject, request_id))

    @nodes_app.command("pair-verify")
    def node_pair_verify(
        node_id: str = typer.Option(..., "--node-id", "--node"),
        token: str = typer.Option(..., "--token"),
    ) -> None:
        _print_json(
            console,
            _call_service(console, node_service.pair_verify, _resolve_node_ref(node_id.strip()), token),
        )

    approvals_app = typer.Typer(help="Exec approvals workflow and policies")
    app.add_typer(approvals_app, name="approvals")
    app.add_typer(approvals_app, name="exec-approvals")

    def _resolve_approvals_target(
        target: str,
        gateway: bool,
        node: str,
        node_id: str,
    ) -> tuple[str, str]:
        normalized = _normalize_approvals_target(target)
        node_ref = node.strip()
        legacy_node_id = node_id.strip()
        has_node = bool(node_ref)
        has_legacy = bool(legacy_node_id)

        if gateway and (has_node or has_legacy or normalized == "node"):
            raise typer.BadParameter(
                "E_TARGET_CONFLICT: --gateway conflicts with --node/--node-id/--target node"
            )
        if has_node and has_legacy:
            raise typer.BadParameter("E_TARGET_CONFLICT: use --node or --node-id (not both)")
        if normalized == "local" and (has_node or has_legacy):
            raise typer.BadParameter(
                "E_TARGET_CONFLICT: local target does not accept --node/--node-id"
            )

        if gateway or normalized == "gateway":
            return "gateway", ""
        if has_node:
            return "node", _resolve_node_ref(node_ref)
        if has_legacy:
            return "node", legacy_node_id
        if normalized == "node":
            raise typer.BadParameter("E_TARGET_REQUIRED: node target requires --node or --node-id")
        return "local", ""

    @approvals_app.command("get")
    def approvals_get(
        node: str = typer.Option("", "--node", help="Target node id/name/IP"),
        gateway: bool = typer.Option(False, "--gateway", help="Force gateway approvals"),
        node_id: str = typer.Option("", "--node-id", help="Node id (legacy)"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        target, resolved_node_id = _resolve_approvals_target("", gateway, node, node_id)
        payload = _call_service(console, approval_service.policy_get, target, resolved_node_id)
        if json_out:
            _print_json(console, payload)
            return
        if isinstance(payload, dict):
            _print_approvals_snapshot(console, payload)
            return
        _print_json(console, payload)

    @approvals_app.command("set")
    def approvals_set(
        file_path: str = typer.Option("", "--file", help="Path to approvals JSON file"),
        stdin: bool = typer.Option(False, "--stdin", help="Read approvals JSON from stdin"),
        node: str = typer.Option("", "--node", help="Target node id/name/IP"),
        gateway: bool = typer.Option(False, "--gateway", help="Force gateway approvals"),
        node_id: str = typer.Option("", "--node-id", help="Node id (legacy)"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        if not file_path and not stdin:
            raise typer.BadParameter("Provide --file or --stdin.")
        if file_path and stdin:
            raise typer.BadParameter("Use either --file or --stdin (not both).")
        file_input = sys.stdin.read() if stdin else file_path
        target, resolved_node_id = _resolve_approvals_target("", gateway, node, node_id)
        payload = _call_service(
            console, approval_service.policy_set, file_input, target, resolved_node_id, ""
        )
        if json_out:
            _print_json(console, payload)
            return
        if isinstance(payload, dict):
            _print_approvals_snapshot(console, payload)
            return
        _print_json(console, payload)

    @approvals_app.command("request")
    def approvals_request(
        command: str = typer.Option(..., "--command"),
        timeout_ms: int = typer.Option(300000, "--timeout-ms"),
        request_id: str = typer.Option("", "--id"),
    ) -> None:
        _print_json(
            console,
            _call_service(console, approval_service.request, command, timeout_ms, request_id),
        )

    @approvals_app.command("wait")
    def approvals_wait(request_id: str = typer.Argument(..., help="Approval request ID")) -> None:
        _print_json(console, _call_service(console, approval_service.wait, request_id))

    @approvals_app.command("resolve")
    def approvals_resolve(
        request_id: str = typer.Argument(..., help="Approval request ID"),
        decision: str = typer.Option(..., "--decision", help="allow-once | allow-always | deny"),
    ) -> None:
        normalized = decision.strip().lower()
        if normalized not in {"allow-once", "allow-always", "deny"}:
            raise typer.BadParameter("--decision must be allow-once | allow-always | deny")
        _print_json(console, _call_service(console, approval_service.resolve, request_id, normalized))

    @approvals_app.command("policy-get")
    def approvals_policy_get(
        target: str = typer.Option("", "--target", help="local|gateway|node"),
        node: str = typer.Option("", "--node", help="Target node id/name/IP"),
        gateway: bool = typer.Option(False, "--gateway"),
        node_id: str = typer.Option("", "--node-id"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        resolved_target, resolved_node_id = _resolve_approvals_target(target, gateway, node, node_id)
        payload = _call_service(console, approval_service.policy_get, resolved_target, resolved_node_id)
        if json_out:
            _print_json(console, payload)
            return
        if isinstance(payload, dict):
            _print_approvals_snapshot(console, payload)
            return
        _print_json(console, payload)

    @approvals_app.command("policy-set")
    def approvals_policy_set(
        file_json: str = typer.Option(..., "--file", help="JSON policy content or file path"),
        target: str = typer.Option("", "--target", help="local|gateway|node"),
        node: str = typer.Option("", "--node", help="Target node id/name/IP"),
        gateway: bool = typer.Option(False, "--gateway"),
        node_id: str = typer.Option("", "--node-id"),
        base_hash: str = typer.Option("", "--base-hash"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        resolved_target, resolved_node_id = _resolve_approvals_target(target, gateway, node, node_id)
        payload = _call_service(
            console,
            approval_service.policy_set,
            file_json,
            resolved_target,
            resolved_node_id,
            base_hash,
        )
        if json_out:
            _print_json(console, payload)
            return
        if isinstance(payload, dict):
            _print_approvals_snapshot(console, payload)
            return
        _print_json(console, payload)

    @approvals_app.command("node-get")
    def approvals_node_get(
        node: str = typer.Option("", "--node", help="Target node id/name/IP"),
        node_id: str = typer.Option("", "--node-id"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        resolved = node.strip() or node_id.strip()
        if not resolved:
            raise typer.BadParameter("--node or --node-id is required")
        resolved_node_id = _resolve_node_ref(resolved) if node.strip() else resolved
        payload = _call_service(console, approval_service.policy_get, "node", resolved_node_id)
        if json_out:
            _print_json(console, payload)
            return
        if isinstance(payload, dict):
            _print_approvals_snapshot(console, payload)
            return
        _print_json(console, payload)

    @approvals_app.command("node-set")
    def approvals_node_set(
        node: str = typer.Option("", "--node", help="Target node id/name/IP"),
        node_id: str = typer.Option("", "--node-id"),
        file_json: str = typer.Option(..., "--file", help="JSON policy content or file path"),
        base_hash: str = typer.Option("", "--base-hash"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        resolved = node.strip() or node_id.strip()
        if not resolved:
            raise typer.BadParameter("--node or --node-id is required")
        resolved_node_id = _resolve_node_ref(resolved) if node.strip() else resolved
        payload = _call_service(
            console, approval_service.policy_set, file_json, "node", resolved_node_id, base_hash
        )
        if json_out:
            _print_json(console, payload)
            return
        if isinstance(payload, dict):
            _print_approvals_snapshot(console, payload)
            return
        _print_json(console, payload)

    allowlist_app = typer.Typer(help="Approvals allowlist operations")
    approvals_app.add_typer(allowlist_app, name="allowlist")

    @allowlist_app.command("list")
    def approvals_allowlist_list(
        node: str = typer.Option("", "--node", help="Target node id/name/IP"),
        gateway: bool = typer.Option(False, "--gateway"),
        target: str = typer.Option("", "--target", help="local|gateway|node"),
        node_id: str = typer.Option("", "--node-id"),
        agent: str = typer.Option("*", "--agent"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        resolved_target, resolved_node_id = _resolve_approvals_target(target, gateway, node, node_id)
        payload = _call_service(
            console, approval_service.allowlist_list, resolved_target, resolved_node_id, agent
        )
        if json_out:
            _print_json(console, payload)
            return
        if isinstance(payload, dict):
            _print_approvals_allowlist(console, payload)
            return
        _print_json(console, payload)

    @allowlist_app.command("add")
    def approvals_allowlist_add(
        pattern_arg: str = typer.Argument("", help="Pattern (OpenClaw style)"),
        pattern: str = typer.Option("", "--pattern", help="Pattern (legacy)"),
        node: str = typer.Option("", "--node", help="Target node id/name/IP"),
        gateway: bool = typer.Option(False, "--gateway"),
        target: str = typer.Option("", "--target", help="local|gateway|node"),
        node_id: str = typer.Option("", "--node-id"),
        agent: str = typer.Option("*", "--agent"),
        base_hash: str = typer.Option("", "--base-hash"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        resolved_pattern = pattern_arg.strip() or pattern.strip()
        if not resolved_pattern:
            raise typer.BadParameter("Pattern required.")
        resolved_target, resolved_node_id = _resolve_approvals_target(target, gateway, node, node_id)
        payload = _call_service(
            console,
            approval_service.allowlist_add,
            resolved_pattern,
            resolved_target,
            resolved_node_id,
            agent,
            base_hash,
        )
        if json_out:
            _print_json(console, payload)
            return
        if isinstance(payload, dict):
            _print_approvals_snapshot(console, payload)
            return
        _print_json(console, payload)

    @allowlist_app.command("remove")
    def approvals_allowlist_remove(
        pattern_arg: str = typer.Argument("", help="Pattern (OpenClaw style)"),
        pattern: str = typer.Option("", "--pattern", help="Pattern (legacy)"),
        node: str = typer.Option("", "--node", help="Target node id/name/IP"),
        gateway: bool = typer.Option(False, "--gateway"),
        target: str = typer.Option("", "--target", help="local|gateway|node"),
        node_id: str = typer.Option("", "--node-id"),
        agent: str = typer.Option("*", "--agent"),
        base_hash: str = typer.Option("", "--base-hash"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        resolved_pattern = pattern_arg.strip() or pattern.strip()
        if not resolved_pattern:
            raise typer.BadParameter("Pattern required.")
        resolved_target, resolved_node_id = _resolve_approvals_target(target, gateway, node, node_id)
        payload = _call_service(
            console,
            approval_service.allowlist_remove,
            resolved_pattern,
            resolved_target,
            resolved_node_id,
            agent,
            base_hash,
        )
        if json_out:
            _print_json(console, payload)
            return
        if isinstance(payload, dict):
            _print_approvals_snapshot(console, payload)
            return
        _print_json(console, payload)

    sandbox_app = typer.Typer(help="Sandbox policies")
    app.add_typer(sandbox_app, name="sandbox")

    @sandbox_app.command("status")
    def sandbox_status() -> None:
        _print_json(console, _call_service(console, sandbox_service.status))

    @sandbox_app.command("set")
    def sandbox_set(
        restrict_to_workspace: bool = typer.Option(
            False, "--restrict-to-workspace/--no-restrict-to-workspace"
        ),
        timeout: int = typer.Option(60, "--timeout"),
        shell_mode: bool = typer.Option(False, "--shell-mode/--no-shell-mode"),
    ) -> None:
        _call_service(console, sandbox_service.set, restrict_to_workspace, timeout, shell_mode)
        console.print("[green]✓[/green] Sandbox-related config updated.")

    @sandbox_app.command("list")
    def sandbox_list(
        browser_only: bool = typer.Option(False, "--browser"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        rows = _call_service(console, sandbox_service.list, browser_only)
        if json_out:
            _print_json(console, rows)
            return
        items = [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []
        browser_items = [x for x in items if bool(x.get("browser"))]
        normal_items = [x for x in items if not bool(x.get("browser"))]
        now_ms = int(datetime.now().timestamp() * 1000)

        def print_items(title: str, empty_message: str, data: list[dict[str, Any]], show_ports: bool) -> None:
            if not data:
                console.print(empty_message)
                return
            console.print("")
            console.print(f"[bold]{title}[/bold]")
            console.print("")
            for item in data:
                name = str(item.get("containerName") or item.get("name") or item.get("id") or "-")
                running = bool(item.get("running"))
                status = str(item.get("status") or ("running" if running else "stopped"))
                image = str(item.get("image") or "-")
                image_match = item.get("imageMatch")
                match_hint = ""
                if isinstance(image_match, bool):
                    match_hint = " (match)" if image_match else " (mismatch)"
                created_at = int(item.get("createdAtMs", now_ms)) if item.get("createdAtMs") else now_ms
                last_used = int(item.get("lastUsedAtMs", now_ms)) if item.get("lastUsedAtMs") else now_ms
                session_key = str(item.get("sessionKey") or item.get("session") or item.get("agent") or "-")
                console.print(f"  {name}")
                console.print(f"    Status:  {status}")
                console.print(f"    Image:   {image}{match_hint}")
                if show_ports:
                    if item.get("cdpPort") is not None:
                        console.print(f"    CDP:     {item.get('cdpPort')}")
                    if item.get("noVncPort") is not None:
                        console.print(f"    noVNC:   {item.get('noVncPort')}")
                console.print(f"    Age:     {_format_duration(now_ms - created_at)}")
                console.print(f"    Idle:    {_format_duration(now_ms - last_used)}")
                console.print(f"    Session: {session_key}")
                console.print("")

        if browser_only:
            print_items(
                "🌐 Sandbox Browser Containers:",
                "No sandbox browser containers found.",
                browser_items,
                True,
            )
            running_count = len([x for x in browser_items if bool(x.get("running"))])
            console.print(f"Total: {len(browser_items)} ({running_count} running)")
            return

        print_items("📦 Sandbox Containers:", "No sandbox containers found.", normal_items, False)
        if browser_items:
            print_items("🌐 Sandbox Browser Containers:", "", browser_items, True)
        total_count = len(items)
        running_count = len([x for x in items if bool(x.get("running"))])
        mismatch_count = len([x for x in items if x.get("imageMatch") is False])
        console.print(f"Total: {total_count} ({running_count} running)")
        if mismatch_count > 0:
            console.print(f"⚠️  {mismatch_count} container(s) with image mismatch detected.")
            console.print("   Run 'openclaw sandbox recreate --all' to update all containers.")

    @sandbox_app.command("recreate")
    def sandbox_recreate(
        all_items: bool = typer.Option(False, "--all"),
        session: str = typer.Option("", "--session"),
        agent: str = typer.Option("", "--agent"),
        browser_only: bool = typer.Option(False, "--browser"),
        force: bool = typer.Option(False, "--force"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        exclusive_count = sum(1 for item in (all_items, bool(session.strip()), bool(agent.strip())) if item)
        if exclusive_count == 0:
            raise typer.BadParameter("E_TARGET_REQUIRED: Please specify --all, --session, or --agent")
        if exclusive_count > 1:
            raise typer.BadParameter(
                "E_TARGET_CONFLICT: Please specify only one of: --all, --session, --agent"
            )
        payload = _call_service(console, sandbox_service.recreate, all_items, session, agent, browser_only, force)
        if json_out:
            _print_json(console, payload)
            return
        if isinstance(payload, dict):
            if isinstance(payload.get("containers"), list) or isinstance(payload.get("browsers"), list):
                containers = payload.get("containers") if isinstance(payload.get("containers"), list) else []
                browsers = payload.get("browsers") if isinstance(payload.get("browsers"), list) else []
                console.print("")
                console.print("Containers to be recreated:")
                if containers:
                    console.print("")
                    console.print("📦 Sandbox Containers:")
                    for item in containers:
                        if isinstance(item, dict):
                            name = str(item.get("containerName") or item.get("name") or item.get("id") or "-")
                            running = bool(item.get("running"))
                            console.print(f"  - {name} ({'running' if running else 'stopped'})")
                if browsers:
                    console.print("")
                    console.print("🌐 Browser Containers:")
                    for item in browsers:
                        if isinstance(item, dict):
                            name = str(item.get("containerName") or item.get("name") or item.get("id") or "-")
                            running = bool(item.get("running"))
                            console.print(f"  - {name} ({'running' if running else 'stopped'})")
                console.print("")
                total = len(containers) + len(browsers)
                console.print(f"Total: {total} container(s)")
            success_count = payload.get("successCount")
            fail_count = payload.get("failCount")
            if isinstance(success_count, int) and isinstance(fail_count, int):
                console.print("")
                console.print(f"Done: {success_count} removed, {fail_count} failed")
                if success_count > 0:
                    console.print("")
                    console.print("Containers will be automatically recreated when the agent is next used.")
                return
            if payload.get("ok") is True:
                console.print("[green]✓[/green] Sandbox recreate requested.")
                operation = payload.get("operation")
                if isinstance(operation, dict):
                    if operation.get("all"):
                        console.print("scope: all")
                    elif operation.get("session"):
                        console.print(f"scope: session={operation.get('session')}")
                    elif operation.get("agent"):
                        console.print(f"scope: agent={operation.get('agent')}")
                    console.print(f"browser: {'yes' if bool(operation.get('browserOnly')) else 'no'}")
                    console.print(f"force: {'yes' if bool(operation.get('force')) else 'no'}")
                return
        _print_json(console, payload)

    @sandbox_app.command("explain")
    def sandbox_explain(
        session: str = typer.Option("", "--session"),
        agent: str = typer.Option("", "--agent"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        payload = _call_service(console, sandbox_service.explain, session, agent)
        if json_out:
            _print_json(console, payload)
            return
        if not isinstance(payload, dict):
            _print_json(console, payload)
            return
        _print_sandbox_explain(console, payload)

    security_app = typer.Typer(help="Security and scope controls")
    app.add_typer(security_app, name="security")

    @security_app.command("status")
    def security_status() -> None:
        _print_json(console, _call_service(console, security_service.status))

    @security_app.command("scopes-set")
    def security_scopes_set(scopes: str = typer.Option(..., "--scopes", help="Comma-separated scopes")) -> None:
        scope_list = _call_service(console, security_service.set_scopes, scopes)
        console.print(f"[green]✓[/green] gateway.rpc_default_scopes updated: {', '.join(scope_list)}")

    @security_app.command("token-rotate")
    def security_token_rotate(
        device_id: str = typer.Option("", "--device-id"),
        role: str = typer.Option("operator", "--role"),
        scopes: str = typer.Option("operator.read,operator.write", "--scopes"),
    ) -> None:
        _print_json(console, _call_service(console, security_service.rotate_token, device_id, role, scopes))

    @security_app.command("token-revoke")
    def security_token_revoke(device_id: str = typer.Option("", "--device-id")) -> None:
        _print_json(console, _call_service(console, security_service.revoke_token, device_id))

    @security_app.command("audit")
    def security_audit(
        deep: bool = typer.Option(False, "--deep"),
        fix: bool = typer.Option(False, "--fix"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        result: dict[str, Any]
        if fix:
            fix_result = _call_service(console, security_service.fix)
            report = _call_service(console, security_service.audit, deep)
            result = {"fix": fix_result, "report": report}
        else:
            result = _call_service(console, security_service.audit, deep)
        if json_out:
            _print_json(console, result)
            return
        report = result.get("report") if fix else result
        if not isinstance(report, dict):
            _print_json(console, result)
            return

        summary = report.get("summary", {})
        critical = int(summary.get("critical", 0)) if isinstance(summary, dict) else 0
        warn = int(summary.get("warn", 0)) if isinstance(summary, dict) else 0
        info = int(summary.get("info", 0)) if isinstance(summary, dict) else 0
        console.print("[bold]OpenClaw security audit[/bold]")
        console.print(f"[dim]Summary: {critical} critical · {warn} warn · {info} info[/dim]")
        if deep:
            console.print("[dim]Run with --json for machine-readable details.[/dim]")
        if fix and isinstance(result.get("fix"), dict):
            fix_payload = result.get("fix", {})
            changes = fix_payload.get("changes") if isinstance(fix_payload.get("changes"), list) else []
            console.print("")
            console.print("[bold]FIX[/bold]")
            if changes:
                for change in changes:
                    console.print(f"[dim]- {change}[/dim]")
            else:
                console.print("[dim]- no changes applied[/dim]")

        findings = report.get("findings", [])
        if not isinstance(findings, list):
            return
        for row in sorted(
            [x for x in findings if isinstance(x, dict)],
            key=lambda x: _severity_weight(str(x.get("severity", "info"))),
        ):
            severity = str(row.get("severity", "info")).upper()
            check_id = str(row.get("checkId", "unknown"))
            title = str(row.get("title", "")).strip() or str(row.get("detail", "")).strip()
            detail = str(row.get("detail", "")).strip()
            remediation = str(row.get("remediation", "")).strip()
            console.print("")
            console.print(f"[bold]{severity}[/bold] {check_id} {title}")
            if detail:
                console.print(f"  {detail}")
            if remediation:
                console.print(f"  [dim]Fix: {remediation}[/dim]")

    @security_app.command("fix")
    def security_fix() -> None:
        _print_json(console, _call_service(console, security_service.fix))

    acp_app = typer.Typer(help="ACP / RPC bridge commands")
    app.add_typer(acp_app, name="acp")

    @acp_app.command("connect")
    def acp_connect() -> None:
        _print_json(console, {"ok": True, "health": _call_service(console, acp_service.connect)})

    @acp_app.command("call")
    def acp_call(
        method: str = typer.Argument(..., help="RPC method name"),
        params_json: str = typer.Option("{}", "--params", help="JSON object"),
    ) -> None:
        _print_json(console, _call_service(console, acp_service.call, method, params_json))

    dns_app = typer.Typer(
        help="DNS 工具：解析主机名到 IP（lookup）、为内网域名生成 zone 配置与 Tailscale/CoreDNS 说明（setup）。"
    )
    app.add_typer(dns_app, name="dns")

    @dns_app.command("lookup", help="解析主机名，返回该主机对应的所有 IP 地址（JSON）。")
    def dns_lookup(host: str = typer.Argument(..., help="要解析的主机名或域名")) -> None:
        addrs = _call_service(console, dns_service.lookup, host)
        _print_json(console, {"host": host, "addresses": addrs})

    @dns_app.command(
        "setup",
        help="为指定内网域名生成 DNS zone 配置路径与 Tailscale/CoreDNS 使用说明；可选创建 dns 目录。",
    )
    def dns_setup(
        domain: str = typer.Option("openclaw.internal", "--domain", help="内网域名"),
        apply: bool = typer.Option(False, "--apply", help="是否创建 dns 目录"),
        dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="仅预览不写盘（默认开启）"),
    ) -> None:
        _print_json(console, _call_service(console, dns_service.setup, domain, apply, dry_run))
