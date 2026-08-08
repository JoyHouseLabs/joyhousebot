"""Cloud-role command line interface."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from datetime import datetime, timezone
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
    from joyhousebot.observability.otel import configure_telemetry

    _select_config(config)
    configure_telemetry(service_name="joyhousebot-worker")
    try:
        asyncio.run(_run_service_until_stopped(build_execution_worker()))
    except KeyboardInterrupt:
        pass


@app.command()
def scheduler(config: Path | None = ConfigOption) -> None:
    """Start the distributed schedule and DAG maintenance worker."""
    from joyhousebot.bootstrap.worker import build_scheduler_worker
    from joyhousebot.observability.otel import configure_telemetry

    _select_config(config)
    configure_telemetry(service_name="joyhousebot-scheduler")
    try:
        asyncio.run(_run_service_until_stopped(build_scheduler_worker()))
    except KeyboardInterrupt:
        pass


@app.command("channel-worker")
def channel_worker(config: Path | None = ConfigOption) -> None:
    """Start distributed channel connectors; model execution stays in workers."""
    from joyhousebot.bootstrap.worker import build_channel_worker
    from joyhousebot.observability.otel import configure_telemetry

    _select_config(config)
    configure_telemetry(service_name="joyhousebot-channel-worker")
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


def _default_eval_suite_directory() -> Path:
    candidates = (
        Path.cwd() / "evals" / "suites",
        Path(__file__).resolve().parents[2] / "evals" / "suites",
        Path(__file__).resolve().parent.parent / "eval_suites",
    )
    return next((path for path in candidates if path.is_dir()), candidates[0])


@app.command("eval-bootstrap")
def eval_bootstrap(
    suite_dir: Path | None = typer.Option(
        None,
        "--suite-dir",
        exists=True,
        file_okay=False,
        readable=True,
        resolve_path=True,
    ),
    actor: str = typer.Option("eval-bootstrap", "--actor"),
    config: Path | None = ConfigOption,
) -> None:
    """Install checked-in immutable business Eval suites."""
    from joyhousebot.bootstrap.container import build_api_container

    _select_config(config)
    directory = suite_dir or _default_eval_suite_directory()
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise typer.BadParameter(f"no Eval suite JSON files found in {directory}")

    async def install() -> None:
        container = build_api_container()
        try:
            existing = {
                (str(item["suite_id"]), int(item["version"]))
                for item in await container.evals.list_suites()
            }
            for path in paths:
                value = json.loads(path.read_text(encoding="utf-8"))
                identity = (str(value.get("suite_id") or ""), int(value.get("version") or 0))
                if identity in existing:
                    typer.echo(f"exists {identity[0]}@{identity[1]}")
                    continue
                await container.evals.save_suite(value, actor_id=actor)
                typer.echo(f"installed {identity[0]}@{identity[1]}")
        finally:
            await container.close()

    asyncio.run(install())


@app.command("eval-execute")
def eval_execute(
    eval_run_id: str = typer.Argument(...),
    max_concurrency: int = typer.Option(4, "--max-concurrency", min=1, max=16),
    case_timeout_seconds: float = typer.Option(300.0, "--case-timeout", min=1, max=3600),
    actor: str = typer.Option("eval-cli", "--actor"),
    config: Path | None = ConfigOption,
) -> None:
    """Execute/resume a durable Eval run while normal workers process cases."""
    from joyhousebot.bootstrap.container import build_api_container

    _select_config(config)

    async def execute() -> None:
        container = build_api_container()
        try:
            result = await container.eval_execution.execute(
                eval_run_id,
                actor_id=actor,
                max_concurrency=max_concurrency,
                case_timeout_seconds=case_timeout_seconds,
            )
            typer.echo(json.dumps(result, ensure_ascii=False, sort_keys=True))
            if result.get("status") != "passed":
                raise typer.Exit(1)
        finally:
            await container.close()

    asyncio.run(execute())


def _write_json_report(value: dict[str, Any], output: Path | None, *, prefix: str) -> Path:
    target = output or (
        Path.cwd()
        / "artifacts"
        / "drills"
        / f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    target = target.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


@app.command("durability-drill")
def durability_drill(
    confirm: str = typer.Option("", "--confirm"),
    task_count: int = typer.Option(100, "--tasks", min=1, max=5000),
    claim_concurrency: int = typer.Option(8, "--claim-concurrency", min=1, max=32),
    keep_records: bool = typer.Option(False, "--keep-records"),
    output: Path | None = typer.Option(None, "--output", dir_okay=False),
    config: Path | None = ConfigOption,
) -> None:
    """Write synthetic rows and prove PG claim, takeover, and fencing behavior."""
    from joyhousebot.config.access import get_config
    from joyhousebot.operations import DurabilityDrill
    from joyhousebot.storage.factory import create_runtime_store

    if confirm != "WRITE_SYNTHETIC_RUNTIME_DATA":
        raise typer.BadParameter(
            "pass --confirm WRITE_SYNTHETIC_RUNTIME_DATA after selecting the target database"
        )
    _select_config(config)
    store = create_runtime_store(get_config())
    try:
        result = asyncio.run(
            DurabilityDrill(store).run(
                task_count=task_count,
                claim_concurrency=claim_concurrency,
                cleanup=not keep_records,
            )
        )
    finally:
        store.close()
    report = _write_json_report(result, output, prefix="durability")
    typer.echo(f"report {report}")
    typer.echo("PASS" if result["passed"] else "FAIL")
    if not result["passed"]:
        raise typer.Exit(1)


@app.command("load-test")
def load_test(
    base_url: str = typer.Option("http://127.0.0.1:18790", "--base-url"),
    agent_id: str = typer.Option("default", "--agent-id"),
    count: int = typer.Option(20, "--count", min=1, max=10000),
    concurrency: int = typer.Option(4, "--concurrency", min=1, max=256),
    wait_for_terminal: bool = typer.Option(True, "--wait/--submit-only"),
    timeout_seconds: float = typer.Option(180.0, "--timeout", min=1, max=3600),
    min_accept_rate: float = typer.Option(0.995, "--min-accept-rate", min=0, max=1),
    min_completion_rate: float = typer.Option(
        0.99, "--min-completion-rate", min=0, max=1
    ),
    min_success_rate: float = typer.Option(0.95, "--min-success-rate", min=0, max=1),
    max_submit_p95_ms: float = typer.Option(1000.0, "--max-submit-p95-ms", min=1),
    max_e2e_p95_ms: float = typer.Option(120000.0, "--max-e2e-p95-ms", min=1),
    output: Path | None = typer.Option(None, "--output", dir_okay=False),
) -> None:
    """Rehearse authenticated API load and emit an SLO acceptance report."""
    from joyhousebot.operations import LoadTestOptions, run_api_load_test

    token = str(os.getenv("JOYHOUSEBOT_LOAD_TOKEN") or "").strip()
    if not token:
        raise typer.BadParameter(
            "JOYHOUSEBOT_LOAD_TOKEN is required; use a scoped runs.read/runs.write service token"
        )
    result = asyncio.run(
        run_api_load_test(
            LoadTestOptions(
                base_url=base_url,
                token=token,
                agent_id=agent_id,
                count=count,
                concurrency=concurrency,
                wait_for_terminal=wait_for_terminal,
                timeout_seconds=timeout_seconds,
                min_accept_rate=min_accept_rate,
                min_completion_rate=min_completion_rate,
                min_success_rate=min_success_rate,
                max_submit_p95_ms=max_submit_p95_ms,
                max_e2e_p95_ms=max_e2e_p95_ms,
            )
        )
    )
    report = _write_json_report(result, output, prefix="load")
    typer.echo(f"report {report}")
    typer.echo("PASS" if result["passed"] else "FAIL")
    if not result["passed"]:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
