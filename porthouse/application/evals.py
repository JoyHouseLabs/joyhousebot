"""Deterministic evaluation scoring and release-gate use cases."""

from __future__ import annotations

import asyncio
import json
import re
from hashlib import sha256
from typing import Any
from uuid import uuid4

from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JsonSchemaError

from porthouse.application.errors import ConflictError, NotFoundError, ValidationError
from porthouse.runtime.action_identity import payload_hash

_TARGET_TYPES = {
    "agent",
    "skill",
    "prompt",
    "scenario",
    "capability",
    "embedding_profile",
}
_SCORER_TYPES = {
    "status",
    "exact_match",
    "contains",
    "not_contains",
    "matches_regex",
    "json_schema",
    "json_path_equals",
    "json_path_exists",
    "list_min_items",
    "numeric_range",
    "max_latency_ms",
    "max_cost_usd",
}


def _bounded_json(value: Any, *, label: str, max_bytes: int = 1_048_576) -> None:
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} must be JSON serializable") from exc
    if len(encoded) > max_bytes:
        raise ValidationError(f"{label} exceeds {max_bytes} bytes")


def _read_path(value: Any, path: str) -> Any:
    current = value
    for part in [item for item in path.removeprefix("$.").split(".") if item][:32]:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _score_case(
    case: dict[str, Any],
    *,
    output: Any,
    execution_status: str,
    latency_ms: float | None,
    cost_usd: float | None,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    earned = 0.0
    total = 0.0
    required_passed = True
    for index, scorer in enumerate(case["scorers"]):
        scorer_type = str(scorer["type"])
        weight = float(scorer.get("weight", 1.0))
        required = bool(scorer.get("required", True))
        passed = False
        actual: Any = output
        expected = scorer.get("value", case.get("expected"))
        try:
            if scorer_type == "status":
                expected = str(scorer.get("value") or "completed")
                actual = execution_status
                passed = actual == expected
            elif scorer_type == "exact_match":
                actual = _read_path(output, str(scorer.get("path") or ""))
                passed = actual == expected
            elif scorer_type == "contains":
                actual = str(_read_path(output, str(scorer.get("path") or "")) or "")
                expected = str(expected or "")
                passed = bool(expected) and expected in actual
            elif scorer_type == "not_contains":
                actual = str(_read_path(output, str(scorer.get("path") or "")) or "")
                expected = str(expected or "")
                passed = bool(expected) and expected not in actual
            elif scorer_type == "matches_regex":
                actual = str(_read_path(output, str(scorer.get("path") or "")) or "")
                expected = str(expected or "")
                passed = bool(expected) and re.search(expected, actual) is not None
            elif scorer_type == "json_schema":
                schema = scorer.get("schema")
                if not isinstance(schema, dict):
                    raise ValueError("json_schema scorer requires schema")
                Draft202012Validator.check_schema(schema)
                candidate = _read_path(output, str(scorer.get("path") or ""))
                Draft202012Validator(schema).validate(candidate)
                actual = type(candidate).__name__
                expected = "schema_valid"
                passed = True
            elif scorer_type == "json_path_equals":
                actual = _read_path(output, str(scorer.get("path") or ""))
                passed = actual == expected
            elif scorer_type == "json_path_exists":
                actual = _read_path(output, str(scorer.get("path") or ""))
                expected = "non_null"
                passed = actual is not None
            elif scorer_type == "list_min_items":
                actual = _read_path(output, str(scorer.get("path") or ""))
                expected = int(scorer.get("value") or 1)
                passed = isinstance(actual, list) and len(actual) >= expected
            elif scorer_type == "numeric_range":
                actual = _read_path(output, str(scorer.get("path") or ""))
                number = float(actual)
                minimum = float(scorer.get("min", "-inf"))
                maximum = float(scorer.get("max", "inf"))
                expected = {"min": minimum, "max": maximum}
                passed = minimum <= number <= maximum
            elif scorer_type == "max_latency_ms":
                actual = latency_ms
                expected = float(scorer["value"])
                passed = actual is not None and float(actual) <= expected
            elif scorer_type == "max_cost_usd":
                actual = cost_usd
                expected = float(scorer["value"])
                passed = actual is not None and float(actual) <= expected
        except (JsonSchemaError, TypeError, ValueError):
            passed = False
        total += weight
        if passed:
            earned += weight
        if required and not passed:
            required_passed = False
        results.append(
            {
                "index": index,
                "type": scorer_type,
                "passed": passed,
                "required": required,
                "weight": weight,
                "expected": expected,
                "actual": actual,
            }
        )
    score = earned / total if total else 0.0
    minimum = float(case.get("min_score", 1.0))
    passed = required_passed and score >= minimum
    return {
        "status": "passed" if passed else "failed",
        "score": score,
        "scorer_results": results,
    }


async def require_release_gate(
    store: Any,
    *,
    target_type: str,
    target_id: str,
    target_revision_id: str,
    purpose: str,
    actor_id: str,
) -> dict[str, Any]:
    decision = await asyncio.to_thread(
        store.evaluate_release_gate,
        target_type=target_type,
        target_id=target_id,
        target_revision_id=target_revision_id,
        purpose=purpose,
        actor_id=actor_id,
        decision_id=f"gate_{uuid4().hex}",
    )
    if decision["required"] and not decision["passed"]:
        failed = [
            f"{item['suite_id']}@{item['suite_version']}:{item['status']}"
            for item in decision["requirements"]
            if not item["passed"]
        ]
        raise ConflictError(f"release gate failed: {', '.join(failed)}")
    return decision


class EvalService:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def save_suite(self, value: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        suite_id = str(value.get("suite_id") or "")
        version = int(value.get("version") or 0)
        cases = list(value.get("cases") or [])
        target_types = sorted({str(item) for item in value.get("target_types") or []})
        if not suite_id or version < 1 or not 1 <= len(cases) <= 1000:
            raise ValidationError("evaluation suite requires id, version, and 1-1000 cases")
        if not set(target_types) or not set(target_types) <= _TARGET_TYPES:
            raise ValidationError("evaluation suite target_types are invalid")
        thresholds = dict(value.get("thresholds") or {})
        min_pass_rate = float(thresholds.get("min_pass_rate", 1.0))
        min_average = float(thresholds.get("min_average_score", 0.0))
        max_total_cost = thresholds.get("max_total_cost_usd")
        max_p95_latency = thresholds.get("max_p95_latency_ms")
        min_cost_coverage = float(thresholds.get("min_cost_coverage", 0.0))
        if not 0 <= min_pass_rate <= 1 or not 0 <= min_average <= 1:
            raise ValidationError("evaluation suite thresholds must be between 0 and 1")
        if (
            (max_total_cost is not None and float(max_total_cost) < 0)
            or (max_p95_latency is not None and float(max_p95_latency) < 0)
            or not 0 <= min_cost_coverage <= 1
        ):
            raise ValidationError("evaluation suite cost or latency thresholds are invalid")
        seen: set[str] = set()
        normalized_cases: list[dict[str, Any]] = []
        for case in cases:
            case_id = str(case.get("case_id") or "")
            scorers = list(case.get("scorers") or [])
            if not case_id or case_id in seen or not scorers or len(scorers) > 32:
                raise ValidationError("evaluation case ids must be unique and have scorers")
            seen.add(case_id)
            for scorer in scorers:
                scorer_type = str(scorer.get("type") or "")
                if scorer_type not in _SCORER_TYPES:
                    raise ValidationError(f"unsupported evaluation scorer: {scorer_type}")
                weight = float(scorer.get("weight", 1.0))
                if not 0 < weight <= 100:
                    raise ValidationError("evaluation scorer weight must be between 0 and 100")
                if scorer_type == "json_schema":
                    try:
                        Draft202012Validator.check_schema(dict(scorer.get("schema") or {}))
                    except JsonSchemaError as exc:
                        raise ValidationError("evaluation scorer schema is invalid") from exc
            normalized = {
                "case_id": case_id,
                "name": str(case.get("name") or case_id),
                "input": dict(case.get("input") or {}),
                "expected": case.get("expected"),
                "scorers": scorers,
                "tags": [str(item) for item in case.get("tags") or []],
                "min_score": float(case.get("min_score", 1.0)),
            }
            _bounded_json(normalized, label=f"evaluation case {case_id}")
            normalized_cases.append(normalized)
        suite = {
            "suite_id": suite_id,
            "version": version,
            "name": str(value.get("name") or suite_id),
            "description": str(value.get("description") or ""),
            "status": str(value.get("status") or "active"),
            "target_types": target_types,
            "thresholds": {
                "min_pass_rate": min_pass_rate,
                "min_average_score": min_average,
                "max_total_cost_usd": (
                    float(max_total_cost) if max_total_cost is not None else None
                ),
                "max_p95_latency_ms": (
                    float(max_p95_latency) if max_p95_latency is not None else None
                ),
                "min_cost_coverage": min_cost_coverage,
            },
            "created_by": actor_id,
        }
        try:
            return await asyncio.to_thread(
                self.store.save_eval_suite, suite=suite, cases=normalized_cases
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc

    async def list_suites(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.store.list_eval_suites)

    async def create_run(self, value: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        target_type = str(value.get("target_type") or "")
        if target_type not in _TARGET_TYPES:
            raise ValidationError("evaluation target_type is invalid")
        request = {
            "suite_id": str(value.get("suite_id") or ""),
            "suite_version": int(value.get("suite_version") or 0),
            "target_type": target_type,
            "target_id": str(value.get("target_id") or ""),
            "target_revision_id": str(value.get("target_revision_id") or ""),
        }
        if not all(request.values()):
            raise ValidationError("evaluation run target and suite are required")
        idempotency_key = str(value.get("idempotency_key") or uuid4().hex)
        request_hash = payload_hash({**request, "idempotency_key": idempotency_key})
        run, _created = await asyncio.to_thread(
            self.store.create_eval_run,
            value={
                **request,
                "eval_run_id": f"evalrun_{request_hash}",
                "request_hash": request_hash,
                "created_by": actor_id,
            },
        )
        return run

    async def record_observation(
        self, eval_run_id: str, value: dict[str, Any]
    ) -> dict[str, Any]:
        run = await asyncio.to_thread(self.store.get_eval_run, eval_run_id)
        if run is None:
            raise NotFoundError("evaluation run not found")
        suite = await asyncio.to_thread(
            self.store.get_eval_suite, run["suite_id"], run["suite_version"]
        )
        case_id = str(value.get("case_id") or "")
        case = next((item for item in suite["cases"] if item["case_id"] == case_id), None)
        if case is None:
            raise ValidationError("evaluation case does not belong to run suite")
        output = value.get("output")
        _bounded_json(output, label="evaluation observation output")
        latency = value.get("latency_ms")
        cost = value.get("cost_usd")
        scored = _score_case(
            case,
            output=output,
            execution_status=str(value.get("status") or "completed"),
            latency_ms=float(latency) if latency is not None else None,
            cost_usd=float(cost) if cost is not None else None,
        )
        try:
            return await asyncio.to_thread(
                self.store.record_eval_case_result,
                eval_run_id,
                result={
                    "case_id": case_id,
                    **scored,
                    "output": output,
                    "metrics": {
                        "latency_ms": latency,
                        "cost_usd": cost,
                        **dict(value.get("metadata") or {}),
                    },
                },
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc

    async def finalize_run(self, eval_run_id: str) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(self.store.finalize_eval_run, eval_run_id)
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc

    async def list_runs(
        self, *, target_type: str | None, target_id: str | None, limit: int
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.store.list_eval_runs,
            target_type=target_type,
            target_id=target_id,
            limit=limit,
        )

    async def save_schedule(
        self, value: dict[str, Any], *, actor_id: str
    ) -> dict[str, Any]:
        policy_id = str(value.get("policy_id") or "").strip()
        target_type = str(value.get("target_type") or "")
        suite_id = str(value.get("suite_id") or "")
        suite_version = int(value.get("suite_version") or 0)
        cadence_seconds = int(value.get("cadence_seconds") or 0)
        if (
            not policy_id
            or target_type not in _TARGET_TYPES
            or not suite_id
            or suite_version < 1
            or not 60 <= cadence_seconds <= 31_536_000
        ):
            raise ValidationError("evaluation schedule identity or cadence is invalid")
        configuration = dict(value.get("execution_configuration") or {})
        max_concurrency = int(configuration.get("max_concurrency", 4))
        timeout = float(configuration.get("case_timeout_seconds", 300))
        if not 1 <= max_concurrency <= 16 or not 1 <= timeout <= 3600:
            raise ValidationError("evaluation schedule execution limits are invalid")
        try:
            return await asyncio.to_thread(
                self.store.upsert_eval_schedule_policy,
                value={
                    "policy_id": policy_id,
                    "suite_id": suite_id,
                    "suite_version": suite_version,
                    "target_type": target_type,
                    "target_id": str(value.get("target_id") or ""),
                    "target_revision_id": str(value.get("target_revision_id") or ""),
                    "cadence_seconds": cadence_seconds,
                    "enabled": bool(value.get("enabled", True)),
                    "execution_configuration": {
                        "max_concurrency": max_concurrency,
                        "case_timeout_seconds": timeout,
                    },
                    "next_run_at": value.get("next_run_at"),
                    "created_by": actor_id,
                },
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc

    async def list_schedules(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.store.list_eval_schedule_policies)

    async def save_release_gate(
        self, value: dict[str, Any], *, actor_id: str
    ) -> dict[str, Any]:
        target_type = str(value.get("target_type") or "")
        requirements = list(value.get("requirements") or [])
        if target_type not in _TARGET_TYPES or not requirements:
            raise ValidationError("release gate target and requirements are required")
        normalized: list[dict[str, Any]] = []
        for requirement in requirements:
            suite_id = str(requirement.get("suite_id") or "")
            suite_version = int(requirement.get("suite_version") or 0)
            suite = await asyncio.to_thread(
                self.store.get_eval_suite, suite_id, suite_version
            )
            if suite is None or suite["status"] != "active":
                raise ValidationError(f"active evaluation suite not found: {suite_id}")
            if target_type not in suite["target_types"]:
                raise ValidationError("release gate suite does not support target type")
            min_rate = float(requirement.get("min_pass_rate", 1.0))
            max_age = int(requirement.get("max_age_hours", 168))
            max_total_cost = requirement.get("max_total_cost_usd")
            max_p95_latency = requirement.get("max_p95_latency_ms")
            min_cost_coverage = float(requirement.get("min_cost_coverage", 0.0))
            if (
                not 0 <= min_rate <= 1
                or not 1 <= max_age <= 8760
                or (max_total_cost is not None and float(max_total_cost) < 0)
                or (max_p95_latency is not None and float(max_p95_latency) < 0)
                or not 0 <= min_cost_coverage <= 1
            ):
                raise ValidationError("release gate requirement limits are invalid")
            normalized.append(
                {
                    "suite_id": suite_id,
                    "suite_version": suite_version,
                    "min_pass_rate": min_rate,
                    "max_age_hours": max_age,
                    "max_total_cost_usd": (
                        float(max_total_cost) if max_total_cost is not None else None
                    ),
                    "max_p95_latency_ms": (
                        float(max_p95_latency) if max_p95_latency is not None else None
                    ),
                    "min_cost_coverage": min_cost_coverage,
                    "require_automated": bool(
                        requirement.get("require_automated", False)
                    ),
                }
            )
        return await asyncio.to_thread(
            self.store.save_release_gate_policy,
            value={
                "target_type": target_type,
                "target_id": str(value.get("target_id") or ""),
                "target_revision_id": str(value.get("target_revision_id") or ""),
                "required": bool(value.get("required", True)),
                "requirements": normalized,
                "created_by": actor_id,
            },
        )


def stable_observation_hash(output: Any) -> str:
    """Public helper for CI adapters to attest the exact scored output."""

    encoded = json.dumps(output, ensure_ascii=False, sort_keys=True, default=str).encode()
    return sha256(encoded).hexdigest()
