"""Synthetic PostgreSQL coordination drill with explicit fencing checks."""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * ratio)))
    return float(ordered[index])


class DurabilityDrill:
    """Exercise claim distribution, lease takeover, and idempotency on real PG."""

    def __init__(self, store: Any) -> None:
        self.store = store

    async def run(
        self,
        *,
        task_count: int = 100,
        claim_concurrency: int = 8,
        cleanup: bool = True,
    ) -> dict[str, Any]:
        task_count = max(1, min(int(task_count), 5000))
        claim_concurrency = max(1, min(int(claim_concurrency), 32, task_count))
        drill_id = f"drill_{uuid4().hex}"
        user_id = f"drill:{drill_id}"
        started_at = datetime.now(timezone.utc).isoformat()
        checks: dict[str, bool] = {}
        details: dict[str, Any] = {}
        try:
            distribution = await asyncio.to_thread(
                self._claim_distribution,
                drill_id,
                user_id,
                task_count,
                claim_concurrency,
            )
            details["claim_distribution"] = distribution
            checks["all_tasks_claimed_once"] = (
                distribution["claimed"] == task_count
                and distribution["unique_task_ids"] == task_count
            )
            takeover = await asyncio.to_thread(
                self._lease_takeover, drill_id, user_id
            )
            details["lease_takeover"] = takeover
            checks["lease_version_advanced"] = takeover["new_lease_version"] > takeover[
                "old_lease_version"
            ]
            checks["stale_worker_fenced"] = not takeover["stale_commit_accepted"]
            checks["new_owner_committed"] = takeover["new_owner_commit_accepted"]
            idempotency = await asyncio.to_thread(
                self._idempotency, drill_id, user_id
            )
            details["idempotency"] = idempotency
            checks["duplicate_submission_collapsed"] = (
                idempotency["first_run_id"] == idempotency["second_run_id"]
                and idempotency["first_created"]
                and not idempotency["second_created"]
            )
            metrics = await asyncio.to_thread(self.store.operational_metrics)
            details["post_drill_queue"] = dict(metrics.get("queue") or {})
            checks["no_expired_drill_lease_remains"] = all(
                task.status != "running" or task.lease_owner is not None
                for task in self.store.list_runtime_tasks(user_id=user_id, limit=5000)
            )
        finally:
            cleaned = await asyncio.to_thread(self._cleanup, user_id) if cleanup else 0
        return {
            "schema_version": 1,
            "drill_id": drill_id,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "task_count": task_count,
            "claim_concurrency": claim_concurrency,
            "checks": checks,
            "details": details,
            "cleanup": {"enabled": cleanup, "runs_removed": cleaned},
            "passed": bool(checks) and all(checks.values()),
        }

    def _claim_distribution(
        self,
        drill_id: str,
        user_id: str,
        task_count: int,
        claim_concurrency: int,
    ) -> dict[str, Any]:
        run_id = f"{drill_id}:distribution"
        self.store.create_runtime_run(
            run_id=run_id,
            user_id=user_id,
            session_id=run_id,
            agent_id="default",
            kind="graph",
            prompt="synthetic claim distribution drill",
            options={"max_concurrent": task_count},
            total_task_count=task_count,
        )
        for index in range(task_count):
            self.store.create_runtime_task(
                task_id=f"{run_id}:task:{index}",
                run_id=run_id,
                name=f"claim-{index}",
                payload={"node_type": "agent", "drill_id": drill_id},
                max_attempts=2,
            )

        def claim(index: int) -> tuple[Any, float]:
            started = time.monotonic()
            task = self.store.claim_runtime_task(
                worker_id=f"{drill_id}:worker:{index}",
                lease_seconds=30,
                run_id=run_id,
            )
            return task, (time.monotonic() - started) * 1000

        claims: list[tuple[Any, float]] = []
        with ThreadPoolExecutor(max_workers=claim_concurrency) as executor:
            while len(claims) < task_count:
                batch_size = min(claim_concurrency, task_count - len(claims))
                batch = list(executor.map(claim, range(batch_size)))
                accepted = [item for item in batch if item[0] is not None]
                if not accepted:
                    break
                claims.extend(accepted)
        for task, _latency in claims:
            self.store.update_runtime_task(
                task.task_id,
                status="completed",
                result={"drill": "claim_distribution"},
                worker_id=task.lease_owner,
                lease_version=task.lease_version,
            )
        self.store.update_runtime_run(run_id, status="completed")
        latencies = [latency for _task, latency in claims]
        task_ids = [task.task_id for task, _latency in claims]
        return {
            "claimed": len(claims),
            "unique_task_ids": len(set(task_ids)),
            "claim_latency_ms": {
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
                "p99": _percentile(latencies, 0.99),
                "max": max(latencies, default=0.0),
            },
        }

    def _lease_takeover(self, drill_id: str, user_id: str) -> dict[str, Any]:
        run_id = f"{drill_id}:takeover"
        task_id = f"{run_id}:task"
        self.store.create_runtime_run(
            run_id=run_id,
            user_id=user_id,
            session_id=run_id,
            agent_id="default",
            kind="graph",
            prompt="synthetic lease takeover drill",
            options={"max_concurrent": 1},
            total_task_count=1,
        )
        self.store.create_runtime_task(
            task_id=task_id,
            run_id=run_id,
            name="takeover",
            payload={"node_type": "agent", "drill_id": drill_id},
            max_attempts=3,
        )
        old = self.store.claim_runtime_task(
            worker_id=f"{drill_id}:old", lease_seconds=30, run_id=run_id
        )
        if old is None:
            raise RuntimeError("old worker could not claim synthetic task")
        with self.store._pool.connection() as conn, conn.transaction():
            conn.execute(
                """UPDATE runtime_tasks
                   SET lease_expires_at=clock_timestamp()-interval '1 second'
                   WHERE task_id=%s AND lease_owner=%s AND lease_version=%s""",
                (task_id, old.lease_owner, old.lease_version),
            )
        self.store._lease_sweep_at = 0.0
        new = self.store.claim_runtime_task(
            worker_id=f"{drill_id}:new", lease_seconds=30, run_id=run_id
        )
        if new is None:
            raise RuntimeError("new worker did not take over expired synthetic lease")
        stale_commit = self.store.update_runtime_task(
            task_id,
            status="completed",
            result={"owner": "old"},
            worker_id=old.lease_owner,
            lease_version=old.lease_version,
        )
        new_commit = self.store.update_runtime_task(
            task_id,
            status="completed",
            result={"owner": "new"},
            worker_id=new.lease_owner,
            lease_version=new.lease_version,
        )
        self.store.update_runtime_run(run_id, status="completed")
        return {
            "old_lease_version": old.lease_version,
            "new_lease_version": new.lease_version,
            "attempt": new.attempt,
            "stale_commit_accepted": stale_commit,
            "new_owner_commit_accepted": new_commit,
        }

    def _idempotency(self, drill_id: str, user_id: str) -> dict[str, Any]:
        values = {
            "run_id": f"{drill_id}:idempotency:first",
            "user_id": user_id,
            "session_id": f"{drill_id}:idempotency",
            "agent_id": "default",
            "kind": "agent",
            "prompt": "synthetic idempotency drill",
            "options": {},
            "idempotency_key": f"{drill_id}:same-request",
        }
        first, first_created = self.store.create_runtime_run(**values)
        second, second_created = self.store.create_runtime_run(
            **{**values, "run_id": f"{drill_id}:idempotency:second"}
        )
        self.store.update_runtime_run(first.run_id, status="completed")
        return {
            "first_run_id": first.run_id,
            "second_run_id": second.run_id,
            "first_created": first_created,
            "second_created": second_created,
        }

    def _cleanup(self, user_id: str) -> int:
        with self.store._pool.connection() as conn, conn.transaction():
            rows = conn.execute(
                "DELETE FROM runtime_runs WHERE user_id=%s RETURNING run_id", (user_id,)
            ).fetchall()
        return len(rows)
