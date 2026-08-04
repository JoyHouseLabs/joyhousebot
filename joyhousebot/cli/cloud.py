"""Cloud-role command line interface."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

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
        asyncio.run(build_execution_worker().run())
    except KeyboardInterrupt:
        pass


@app.command()
def scheduler(config: Path | None = ConfigOption) -> None:
    """Start the distributed schedule and DAG maintenance worker."""
    from joyhousebot.bootstrap.worker import build_scheduler_worker

    _select_config(config)
    try:
        asyncio.run(build_scheduler_worker().run())
    except KeyboardInterrupt:
        pass


@app.command("channel-worker")
def channel_worker(config: Path | None = ConfigOption) -> None:
    """Start distributed channel connectors; model execution stays in workers."""
    from joyhousebot.bootstrap.worker import build_channel_worker

    _select_config(config)
    try:
        asyncio.run(build_channel_worker().run())
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
