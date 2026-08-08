from pathlib import Path

import httpx
import pytest

from joyhousebot.operations.durability_drill import DurabilityDrill
from joyhousebot.operations.load_test import LoadTestOptions, percentile, run_api_load_test
from tests.support.postgres_store import PostgresTestStore


@pytest.mark.asyncio
async def test_durability_drill_proves_claim_takeover_fencing_and_cleanup(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "durability-drill.db")
    report = await DurabilityDrill(store).run(
        task_count=24,
        claim_concurrency=6,
        cleanup=True,
    )

    assert report["passed"] is True
    assert report["checks"] == {
        "all_tasks_claimed_once": True,
        "lease_version_advanced": True,
        "stale_worker_fenced": True,
        "new_owner_committed": True,
        "duplicate_submission_collapsed": True,
        "no_expired_drill_lease_remains": True,
    }
    assert report["details"]["claim_distribution"]["claimed"] == 24
    assert report["cleanup"]["runs_removed"] == 3
    assert store.list_runtime_tasks(user_id=f"drill:{report['drill_id']}") == []


def test_load_report_percentiles_are_deterministic() -> None:
    values = [10.0, 50.0, 20.0, 40.0, 30.0]
    assert percentile(values, 0.5) == 30.0
    assert percentile(values, 0.95) == 40.0
    assert percentile(values, 0.99) == 40.0
    assert percentile([], 0.95) == 0.0


@pytest.mark.asyncio
async def test_api_load_rehearsal_checks_slos_and_never_reports_token() -> None:
    runs: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/readyz":
            return httpx.Response(200, json={"ok": True})
        if request.method == "POST" and request.url.path == "/v1/runs":
            identity = str(request.headers["idempotency-key"])
            run_id = runs.setdefault(identity, f"run-{len(runs)}")
            return httpx.Response(202, json={"run_id": run_id, "status": "queued"})
        if request.method == "GET" and request.url.path.startswith("/v1/runs/"):
            return httpx.Response(
                200,
                json={"run_id": request.url.path.rsplit("/", 1)[-1], "status": "completed"},
            )
        return httpx.Response(404)

    secret = "this-token-must-not-enter-the-report"
    report = await run_api_load_test(
        LoadTestOptions(
            base_url="https://runtime.example.test",
            token=secret,
            count=8,
            concurrency=4,
            max_submit_p95_ms=1000,
            max_e2e_p95_ms=1000,
        ),
        transport=httpx.MockTransport(handler),
    )

    assert report["passed"] is True
    assert report["metrics"]["accepted"] == 8
    assert report["checks"]["idempotency_collapsed"] is True
    assert secret not in str(report)
