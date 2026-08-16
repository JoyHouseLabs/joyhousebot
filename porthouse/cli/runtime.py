"""Porthouse Runtime command line interface."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer

from porthouse.config.loader import CONFIG_PATH_ENV

app = typer.Typer(help="Porthouse Agent Runtime and control plane")


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
    service_task = asyncio.create_task(service.run(), name="porthouse-service")
    stop_task = asyncio.create_task(stopping.wait(), name="porthouse-stop-signal")
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
    os.environ["PORTHOUSE_API_SURFACE"] = normalized_surface
    uvicorn.run(
        "porthouse.api.app:app",
        host=host,
        port=port,
        workers=workers,
        factory=False,
    )


@app.command("model-gateway")
def model_gateway(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(18794, "--port"),
    workers: int = typer.Option(1, "--workers", min=1),
    config: Path | None = ConfigOption,
) -> None:
    """Start the credential-isolating Host Model Gateway process."""
    import uvicorn

    _select_config(config)
    uvicorn.run(
        "porthouse.model_gateway.app:app",
        host=host,
        port=port,
        workers=workers,
        factory=False,
    )


@app.command()
def worker(config: Path | None = ConfigOption) -> None:
    """Start an Agent execution worker with no public network listener."""
    from porthouse.bootstrap.worker import build_execution_worker
    from porthouse.observability.otel import configure_telemetry

    _select_config(config)
    configure_telemetry(service_name="porthouse-worker")
    try:
        asyncio.run(_run_service_until_stopped(build_execution_worker()))
    except KeyboardInterrupt:
        pass


@app.command("discover-extensions")
def discover_extensions(config: Path | None = ConfigOption) -> None:
    """Register enabled extension metadata without executing models or tools."""
    from porthouse.bootstrap.extension_catalog import discover_enabled_extensions
    from porthouse.config.access import get_config

    _select_config(config)
    values = discover_enabled_extensions(get_config())
    typer.echo(json.dumps({"items": values}, ensure_ascii=False))


@app.command("scan-extensions")
def scan_extensions(config: Path | None = ConfigOption) -> None:
    """Refresh available extension metadata without importing extension code."""
    from porthouse.bootstrap.extension_catalog import synchronize_extension_inventory
    from porthouse.config.access import get_config

    _select_config(config)
    values = synchronize_extension_inventory(get_config())
    typer.echo(json.dumps({"items": values}, ensure_ascii=False))


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    """Create a private key file without ever overwriting an existing file."""
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
    except BaseException:
        try:
            target.unlink(missing_ok=True)
        finally:
            raise


@app.command("market-keygen")
def market_keygen(
    private_output: Path = typer.Option(..., "--private-output", dir_okay=False),
    public_output: Path = typer.Option(..., "--public-output", dir_okay=False),
) -> None:
    """Generate an Ed25519 App publisher key pair without printing the secret."""
    from porthouse.market_protocol.dsse import generate_ed25519_key_pair

    pair = generate_ed25519_key_pair()
    private_value = {
        **pair.public_record(),
        "private_key": f"base64url:{pair.private_key}",
    }
    _write_private_json(private_output, private_value)
    public_target = public_output.expanduser().resolve()
    if public_target.exists():
        private_output.expanduser().resolve().unlink(missing_ok=True)
        raise typer.BadParameter(f"public key file already exists: {public_target}")
    public_target.parent.mkdir(parents=True, exist_ok=True)
    public_target.write_text(
        json.dumps(pair.public_record(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    typer.echo(json.dumps({"key_id": pair.key_id, "public_output": str(public_target)}))


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(f"unable to read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise typer.BadParameter(f"{label} must contain a JSON object")
    return value


@app.command("market-pack")
def market_pack(
    manifest: Path = typer.Option(
        ...,
        "--manifest",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    publisher_key: Path = typer.Option(
        ...,
        "--publisher-key",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    market_id: str = typer.Option(..., "--market-id"),
    publisher_id: str = typer.Option(..., "--publisher-id"),
    output: Path = typer.Option(..., "--output", dir_okay=False),
    component: list[str] | None = typer.Option(
        None,
        "--component",
        help="Portable component as kind:logical_id:version:/path/to/payload.json.",
    ),
) -> None:
    """Build a canonical, DSSE-signed ``.porthouse-app`` release bundle."""
    from porthouse.market_protocol.bundle import build_app_bundle
    from porthouse.market_protocol.canonical import canonical_json, parse_strict_json

    manifest_value = _read_json_object(manifest, label="App manifest")
    key_value = _read_json_object(publisher_key, label="publisher private key")
    private_key = str(key_value.get("private_key") or "")
    if not private_key:
        raise typer.BadParameter("publisher key file does not contain private_key")
    components: dict[tuple[str, str, str], bytes] = {}
    for spec in component or []:
        parts = str(spec).split(":", 3)
        if len(parts) != 4:
            raise typer.BadParameter("component must use kind:logical_id:version:path")
        kind, logical_id, version, component_path = parts
        path = Path(component_path).expanduser()
        try:
            raw_payload = path.read_bytes()
            parsed_payload = parse_strict_json(raw_payload)
        except OSError as exc:
            raise typer.BadParameter(f"unable to read component: {path}") from exc
        except ValueError as exc:
            raise typer.BadParameter(f"component must contain strict JSON: {path}") from exc
        if not isinstance(parsed_payload, dict):
            raise typer.BadParameter(f"component must contain a JSON object: {path}")
        payload = canonical_json(parsed_payload)
        identity = (kind, logical_id, version)
        if identity in components:
            raise typer.BadParameter(f"duplicate component: {identity}")
        components[identity] = payload
    bundle = build_app_bundle(
        output.expanduser().resolve(),
        manifest=manifest_value,
        private_key=private_key,
        market_id=market_id,
        publisher_id=publisher_id,
        components=components,
    )
    typer.echo(
        json.dumps(
            {
                "output": str(output.expanduser().resolve()),
                "app_id": bundle.manifest["app_id"],
                "version": bundle.manifest["version"],
                "signer_key_id": bundle.signer_key_id,
            },
            ensure_ascii=False,
        )
    )


@app.command("market-verify")
def market_verify(
    bundle: Path = typer.Option(
        ...,
        "--bundle",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    publisher_key: Path = typer.Option(
        ...,
        "--publisher-key",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    market_id: str | None = typer.Option(None, "--market-id"),
    publisher_id: str | None = typer.Option(None, "--publisher-id"),
) -> None:
    """Verify an App bundle using an explicitly trusted publisher key."""
    from porthouse.market_protocol.bundle import verify_app_bundle

    key_value = _read_json_object(publisher_key, label="publisher public key")
    key_id = str(key_value.get("key_id") or "")
    public_key = str(key_value.get("public_key") or "")
    if not key_id or not public_key:
        raise typer.BadParameter("publisher key must contain key_id and public_key")
    verified = verify_app_bundle(
        bundle.expanduser().resolve(),
        public_keys={key_id: public_key},
        expected_market_id=market_id,
        expected_publisher_id=publisher_id,
    )
    typer.echo(
        json.dumps(
            {
                "valid": True,
                "app_id": verified.manifest["app_id"],
                "version": verified.manifest["version"],
                "publisher_id": verified.descriptor["source"]["publisher_id"],
                "market_id": verified.descriptor["source"]["market_id"],
                "signer_key_id": verified.signer_key_id,
                "components": len(verified.components),
            },
            ensure_ascii=False,
        )
    )


@app.command("market-key-rotation-proof")
def market_key_rotation_proof(
    publisher_id: str = typer.Option(..., "--publisher-id"),
    old_publisher_key: Path = typer.Option(
        ..., "--old-publisher-key", exists=True, dir_okay=False, readable=True
    ),
    new_publisher_key: Path = typer.Option(
        ..., "--new-publisher-key", exists=True, dir_okay=False, readable=True
    ),
    output: Path = typer.Option(..., "--output", dir_okay=False),
) -> None:
    """Create the old-key and new-key DSSE proofs required for safe rotation."""
    from porthouse.market_protocol.contracts import (
        PUBLISHER_KEY_ROTATION_MEDIA_TYPE,
        sign_json_contract,
    )
    from porthouse.market_protocol.dsse import ed25519_key_id, public_key_bytes
    from porthouse.market_protocol.release import normalize_publisher_id

    old_value = _read_json_object(old_publisher_key, label="old publisher private key")
    new_value = _read_json_object(new_publisher_key, label="new publisher private key")
    for label, value in (("old", old_value), ("new", new_value)):
        if not value.get("key_id") or not value.get("public_key") or not value.get("private_key"):
            raise typer.BadParameter(f"{label} publisher key is incomplete")
        actual_key_id = ed25519_key_id(public_key_bytes(str(value["public_key"])))
        if str(value["key_id"]) != actual_key_id:
            raise typer.BadParameter(
                f"{label} publisher key_id does not match its public key"
            )
    payload = {
        "schema_version": "1.0",
        "publisher_id": normalize_publisher_id(publisher_id),
        "old_key_id": str(old_value["key_id"]),
        "new_key": {
            "key_id": str(new_value["key_id"]),
            "algorithm": "ed25519",
            "public_key": str(new_value["public_key"]),
        },
    }
    result = {
        "new_key_id": str(new_value["key_id"]),
        "new_public_key": str(new_value["public_key"]),
        "old_proof": sign_json_contract(
            payload,
            payload_type=PUBLISHER_KEY_ROTATION_MEDIA_TYPE,
            private_key=str(old_value["private_key"]),
        ),
        "new_proof": sign_json_contract(
            payload,
            payload_type=PUBLISHER_KEY_ROTATION_MEDIA_TYPE,
            private_key=str(new_value["private_key"]),
        ),
    }
    _write_private_json(output, result)
    typer.echo(json.dumps({"output": str(output.expanduser().resolve())}))


@app.command()
def scheduler(config: Path | None = ConfigOption) -> None:
    """Start the distributed schedule and DAG maintenance worker."""
    from porthouse.bootstrap.worker import build_scheduler_worker
    from porthouse.observability.otel import configure_telemetry

    _select_config(config)
    configure_telemetry(service_name="porthouse-scheduler")
    try:
        asyncio.run(_run_service_until_stopped(build_scheduler_worker()))
    except KeyboardInterrupt:
        pass


@app.command("channel-worker")
def channel_worker(config: Path | None = ConfigOption) -> None:
    """Start distributed channel connectors; model execution stays in workers."""
    from porthouse.bootstrap.worker import build_channel_worker
    from porthouse.observability.otel import configure_telemetry

    _select_config(config)
    configure_telemetry(service_name="porthouse-channel-worker")
    try:
        asyncio.run(_run_service_until_stopped(build_channel_worker()))
    except KeyboardInterrupt:
        pass


@app.command()
def check(config: Path | None = ConfigOption) -> None:
    """Validate configuration and database readiness."""
    from porthouse.config.access import get_config
    from porthouse.storage.factory import create_runtime_store

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
    from porthouse.bootstrap.container import build_api_container

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
    from porthouse.bootstrap.container import build_api_container

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
    from porthouse.config.access import get_config
    from porthouse.operations import DurabilityDrill
    from porthouse.storage.factory import create_runtime_store

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
    from porthouse.operations import LoadTestOptions, run_api_load_test

    token = str(os.getenv("PORTHOUSE_LOAD_TOKEN") or "").strip()
    if not token:
        raise typer.BadParameter(
            "PORTHOUSE_LOAD_TOKEN is required; use a scoped runs.read/runs.write service token"
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
