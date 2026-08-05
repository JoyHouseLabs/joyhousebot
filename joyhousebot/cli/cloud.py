"""Cloud-role command line interface."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from typing import Any

import typer

from joyhousebot.config.loader import CONFIG_PATH_ENV

app = typer.Typer(help="Joyhousebot multi-user Agent cloud platform")


def _select_config(config: Path | None) -> None:
    """Make one explicit config selection visible to this role and its children."""
    if config is not None:
        os.environ[CONFIG_PATH_ENV] = str(config.expanduser().resolve())


ConfigOption = typer.Option(
    None,
    "--config",
    envvar=CONFIG_PATH_ENV,
    exists=True,
    file_okay=True,
    dir_okay=False,
    readable=True,
    resolve_path=True,
    help=f"Deployment config file (env: {CONFIG_PATH_ENV}).",
)


async def _release_worker_presence(service: Any) -> None:
    """Best-effort lease release before cancelling a systemd-managed service."""
    runtime = getattr(service, "runtime", None)
    store = getattr(runtime, "store", None)
    worker_id = getattr(runtime, "worker_id", None)
    unregister = getattr(store, "unregister_runtime_worker", None)
    if worker_id and callable(unregister):
        await asyncio.to_thread(unregister, worker_id)


async def _run_service_until_stopped(service: Any) -> None:
    """Turn SIGTERM into cooperative cancellation so worker leases are released.

    systemd sends SIGTERM during deploys.  Without a handler Python exits
    immediately, skipping the worker's ``finally`` block and leaving a live
    looking registration until the heartbeat lease expires.
    """
    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    registered_signals: list[signal.Signals] = []
    for value in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(value, stopping.set)
            registered_signals.append(value)
        except (NotImplementedError, RuntimeError):
            # Windows and embedded event loops still receive normal task
            # cancellation through asyncio.run; this is an optional upgrade.
            continue
    service_task = asyncio.create_task(service.run(), name="joyhousebot-service")
    stop_task = asyncio.create_task(stopping.wait(), name="joyhousebot-stop-signal")
    try:
        done, _ = await asyncio.wait(
            {service_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if service_task in done:
            await service_task
            return
        # Do this before cancellation. Systemd may apply a hard kill after a
        # short stop deadline, while this one tiny durable update is enough to
        # remove the process from capacity and rollout calculations at once.
        await _release_worker_presence(service)
        service_task.cancel()
        await asyncio.gather(service_task, return_exceptions=True)
    finally:
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        if not service_task.done():
            service_task.cancel()
            await asyncio.gather(service_task, return_exceptions=True)
        for value in registered_signals:
            loop.remove_signal_handler(value)


@app.command()
def api(
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(18790, "--port"),
    workers: int = typer.Option(1, "--workers", min=1),
    surface: str = typer.Option(
        "combined",
        "--surface",
        help="HTTP surface: public, control, or combined.",
    ),
    config: Path | None = ConfigOption,
) -> None:
    """Start stateless HTTP/SSE API replicas; no Agent execution occurs here."""
    import uvicorn

    _select_config(config)
    normalized_surface = surface.strip().lower()
    if normalized_surface not in {"combined", "public", "control"}:
        raise typer.BadParameter("surface must be public, control, or combined")
    os.environ["JOYHOUSEBOT_API_SURFACE"] = normalized_surface
    uvicorn.run(
        "joyhousebot.api.app:app",
        host=host,
        port=port,
        workers=workers,
        factory=False,
    )


@app.command()
def worker(config: Path | None = ConfigOption) -> None:
    """Start an Agent execution worker with no public network listener."""
    from joyhousebot.bootstrap.worker import build_execution_worker

    _select_config(config)
    try:
        asyncio.run(_run_service_until_stopped(build_execution_worker()))
    except KeyboardInterrupt:
        pass


@app.command()
def scheduler(config: Path | None = ConfigOption) -> None:
    """Start the distributed schedule and DAG maintenance worker."""
    from joyhousebot.bootstrap.worker import build_scheduler_worker

    _select_config(config)
    try:
        asyncio.run(_run_service_until_stopped(build_scheduler_worker()))
    except KeyboardInterrupt:
        pass


@app.command("channel-worker")
def channel_worker(config: Path | None = ConfigOption) -> None:
    """Start distributed channel connectors; model execution stays in workers."""
    from joyhousebot.bootstrap.worker import build_channel_worker

    _select_config(config)
    try:
        asyncio.run(_run_service_until_stopped(build_channel_worker()))
    except KeyboardInterrupt:
        pass


@app.command()
def check(config: Path | None = ConfigOption) -> None:
    """Validate configuration and database readiness."""
    from joyhousebot.config.access import get_config
    from joyhousebot.storage.factory import create_runtime_store

    _select_config(config)
    store = create_runtime_store(get_config())
    try:
        result = store.healthcheck()
        if not result.get("ok"):
            raise typer.Exit(1)
        typer.echo("ready")
    finally:
        store.close()


if __name__ == "__main__":
    app()
