"""Cron command group extracted from legacy commands.py."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table


def register_cron_commands(app: typer.Typer, console: Console) -> None:
    """Register cron command group."""
    cron_app = typer.Typer(help="Manage scheduled tasks")
    app.add_typer(cron_app, name="cron")

    @cron_app.command("list")
    def cron_list(
        all: bool = typer.Option(False, "--all", "-a", help="Include disabled jobs"),
    ) -> None:
        """List scheduled jobs."""
        from joyhousebot.config.loader import get_data_dir
        from joyhousebot.cron.service import CronService

        store_path = get_data_dir() / "cron" / "jobs.json"
        service = CronService(store_path)
        jobs = service.list_jobs(include_disabled=all)
        if not jobs:
            console.print("No scheduled jobs.")
            return

        table = Table(title="Scheduled Jobs")
        table.add_column("ID", style="cyan")
        table.add_column("Name")
        table.add_column("Schedule")
        table.add_column("Status")
        table.add_column("Next Run")

        import time

        for job in jobs:
            if job.schedule.kind == "every":
                sched = f"every {(job.schedule.every_ms or 0) // 1000}s"
            elif job.schedule.kind == "cron":
                sched = job.schedule.expr or ""
            else:
                sched = "one-time"
            next_run = ""
            if job.state.next_run_at_ms:
                next_run = time.strftime("%Y-%m-%d %H:%M", time.localtime(job.state.next_run_at_ms / 1000))
            status = "[green]enabled[/green]" if job.enabled else "[dim]disabled[/dim]"
            table.add_row(job.id, job.name, sched, status, next_run)
        console.print(table)

    @cron_app.command("add")
    def cron_add(
        name: str = typer.Option(..., "--name", "-n", help="Job name"),
        message: str = typer.Option("", "--message", "-m", help="Message for agent (optional when --kind memory_compaction)"),
        every: int = typer.Option(None, "--every", "-e", help="Run every N seconds"),
        cron_expr: str = typer.Option(None, "--cron", "-c", help="Cron expression (e.g. '0 9 * * *')"),
        at: str = typer.Option(None, "--at", help="Run once at time (ISO format)"),
        deliver: bool = typer.Option(False, "--deliver", "-d", help="Deliver response to channel"),
        to: str = typer.Option(None, "--to", help="Recipient for delivery"),
        channel: str = typer.Option(None, "--channel", help="Channel for delivery (e.g. 'telegram', 'whatsapp')"),
        kind: str = typer.Option("agent_turn", "--kind", "-k", help="Payload kind: agent_turn (send message to agent) or memory_compaction (L2->L1->L0)"),
    ) -> None:
        """Add a scheduled job."""
        from joyhousebot.config.loader import get_data_dir
        from joyhousebot.cron.service import CronService
        from joyhousebot.cron.types import CronSchedule

        if every:
            schedule = CronSchedule(kind="every", every_ms=every * 1000)
        elif cron_expr:
            schedule = CronSchedule(kind="cron", expr=cron_expr)
        elif at:
            import datetime

            dt = datetime.datetime.fromisoformat(at)
            schedule = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
        else:
            console.print("[red]Error: Must specify --every, --cron, or --at[/red]")
            raise typer.Exit(1)

        payload_kind = "memory_compaction" if (kind or "").strip().lower() == "memory_compaction" else "agent_turn"
        if payload_kind == "agent_turn" and not (message or "").strip():
            console.print("[red]Error: --message is required when kind is agent_turn[/red]")
            raise typer.Exit(1)

        store_path = get_data_dir() / "cron" / "jobs.json"
        service = CronService(store_path)
        job = service.add_job(
            name=name,
            schedule=schedule,
            message=message or "",
            deliver=deliver,
            to=to,
            channel=channel,
            payload_kind=payload_kind,
        )
        console.print(f"[green]✓[/green] Added job '{job.name}' ({job.id})")

    @cron_app.command("remove")
    def cron_remove(
        job_id: str = typer.Argument(..., help="Job ID to remove"),
    ) -> None:
        """Remove a scheduled job."""
        from joyhousebot.config.loader import get_data_dir
        from joyhousebot.cron.service import CronService

        store_path = get_data_dir() / "cron" / "jobs.json"
        service = CronService(store_path)
        if service.remove_job(job_id):
            console.print(f"[green]✓[/green] Removed job {job_id}")
        else:
            console.print(f"[red]Job {job_id} not found[/red]")

    @cron_app.command("enable")
    def cron_enable(
        job_id: str = typer.Argument(..., help="Job ID"),
        disable: bool = typer.Option(False, "--disable", help="Disable instead of enable"),
    ) -> None:
        """Enable or disable a job."""
        from joyhousebot.config.loader import get_data_dir
        from joyhousebot.cron.service import CronService

        store_path = get_data_dir() / "cron" / "jobs.json"
        service = CronService(store_path)
        job = service.enable_job(job_id, enabled=not disable)
        if job:
            status = "disabled" if disable else "enabled"
            console.print(f"[green]✓[/green] Job '{job.name}' {status}")
        else:
            console.print(f"[red]Job {job_id} not found[/red]")

    @cron_app.command("run")
    def cron_run(
        job_id: str = typer.Argument(..., help="Job ID to run"),
        force: bool = typer.Option(False, "--force", "-f", help="Run even if disabled"),
    ) -> None:
        """Manually run a job."""
        from joyhousebot.config.loader import get_data_dir
        from joyhousebot.cron.service import CronService

        store_path = get_data_dir() / "cron" / "jobs.json"
        service = CronService(store_path)

        async def run() -> bool:
            return await service.run_job(job_id, force=force)

        if asyncio.run(run()):
            console.print("[green]✓[/green] Job executed")
        else:
            console.print(f"[red]Failed to run job {job_id}[/red]")

