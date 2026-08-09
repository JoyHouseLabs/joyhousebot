"""Execute versioned Eval cases against exact runtime targets."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from joyhousebot.application.errors import ConflictError, NotFoundError, ValidationError
from joyhousebot.application.evals import stable_observation_hash
from joyhousebot.domain.capabilities.models import CapabilityRef
from joyhousebot.runtime.models import AgentOptions, GraphTaskSpec, TaskGraphSpec

_TERMINAL = {"completed", "failed", "cancelled", "timed_out"}


class EvalExecutionService:
    """Durable, resumable execution adapter for Eval datasets."""

    def __init__(self, *, store: Any, runtime: Any, evals: Any, scenarios: Any) -> None:
        self.store = store
        self.runtime = runtime
        self.evals = evals
        self.scenarios = scenarios

    async def enqueue(
        self,
        eval_run_id: str,
        *,
        actor_id: str,
        max_concurrency: int = 4,
        case_timeout_seconds: float = 300.0,
    ) -> dict[str, Any]:
        """Queue execution durably; a Scheduler Worker owns orchestration."""
        try:
            return await asyncio.to_thread(
                self.store.enqueue_eval_execution,
                eval_run_id,
                configuration={
                    "max_concurrency": max(1, min(int(max_concurrency), 16)),
                    "case_timeout_seconds": max(
                        1.0, min(float(case_timeout_seconds), 3600.0)
                    ),
                },
                requested_by=actor_id,
            )
        except ValueError as exc:
            if "not found" in str(exc):
                raise NotFoundError(str(exc)) from exc
            raise ConflictError(str(exc)) from exc

    async def execute(
        self,
        eval_run_id: str,
        *,
        actor_id: str,
        max_concurrency: int = 4,
        case_timeout_seconds: float = 300.0,
    ) -> dict[str, Any]:
        run = await asyncio.to_thread(self.store.get_eval_run, eval_run_id)
        if run is None:
            raise NotFoundError("evaluation run not found")
        if run["status"] != "running":
            return run
        suite = await asyncio.to_thread(
            self.store.get_eval_suite, run["suite_id"], run["suite_version"]
        )
        if suite is None:
            raise NotFoundError("evaluation suite not found")
        observed = {str(item["case_id"]) for item in run.get("results") or []}
        pending = [case for case in suite["cases"] if case["case_id"] not in observed]
        semaphore = asyncio.Semaphore(max(1, min(int(max_concurrency), 16)))

        async def execute_case(case: dict[str, Any]) -> None:
            async with semaphore:
                await self._execute_case(
                    run,
                    case,
                    actor_id=actor_id,
                    timeout_seconds=case_timeout_seconds,
                )

        await asyncio.gather(*(execute_case(case) for case in pending))
        return await self.evals.finalize_run(eval_run_id)

    async def _execute_case(
        self,
        eval_run: dict[str, Any],
        case: dict[str, Any],
        *,
        actor_id: str,
        timeout_seconds: float,
    ) -> None:
        started = time.monotonic()
        status = "failed"
        source_run_id: str | None = None
        try:
            output, status, source_run_id = await self._execute_target(
                eval_run,
                case,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - failures are scored evidence
            output = {
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc)[:1000],
                }
            }
        latency_ms = (time.monotonic() - started) * 1000
        cost_usd = self._cost(output)
        await self.evals.record_observation(
            eval_run["eval_run_id"],
            {
                "case_id": case["case_id"],
                "output": output,
                "status": status,
                "latency_ms": latency_ms,
                "cost_usd": cost_usd,
                "metadata": {
                    "actor_id": actor_id,
                    "source_run_id": source_run_id,
                    "execution_mode": "automated",
                    "observation_sha256": stable_observation_hash(output),
                },
            },
        )

    async def _execute_target(
        self,
        eval_run: dict[str, Any],
        case: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> tuple[dict[str, Any], str, str | None]:
        target_type = str(eval_run["target_type"])
        if target_type == "agent":
            return await self._execute_agent(eval_run, case, timeout_seconds=timeout_seconds)
        if target_type == "scenario":
            return await self._execute_scenario(eval_run, case)
        if target_type == "capability":
            return await self._execute_capability(
                eval_run, case, timeout_seconds=timeout_seconds
            )
        raise ValidationError(f"unsupported evaluation target: {target_type}")

    async def _execute_agent(
        self,
        eval_run: dict[str, Any],
        case: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> tuple[dict[str, Any], str, str]:
        values = dict(case.get("input") or {})
        prompt = str(values.get("prompt") or "").strip()
        if not prompt:
            raise ValidationError("Agent evaluation case requires input.prompt")
        bounded_timeout = min(timeout_seconds, float(values.get("timeout_seconds") or timeout_seconds))
        record = await self.runtime.submit_run(
            AgentOptions(
                prompt=prompt,
                user_id=f"eval:{eval_run['eval_run_id']}",
                session_id=f"eval:{eval_run['eval_run_id']}:{case['case_id']}",
                agent_id=str(eval_run["target_id"]),
                agent_revision_id=str(eval_run["target_revision_id"]),
                model=values.get("model"),
                system_prompt=values.get("system_prompt"),
                output_schema=(
                    dict(values["output_schema"]) if values.get("output_schema") else None
                ),
                verification_policy=dict(values.get("verification_policy") or {}),
                timeout_seconds=bounded_timeout,
                max_turns=(int(values["max_turns"]) if values.get("max_turns") else None),
                max_input_tokens=(
                    int(values["max_input_tokens"])
                    if values.get("max_input_tokens")
                    else None
                ),
                max_output_tokens=(
                    int(values["max_output_tokens"])
                    if values.get("max_output_tokens")
                    else None
                ),
                max_cost_usd=(
                    float(values["max_cost_usd"]) if values.get("max_cost_usd") else None
                ),
                metadata={
                    "eval_run_id": eval_run["eval_run_id"],
                    "eval_case_id": case["case_id"],
                    "evaluation": True,
                },
                idempotency_key=f"eval:{eval_run['eval_run_id']}:{case['case_id']}",
            )
        )
        final = await self.runtime.wait(record.run_id, timeout=bounded_timeout + 5)
        if final is None or final.status not in _TERMINAL:
            await self.runtime.cancel(record.run_id, reason="evaluation case timeout")
            final = await self.runtime.wait(record.run_id, timeout=5)
        return await self._runtime_evidence(final or record)

    async def _execute_scenario(
        self, eval_run: dict[str, Any], case: dict[str, Any]
    ) -> tuple[dict[str, Any], str, None]:
        values = dict(case.get("input") or {})
        prompt = str(values.get("prompt") or "").strip()
        if not prompt:
            raise ValidationError("Scenario evaluation case requires input.prompt")
        try:
            version = int(eval_run["target_revision_id"])
        except ValueError as exc:
            raise ValidationError("Scenario target revision must be an integer") from exc
        output = await self.scenarios.simulate(
            str(eval_run["target_id"]),
            prompt=prompt,
            inputs=dict(values.get("inputs") or {}),
            version=version,
        )
        return output, "completed", None

    async def _execute_capability(
        self,
        eval_run: dict[str, Any],
        case: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> tuple[dict[str, Any], str, str]:
        definition = await asyncio.to_thread(
            self.store.get_capability_definition,
            str(eval_run["target_id"]),
            str(eval_run["target_revision_id"]),
        )
        if definition is None:
            raise ConflictError("capability must be published before automated regression Eval")
        ref = CapabilityRef.from_dict(dict(definition["ref"]))
        profile = await asyncio.to_thread(self.store.get_agent_profile)
        if profile is None:
            raise ConflictError("default Agent is unavailable")
        spec = TaskGraphSpec(
            goal=f"Evaluate capability {ref.capability_id}",
            user_id=f"eval:{eval_run['eval_run_id']}",
            session_id=f"eval:{eval_run['eval_run_id']}:{case['case_id']}",
            agent_id=profile.definition.agent_id,
            max_concurrent=1,
            fail_fast=True,
            aggregate=True,
            idempotency_key=f"eval:{eval_run['eval_run_id']}:{case['case_id']}",
            metadata={"eval_run_id": eval_run["eval_run_id"], "evaluation": True},
            tasks=[
                GraphTaskSpec(
                    id="capability",
                    prompt=f"Evaluate {ref.capability_id}",
                    capability=ref,
                    capability_input=dict(case.get("input") or {}).get("arguments") or {},
                    timeout_seconds=timeout_seconds,
                    node_type="capability",
                )
            ],
        )
        record = await self.runtime.submit_graph(spec)
        final = await self.runtime.wait(record.run_id, timeout=timeout_seconds + 5)
        if final is None or final.status not in _TERMINAL:
            await self.runtime.cancel(record.run_id, reason="evaluation case timeout")
            final = await self.runtime.wait(record.run_id, timeout=5)
        return await self._runtime_evidence(final or record)

    async def _runtime_evidence(self, record: Any) -> tuple[dict[str, Any], str, str]:
        events, artifacts, verifications = await asyncio.gather(
            asyncio.to_thread(self.store.list_runtime_events, record.run_id),
            asyncio.to_thread(
                self.store.list_runtime_artifacts,
                record.run_id,
                user_id=record.user_id,
            ),
            asyncio.to_thread(self.store.list_verification_records, record.run_id),
        )
        evidence = {
            "runtime_run_id": record.run_id,
            "result": record.result,
            "error": record.error,
            "event_types": [item.type for item in events],
            "artifacts": [
                {
                    key: item.get(key)
                    for key in ("artifact_id", "name", "media_type", "content_sha256", "uri")
                }
                for item in artifacts
            ],
            "verifications": [
                item.to_dict() if hasattr(item, "to_dict") else dict(item)
                for item in verifications
            ],
        }
        return evidence, str(record.status), str(record.run_id)

    @staticmethod
    def _cost(output: dict[str, Any]) -> float | None:
        result = output.get("result")
        if not isinstance(result, dict):
            return None
        usage = result.get("usage")
        if not isinstance(usage, dict) or usage.get("cost_usd") is None:
            return None
        return float(usage["cost_usd"])
