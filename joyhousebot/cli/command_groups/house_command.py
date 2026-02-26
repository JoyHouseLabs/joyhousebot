"""House command group extracted from legacy commands.py."""

from __future__ import annotations

import asyncio
import hashlib
import json
import platform
import socket
import time
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import typer
from rich.console import Console
from rich.table import Table


def _get_house_identity_key_path() -> Path:
    """Path to Ed25519 house identity key (auth only, not for assets)."""
    from joyhousebot.config.loader import get_data_dir

    return get_data_dir() / "keys" / "house_identity_ed25519.hex"


def _machine_fingerprint() -> str:
    raw = "|".join(
        [
            socket.gethostname(),
            platform.platform(),
            str(Path.home()),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_task_prompt(task_type: str, payload: dict) -> str:
    """Translate task payload into an agent prompt."""
    direct = payload.get("message")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    if task_type in {"agent.prompt", "agent.message"}:
        return json.dumps(payload, ensure_ascii=False)
    return (
        f"Execute cloud/local task.\n"
        f"task_type: {task_type}\n"
        f"task_payload:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _format_control_plane_exception(exc: Exception) -> tuple[str, str]:
    from joyhousebot.control_plane import ControlPlaneClientError

    if isinstance(exc, ControlPlaneClientError):
        level = "yellow" if exc.retryable else "red"
        status_suffix = f", status={exc.status_code}" if exc.status_code is not None else ""
        retry_label = "retryable" if exc.retryable else "non-retryable"
        detail = f"{exc.code}{status_suffix} ({retry_label}): {exc}"
        return level, detail
    return "yellow", str(exc)


def _print_control_plane_warning(console: Console, prefix: str, exc: Exception) -> None:
    level, detail = _format_control_plane_exception(exc)
    console.print(f"[{level}]{prefix}:[/{level}] {detail}")


def _next_control_plane_backoff_seconds(
    exc: Exception,
    *,
    retryable_base: float,
    non_retryable_base: float,
) -> float:
    from joyhousebot.control_plane import ControlPlaneClientError

    if isinstance(exc, ControlPlaneClientError):
        return max(0.2, retryable_base if exc.retryable else non_retryable_base)
    return max(0.2, retryable_base)


def _resolve_control_plane_backoff_policy(config: Any) -> dict[str, float]:
    gateway = getattr(config, "gateway", None)
    claim_retryable = float(
        getattr(gateway, "control_plane_claim_retryable_backoff_seconds", 3.0) if gateway else 3.0
    )
    claim_non_retryable = float(
        getattr(gateway, "control_plane_claim_non_retryable_backoff_seconds", 15.0) if gateway else 15.0
    )
    heartbeat_retryable = float(
        getattr(gateway, "control_plane_heartbeat_retryable_backoff_seconds", 5.0) if gateway else 5.0
    )
    heartbeat_non_retryable = float(
        getattr(gateway, "control_plane_heartbeat_non_retryable_backoff_seconds", 30.0) if gateway else 30.0
    )
    return {
        "claim_retryable": max(0.2, claim_retryable),
        "claim_non_retryable": max(0.2, claim_non_retryable),
        "heartbeat_retryable": max(0.2, heartbeat_retryable),
        "heartbeat_non_retryable": max(0.2, heartbeat_non_retryable),
    }


def register_house_commands(app: typer.Typer, console: Console, make_provider: Callable[[Any], Any]) -> None:
    """Register house and house tasks command groups."""
    house_app = typer.Typer(
        help="Local house identity and task state (a house can have multiple agents, tools, skills)"
    )
    app.add_typer(house_app, name="house")
    house_tasks_app = typer.Typer(help="Manage local house task queue")
    house_app.add_typer(house_tasks_app, name="tasks")

    @house_app.command("init")
    def house_init() -> None:
        """Initialize local house identity and SQLite state."""
        from joyhousebot.identity import ensure_bot_identity
        from joyhousebot.storage import LocalStateStore

        store = LocalStateStore.default()
        identity = ensure_bot_identity(_get_house_identity_key_path())
        existing = store.get_identity()
        house_id = existing.house_id if existing else None
        status = existing.status if existing else "local_only"
        store.upsert_identity(
            identity_public_key=identity.public_key_hex,
            house_id=house_id,
            status=status,
            access_token=existing.access_token if existing else None,
            refresh_token=existing.refresh_token if existing else None,
            ws_url=existing.ws_url if existing else None,
            server_url=existing.server_url if existing else None,
        )
        console.print("[green]✓[/green] Local SQLite state initialized")
        console.print(f"Identity public key: [cyan]{identity.public_key_hex}[/cyan]")
        console.print(f"Key path: [dim]{_get_house_identity_key_path()}[/dim]")

    @house_app.command("identity")
    def house_identity() -> None:
        """Show local house identity and registration status."""
        from joyhousebot.identity import ensure_bot_identity
        from joyhousebot.storage import LocalStateStore

        identity = ensure_bot_identity(_get_house_identity_key_path())
        record = LocalStateStore.default().get_identity()
        table = Table(title="Local House Identity")
        table.add_column("Field", style="cyan")
        table.add_column("Value")
        table.add_row("identity_public_key", identity.public_key_hex)
        table.add_row("house_id", record.house_id if record and record.house_id else "[dim](not registered)[/dim]")
        table.add_row("status", record.status if record else "local_only")
        table.add_row("ws_url", record.ws_url if record and record.ws_url else "[dim](none)[/dim]")
        table.add_row("server_url", record.server_url if record and record.server_url else "[dim](none)[/dim]")
        table.add_row("sqlite", str(LocalStateStore.default().db_path))
        console.print(table)

    @house_app.command("register")
    def house_register(
        server: str = typer.Option("http://127.0.0.1:8000", "--server", help="Backend base URL"),
        name: str = typer.Option("joyhouse-local", "--name", help="House name"),
        user_id: str = typer.Option(
            "",
            "--user-id",
            "--owner-user-id",
            help="绑定到后端用户 UUID，注册后该用户可在「我的 House」中查看",
        ),
    ) -> None:
        """Register this local house to backend using Ed25519 signature."""
        from joyhousebot.control_plane import ControlPlaneClient
        from joyhousebot.identity import ensure_bot_identity, sign_bot_challenge
        from joyhousebot.storage import LocalStateStore

        store = LocalStateStore.default()
        identity = ensure_bot_identity(_get_house_identity_key_path())
        challenge = f"register:{identity.public_key_hex}:{int(time.time())}"
        signature = sign_bot_challenge(identity.private_key_hex, challenge)
        client = ControlPlaneClient(server)
        data = client.register_house(
            house_name=name,
            machine_fingerprint=_machine_fingerprint(),
            identity_public_key=identity.public_key_hex,
            challenge=challenge,
            signature=signature,
            capabilities=["task.execute.v1", "task.report.v1", "skills.sync.v1"],
            feature_flags=["sqlite-local-state", "ed25519-identity"],
            owner_user_id=user_id.strip() or None,
        )
        house_id = str(data.get("house_id", ""))
        if not house_id:
            raise typer.BadParameter("register response missing house_id")
        store.upsert_identity(
            identity_public_key=identity.public_key_hex,
            house_id=house_id,
            status="registered",
            access_token=str(data.get("access_token") or "") or None,
            refresh_token=str(data.get("refresh_token") or "") or None,
            ws_url=str(data.get("ws_url") or "") or None,
            server_url=server,
        )
        console.print("[green]✓[/green] Registered house successfully")
        console.print(f"house_id: [cyan]{house_id}[/cyan]")
        if data.get("ws_url"):
            console.print(f"ws_url: [dim]{data['ws_url']}[/dim]")

    @house_app.command("bind")
    def house_bind(
        user_id: str = typer.Argument(..., help="User UUID to bind to (can be copied from \"My House\" page)"),
        server: str = typer.Option("http://127.0.0.1:8000", "--server", help="Backend base URL"),
    ) -> None:
        """Bind current house to user."""
        from joyhousebot.control_plane import ControlPlaneClient
        from joyhousebot.storage import LocalStateStore

        store = LocalStateStore.default()
        identity = store.get_identity()
        if not identity or not identity.house_id:
            raise typer.BadParameter("House is not registered. Please run first: joyhousebot house register --server <url>")
        house_id = identity.house_id
        user_id = user_id.strip()
        if not user_id:
            raise typer.BadParameter("Please provide user_id (user UUID)")
        client = ControlPlaneClient(server)
        try:
            house = client.get_house(house_id)
        except Exception as e:
            _print_control_plane_warning(console, "Failed to get house information", e)
            raise typer.Exit(1) from e
        owner = house.get("owner_user_id") or house.get("ownerUserId")
        if owner:
            console.print("[red]This house has already been bound to a user and cannot be bound again.[/red]")
            console.print(f"Current binding: [dim]{owner}[/dim]")
            raise typer.Exit(1)
        try:
            client.bind_house(house_id=house_id, owner_user_id=user_id)
        except Exception as e:
            _print_control_plane_warning(console, "Binding failed", e)
            raise typer.Exit(1) from e
        console.print("[green]✓[/green] Bound to user")
        console.print(f"house_id: [cyan]{house_id}[/cyan]")
        console.print(f"user_id:  [cyan]{user_id}[/cyan]")

    @house_app.command("worker")
    def bot_worker(
        server: str = typer.Option("http://127.0.0.1:8000", "--server", help="Backend base URL"),
        poll_interval: float = typer.Option(5.0, "--poll-interval", help="Seconds between polling cycles"),
        heartbeat_interval: int = typer.Option(30, "--heartbeat-interval", help="Seconds between heartbeats"),
        ws_enabled: bool = typer.Option(True, "--ws/--no-ws", help="Enable websocket realtime task ingest"),
        concurrency: int = typer.Option(1, "--concurrency", "-c", help="Max concurrent task executions"),
        max_retries: int = typer.Option(3, "--max-retries", help="Max automatic retries per task"),
        retry_backoff_base: int = typer.Option(5, "--retry-backoff-base", help="Base backoff seconds"),
        run_once: bool = typer.Option(False, "--run-once", help="Run one cycle then exit"),
    ) -> None:
        """Run local worker loop."""
        from joyhousebot.agent.loop import AgentLoop
        from joyhousebot.bus.queue import MessageBus
        from joyhousebot.config.loader import load_config
        from joyhousebot.control_plane import ControlPlaneClient
        from joyhousebot.storage import LocalStateStore

        store = LocalStateStore.default()
        identity = store.get_identity()
        if not identity or not identity.house_id:
            raise typer.BadParameter("House is not registered. Run: joyhousebot house register --server <url>")
        if concurrency < 1:
            raise typer.BadParameter("--concurrency must be >= 1")

        house_id = identity.house_id
        client = ControlPlaneClient(server)
        config = load_config()
        default_model, default_fallbacks = config.get_agent_model_and_fallbacks(None)
        cp_backoff = _resolve_control_plane_backoff_policy(config)

        async def _run() -> None:
            cp_status: dict[str, Any] = {
                "running": True,
                "houseId": house_id,
                "server": server,
                "wsEnabled": bool(ws_enabled),
                "startedAtMs": int(time.time() * 1000),
                "updatedAtMs": int(time.time() * 1000),
                "lastHeartbeatMs": None,
                "lastClaimMs": None,
                "lastHeartbeatError": None,
                "lastClaimError": None,
                "heartbeatBackoffUntilMs": 0,
                "claimBackoffUntilMs": 0,
            }

            def _persist_cp_status(patch: dict[str, Any] | None = None) -> None:
                if patch:
                    cp_status.update(patch)
                cp_status["updatedAtMs"] = int(time.time() * 1000)
                try:
                    store.set_sync_json(name="control_plane.worker_status", value=cp_status)
                except Exception:
                    pass

            _persist_cp_status()
            console.print(
                f"[green]✓[/green] Worker started for house_id: [cyan]{house_id}[/cyan] "
                f"(concurrency={concurrency})"
            )
            stop_event = asyncio.Event()
            ws_task: asyncio.Task | None = None
            running: set[asyncio.Task] = set()

            def _on_ws_task(payload: dict) -> None:
                normalized = client.normalize_task(payload)
                cloud_task_id = str(normalized.get("task_id", "")).strip()
                if not cloud_task_id:
                    return
                task_input = normalized.get("input", {})
                if not isinstance(task_input, dict):
                    task_input = {}
                store.enqueue_task(
                    task_id=cloud_task_id,
                    source="cloud",
                    task_type=str(normalized.get("task_type", "unknown")),
                    task_version=str(normalized.get("task_version", "1.0")),
                    payload=task_input,
                    priority=40,
                )

            if ws_enabled and identity.access_token and identity.ws_url:
                abs_ws_url = client.build_ws_url(
                    ws_path=identity.ws_url,
                    token=identity.access_token,
                    server_url=identity.server_url or server,
                )
                _persist_cp_status({"wsUrl": abs_ws_url, "wsActive": True})
                ws_task = asyncio.create_task(
                    client.run_ws_listener(
                        ws_url=abs_ws_url,
                        on_task=_on_ws_task,
                        stop_event=stop_event,
                    )
                )
                console.print(f"[green]✓[/green] WebSocket listener enabled: [dim]{abs_ws_url}[/dim]")
            elif ws_enabled:
                console.print("[yellow]WebSocket disabled automatically: missing access_token/ws_url[/yellow]")
                _persist_cp_status({"wsActive": False, "wsDisabledReason": "missing_access_token_or_ws_url"})

            async def _execute_task(task: Any) -> None:
                bus = MessageBus()
                provider = make_provider(config)
                transcribe_provider = None
                if config.providers.groq.api_key:
                    from joyhousebot.providers.transcription import GroqTranscriptionProvider
                    transcribe_provider = GroqTranscriptionProvider(api_key=config.providers.groq.api_key)
                agent_loop = AgentLoop(
                    bus=bus,
                    provider=provider,
                    workspace=config.workspace_path,
                    model=default_model,
                    model_fallbacks=default_fallbacks,
                    temperature=config.agents.defaults.temperature,
                    max_tokens=config.agents.defaults.max_tokens,
                    max_iterations=config.agents.defaults.max_tool_iterations,
                    memory_window=config.agents.defaults.memory_window,
                    max_context_tokens=getattr(config.agents.defaults, "max_context_tokens", None),
                    brave_api_key=config.tools.web.search.api_key or None,
                    exec_config=config.tools.exec,
                    restrict_to_workspace=config.tools.restrict_to_workspace,
                    mcp_servers=config.tools.mcp_servers,
                    config=config,
                    transcribe_provider=transcribe_provider,
                )
                prompt = _build_task_prompt(task.task_type, task.payload)
                try:
                    if task.source == "cloud":
                        client.report_task_progress(
                            house_id=house_id,
                            task_id=task.task_id,
                            progress=0.05,
                            detail="task started",
                        )
                    response = await agent_loop.process_direct(
                        prompt,
                        session_key=f"bot-task:{task.task_id}",
                        channel="cli",
                        chat_id="bot-worker",
                    )
                    store.update_task_status(task_id=task.task_id, status="completed")
                    store.log_task_event(
                        task_id=task.task_id,
                        event="result",
                        detail={"response_preview": (response or "")[:500]},
                    )
                    if task.source == "cloud":
                        client.report_task_result(
                            house_id=house_id,
                            task_id=task.task_id,
                            result={
                                "output": response or "",
                                "task_type": task.task_type,
                                "completed_at": int(time.time()),
                            },
                        )
                    console.print(f"[green]✓[/green] Completed task: [cyan]{task.task_id}[/cyan]")
                except Exception as exc:
                    error = {
                        "code": "TASK_EXECUTION_ERROR",
                        "message": str(exc),
                        "retryable": True,
                    }
                    if task.retry_count < max_retries:
                        next_retry_idx = task.retry_count + 1
                        delay = retry_backoff_base * (2 ** (next_retry_idx - 1))
                        store.requeue_with_backoff(
                            task_id=task.task_id,
                            retry_increment=1,
                            delay_seconds=delay,
                            error=error,
                        )
                        if task.source == "cloud":
                            try:
                                client.report_task_progress(
                                    house_id=house_id,
                                    task_id=task.task_id,
                                    progress=0.0,
                                    detail=f"retry scheduled in {delay}s",
                                )
                            except Exception as cp_exc:
                                _print_control_plane_warning(console, "report progress warning", cp_exc)
                        console.print(
                            f"[yellow]↺[/yellow] Task retry scheduled: {task.task_id} "
                            f"(attempt {next_retry_idx}/{max_retries}, in {delay}s)"
                        )
                    else:
                        store.update_task_status(task_id=task.task_id, status="failed", error=error)
                        if task.source == "cloud":
                            try:
                                client.report_task_failure(house_id=house_id, task_id=task.task_id, error=error)
                            except Exception as cp_exc:
                                _print_control_plane_warning(console, "report failure warning", cp_exc)
                        console.print(f"[red]✗[/red] Task failed: {task.task_id} -> {exc}")
                finally:
                    await agent_loop.close_mcp()

            last_heartbeat = 0.0
            heartbeat_backoff_until = 0.0
            claim_backoff_until = 0.0
            try:
                while True:
                    did_work = False
                    running = {t for t in running if not t.done()}
                    now = time.time()
                    if now >= heartbeat_backoff_until and now - last_heartbeat >= heartbeat_interval:
                        try:
                            client.heartbeat(
                                house_id=house_id,
                                status="online",
                                metrics={"poll_interval": poll_interval},
                            )
                            last_heartbeat = now
                            heartbeat_backoff_until = 0.0
                            _persist_cp_status(
                                {
                                    "lastHeartbeatMs": int(now * 1000),
                                    "lastHeartbeatError": None,
                                    "heartbeatBackoffUntilMs": 0,
                                }
                            )
                        except Exception as exc:
                            _print_control_plane_warning(console, "heartbeat warning", exc)
                            heartbeat_backoff_until = now + _next_control_plane_backoff_seconds(
                                exc,
                                retryable_base=cp_backoff["heartbeat_retryable"],
                                non_retryable_base=cp_backoff["heartbeat_non_retryable"],
                            )
                            _persist_cp_status(
                                {
                                    "lastHeartbeatError": str(exc),
                                    "heartbeatBackoffUntilMs": int(heartbeat_backoff_until * 1000),
                                }
                            )

                    if now >= claim_backoff_until:
                        try:
                            claimed = client.claim_task(house_id=house_id)
                            claim_backoff_until = 0.0
                            _persist_cp_status(
                                {"lastClaimMs": int(now * 1000), "lastClaimError": None, "claimBackoffUntilMs": 0}
                            )
                            if claimed:
                                normalized_claimed = client.normalize_task(claimed)
                                cloud_task_id = str(normalized_claimed.get("task_id", "")).strip()
                                if cloud_task_id:
                                    store.enqueue_task(
                                        task_id=cloud_task_id,
                                        source="cloud",
                                        task_type=str(normalized_claimed.get("task_type", "unknown")),
                                        task_version=str(normalized_claimed.get("task_version", "1.0")),
                                        payload=(
                                            normalized_claimed.get("input", {})
                                            if isinstance(normalized_claimed.get("input"), dict)
                                            else {}
                                        ),
                                        priority=50,
                                    )
                                    did_work = True
                        except Exception as exc:
                            _print_control_plane_warning(console, "claim warning", exc)
                            claim_backoff_until = now + _next_control_plane_backoff_seconds(
                                exc,
                                retryable_base=cp_backoff["claim_retryable"],
                                non_retryable_base=cp_backoff["claim_non_retryable"],
                            )
                            _persist_cp_status(
                                {
                                    "lastClaimError": str(exc),
                                    "claimBackoffUntilMs": int(claim_backoff_until * 1000),
                                }
                            )

                    while len(running) < concurrency:
                        task = store.pop_next_queued_task()
                        if not task:
                            break
                        did_work = True
                        t = asyncio.create_task(_execute_task(task))
                        running.add(t)
                    if run_once:
                        break
                    if not did_work and not running:
                        await asyncio.sleep(max(0.2, poll_interval))
                    else:
                        await asyncio.sleep(0.05)
            finally:
                _persist_cp_status({"running": False, "stoppedAtMs": int(time.time() * 1000)})
                stop_event.set()
                if ws_task:
                    try:
                        await asyncio.wait_for(ws_task, timeout=2.0)
                    except Exception:
                        ws_task.cancel()
                if running:
                    await asyncio.gather(*running, return_exceptions=True)

        asyncio.run(_run())

    @house_tasks_app.command("list")
    def bot_tasks_list(
        status: str = typer.Option(None, "--status", help="Filter by status"),
        limit: int = typer.Option(50, "--limit", help="Max rows"),
    ) -> None:
        """List tasks in local SQLite queue."""
        from joyhousebot.storage import LocalStateStore

        store = LocalStateStore.default()
        tasks = store.list_tasks(limit=limit, status=status)
        if not tasks:
            console.print("No tasks.")
            return
        table = Table(title="Local Task Queue")
        table.add_column("Task ID", style="cyan")
        table.add_column("Source")
        table.add_column("Type")
        table.add_column("Status")
        table.add_column("Retry")
        table.add_column("Next Retry")
        table.add_column("Updated")
        for t in tasks:
            next_retry = (t.next_retry_at or "")[:19] if t.next_retry_at else "-"
            table.add_row(t.task_id, t.source, t.task_type, t.status, str(t.retry_count), next_retry, t.updated_at[:19])
        console.print(table)

    @house_tasks_app.command("add-local")
    def bot_tasks_add_local(
        task_type: str = typer.Option(..., "--type", help="Task type"),
        payload: str = typer.Option("{}", "--payload", help="JSON payload"),
        priority: int = typer.Option(100, "--priority", help="Queue priority, lower runs first"),
    ) -> None:
        """Create a local task and store it in SQLite."""
        from joyhousebot.storage import LocalStateStore

        try:
            payload_dict = json.loads(payload) if payload else {}
            if not isinstance(payload_dict, dict):
                raise ValueError("payload must be a JSON object")
        except Exception as exc:
            raise typer.BadParameter(f"Invalid --payload JSON: {exc}") from exc
        store = LocalStateStore.default()
        task_id = f"local_{uuid4().hex}"
        store.enqueue_task(
            task_id=task_id,
            source="local",
            task_type=task_type,
            task_version="1.0",
            payload=payload_dict,
            priority=priority,
        )
        console.print(f"[green]✓[/green] Local task queued: [cyan]{task_id}[/cyan]")

    @house_tasks_app.command("retry")
    def bot_tasks_retry(task_id: str = typer.Argument(..., help="Task ID")) -> None:
        """Mark task as queued again and increment retry count."""
        from joyhousebot.storage import LocalStateStore

        store = LocalStateStore.default()
        store.update_task_status(task_id=task_id, status="queued", retry_increment=1)
        console.print(f"[green]✓[/green] Task re-queued: [cyan]{task_id}[/cyan]")

