#!/usr/bin/env python3
"""Launch one explicitly safe App Entry Point and record an integration report."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from joyhousebot.app_sdk import AppRuntimeClient

_CONFIRMATION = "LAUNCH_APP_SMOKE_RUN"
_REQUIRED_ENV = (
    "JOYHOUSEBOT_APP_CLIENT_ID",
    "JOYHOUSEBOT_APP_CLIENT_SECRET",
    "JOYHOUSEBOT_APP_GRANT_ID",
    "JOYHOUSEBOT_APP_INSTALLATION_ID",
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Exercise App token exchange, discovery, launch, and terminal read."
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--entrypoint-id", required=True)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument(
        "--content",
        default="JoyhouseBot App integration smoke. Do not perform external side effects.",
    )
    parser.add_argument("--output-dir", default="artifacts/drills")
    return parser.parse_args()


def _environment() -> dict[str, str]:
    values = {name: str(os.getenv(name) or "").strip() for name in _REQUIRED_ENV}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError("missing required environment: " + ", ".join(missing))
    return values


async def _run(args: argparse.Namespace, env: dict[str, str]) -> dict[str, Any]:
    if args.confirm != _CONFIRMATION:
        raise RuntimeError(f"--confirm must equal {_CONFIRMATION}")
    installation_id = env["JOYHOUSEBOT_APP_INSTALLATION_ID"]
    started_at = datetime.now(timezone.utc)
    request_key = f"app-smoke:{started_at.strftime('%Y%m%dT%H%M%SZ')}:{uuid4().hex}"
    async with AppRuntimeClient(
        args.base_url,
        client_id=env["JOYHOUSEBOT_APP_CLIENT_ID"],
        client_secret=env["JOYHOUSEBOT_APP_CLIENT_SECRET"],
        grant_id=env["JOYHOUSEBOT_APP_GRANT_ID"],
    ) as client:
        installations = await client.list_apps()
        visible = any(
            str(item.get("installation_id")) == installation_id
            for item in installations
        )
        if not visible:
            raise RuntimeError("target installation is not visible to the delegated Token")
        launched = await client.launch(
            installation_id,
            args.content,
            entrypoint_id=args.entrypoint_id,
            idempotency_key=request_key,
        )
        run_id = str(launched["run_id"])
        terminal = await client.wait_run(
            run_id,
            timeout_seconds=max(1.0, args.timeout_seconds),
        )
    finished_at = datetime.now(timezone.utc)
    return {
        "schema_version": 1,
        "kind": "app_integration_smoke",
        "base_url": args.base_url,
        "installation_id": installation_id,
        "entrypoint_id": args.entrypoint_id,
        "request_key": request_key,
        "run_id": run_id,
        "status": str(terminal.get("status")),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
        "passed": str(terminal.get("status")) == "completed",
    }


def main() -> int:
    args = _arguments()
    report = asyncio.run(_run(args, _environment()))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"app-integration-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "report": str(output)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
