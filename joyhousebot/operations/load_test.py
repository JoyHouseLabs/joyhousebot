"""Authenticated API load rehearsal with machine-readable SLO verdicts."""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

_TERMINAL = {"completed", "failed", "cancelled", "timed_out"}


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * ratio)))
    return float(ordered[rank])


@dataclass(frozen=True, slots=True)
class LoadTestOptions:
    base_url: str
    token: str
    agent_id: str = "default"
    count: int = 20
    concurrency: int = 4
    wait_for_terminal: bool = True
    timeout_seconds: float = 180.0
    min_accept_rate: float = 0.995
    min_completion_rate: float = 0.99
    min_success_rate: float = 0.95
    max_submit_p95_ms: float = 1000.0
    max_e2e_p95_ms: float = 120000.0


async def run_api_load_test(
    options: LoadTestOptions,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Submit concurrent Runs, verify idempotency, and enforce explicit SLOs."""
    count = max(1, min(int(options.count), 10000))
    concurrency = max(1, min(int(options.concurrency), 256, count))
    drill_id = f"load_{uuid4().hex}"
    base_url = options.base_url.rstrip("/")
    parsed = urlsplit(base_url)
    safe_target = f"{parsed.scheme}://{parsed.netloc}"
    semaphore = asyncio.Semaphore(concurrency)
    submissions: list[dict[str, Any]] = []
    headers = {"Authorization": f"Bearer {options.token}"}
    limits = httpx.Limits(
        max_connections=max(concurrency * 2, 10),
        max_keepalive_connections=max(concurrency, 5),
    )
    started_at = datetime.now(timezone.utc).isoformat()

    async with httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        timeout=httpx.Timeout(30.0),
        limits=limits,
        transport=transport,
    ) as client:
        health = await client.get("/readyz")
        if health.status_code != 200 or not health.json().get("ok"):
            raise RuntimeError("target API is not ready")

        async def submit(index: int) -> dict[str, Any]:
            async with semaphore:
                identity = f"{drill_id}:{index}"
                payload = {
                    "execution": {"mode": "agent", "agent_id": options.agent_id},
                    "session_id": identity,
                    "interaction_mode": "background",
                    "input": {
                        "content": (
                            "Load rehearsal probe. Return a concise acknowledgement "
                            f"containing sequence {index}."
                        )
                    },
                    "metadata": {"load_test_id": drill_id, "sequence": index},
                    "timeout_seconds": options.timeout_seconds,
                }
                request_headers = {
                    "Idempotency-Key": identity,
                    "X-Request-Id": f"req_{uuid4().hex}",
                    "X-Tracker-Id": f"trace_{uuid4().hex}",
                }
                started = time.monotonic()
                try:
                    response = await client.post(
                        "/control/v1/runs", json=payload, headers=request_headers
                    )
                    submit_ms = (time.monotonic() - started) * 1000
                    body = response.json()
                    return {
                        "index": index,
                        "accepted": response.status_code in {200, 202}
                        and bool(body.get("run_id")),
                        "status_code": response.status_code,
                        "run_id": body.get("run_id"),
                        "status": body.get("status"),
                        "submit_ms": submit_ms,
                        "submitted_monotonic": started,
                        "idempotency_key": identity,
                        "payload": payload,
                    }
                except (httpx.HTTPError, ValueError) as exc:
                    return {
                        "index": index,
                        "accepted": False,
                        "status_code": 0,
                        "run_id": None,
                        "status": "client_error",
                        "submit_ms": (time.monotonic() - started) * 1000,
                        "error_type": type(exc).__name__,
                    }

        submissions = await asyncio.gather(*(submit(index) for index in range(count)))
        accepted = [item for item in submissions if item["accepted"]]
        duplicate_collapsed = False
        if accepted:
            sample = accepted[0]
            repeated = await client.post(
                "/control/v1/runs",
                json=sample["payload"],
                headers={"Idempotency-Key": sample["idempotency_key"]},
            )
            duplicate_collapsed = (
                repeated.status_code in {200, 202}
                and repeated.json().get("run_id") == sample["run_id"]
            )

        if options.wait_for_terminal and accepted:
            await _poll_runs(
                client,
                accepted,
                semaphore=semaphore,
                timeout_seconds=options.timeout_seconds + 30,
            )

    submit_latencies = [float(item["submit_ms"]) for item in submissions]
    terminal = [item for item in accepted if item.get("status") in _TERMINAL]
    successful = [item for item in terminal if item.get("status") == "completed"]
    e2e_latencies = [float(item["e2e_ms"]) for item in terminal if item.get("e2e_ms")]
    accept_rate = len(accepted) / count
    completion_rate = len(terminal) / len(accepted) if accepted else 0.0
    success_rate = len(successful) / len(terminal) if terminal else 0.0
    metrics = {
        "submitted": count,
        "accepted": len(accepted),
        "terminal": len(terminal),
        "completed": len(successful),
        "accept_rate": accept_rate,
        "completion_rate": completion_rate,
        "success_rate": success_rate,
        "submit_latency_ms": {
            "p50": percentile(submit_latencies, 0.50),
            "p95": percentile(submit_latencies, 0.95),
            "p99": percentile(submit_latencies, 0.99),
            "max": max(submit_latencies, default=0.0),
        },
        "e2e_latency_ms": {
            "p50": percentile(e2e_latencies, 0.50),
            "p95": percentile(e2e_latencies, 0.95),
            "p99": percentile(e2e_latencies, 0.99),
            "max": max(e2e_latencies, default=0.0),
        },
        "terminal_statuses": _status_counts(terminal),
        "http_statuses": _status_counts(submissions, key="status_code"),
    }
    checks = {
        "api_accept_rate": accept_rate >= options.min_accept_rate,
        "idempotency_collapsed": duplicate_collapsed,
        "submit_p95": metrics["submit_latency_ms"]["p95"] <= options.max_submit_p95_ms,
    }
    if options.wait_for_terminal:
        checks.update(
            {
                "completion_rate": completion_rate >= options.min_completion_rate,
                "success_rate": success_rate >= options.min_success_rate,
                "e2e_p95": bool(e2e_latencies)
                and metrics["e2e_latency_ms"]["p95"] <= options.max_e2e_p95_ms,
            }
        )
    thresholds = asdict(options)
    thresholds.pop("token", None)
    thresholds.pop("base_url", None)
    return {
        "schema_version": 1,
        "load_test_id": drill_id,
        "target": safe_target,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "concurrency": concurrency,
        "thresholds": thresholds,
        "metrics": metrics,
        "checks": checks,
        "passed": all(checks.values()),
        "failures": [
            {
                key: item.get(key)
                for key in ("index", "status_code", "status", "error_type")
            }
            for item in submissions
            if not item["accepted"] or item.get("status") in {"failed", "cancelled", "timed_out"}
        ][:100],
    }


async def _poll_runs(
    client: httpx.AsyncClient,
    submissions: list[dict[str, Any]],
    *,
    semaphore: asyncio.Semaphore,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    pending = {str(item["run_id"]): item for item in submissions}
    delay = 0.1
    while pending and time.monotonic() < deadline:
        async def poll(run_id: str, item: dict[str, Any]) -> None:
            async with semaphore:
                try:
                    response = await client.get(f"/control/v1/runs/{run_id}")
                    if response.status_code != 200:
                        return
                    status = str(response.json().get("status") or "")
                    item["status"] = status
                    if status in _TERMINAL:
                        item["e2e_ms"] = (
                            time.monotonic() - float(item["submitted_monotonic"])
                        ) * 1000
                        pending.pop(run_id, None)
                except (httpx.HTTPError, ValueError):
                    return

        await asyncio.gather(*(poll(run_id, item) for run_id, item in list(pending.items())))
        if pending:
            await asyncio.sleep(delay)
            delay = min(1.0, delay * 1.5)


def _status_counts(items: list[dict[str, Any]], *, key: str = "status") -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))
