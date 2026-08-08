"""Deterministic, durable output verification shared by Agent execution paths."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from joyhousebot.orchestration.verification_policy import (
    VerifierSpec,
    normalize_verifiers,
)
from joyhousebot.runtime.action_identity import canonical_json, payload_hash
from joyhousebot.runtime.context import RunContext
from joyhousebot.runtime.structured import StructuredOutputError, parse_structured_output

VerificationEvent = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class VerificationDecision:
    passed: bool
    structured_output: Any = None
    repairable: bool = False
    repair_prompt: str | None = None
    attempt: int = 0
    input_hash: str = ""
    failures: tuple[dict[str, Any], ...] = ()


def repair_limit(context: RunContext) -> int:
    if context.max_repairs is not None:
        return max(0, int(context.max_repairs))
    return max(0, int((context.verification_policy or {}).get("max_repairs") or 0))


async def verify_output(
    context: RunContext,
    content: str | None,
    *,
    turn_id: str | None,
    attempt: int | None,
    event_callback: VerificationEvent | None = None,
) -> VerificationDecision:
    """Evaluate all required verifiers and durably record their evidence."""

    specs = normalize_verifiers(context.output_schema, context.verification_policy)
    if not specs:
        return VerificationDecision(passed=True)
    input_hash = payload_hash({"content": content})
    records = await _list_records(context)
    if attempt is None:
        existing_attempt = _passed_attempt(records, specs, input_hash)
        if existing_attempt is not None:
            structured = _structured_value(content, specs)
            return VerificationDecision(
                passed=True,
                structured_output=structured,
                attempt=existing_attempt,
                input_hash=input_hash,
            )
        attempt = max((item.attempt for item in records), default=0) + 1
    failures: list[dict[str, Any]] = []
    structured_output: Any = None
    for spec in specs:
        verification_id = _verification_id(context, turn_id, attempt, spec.verifier_id)
        await _emit(
            event_callback,
            "verification_started",
            _event_payload(spec, verification_id, attempt, turn_id),
        )
        record = await _begin_record(context, spec, verification_id, turn_id, attempt, input_hash)
        if record is not None and record.status in {"passed", "failed"}:
            passed = record.status == "passed"
            evidence = dict(record.evidence)
            error = dict(record.error or {}) or None
        else:
            passed, evidence, error, structured = await _evaluate(
                context, spec, content, input_hash
            )
            if structured is not None:
                structured_output = structured
            saved = await _complete_record(
                context,
                verification_id,
                status="passed" if passed else "failed",
                evidence=evidence,
                error=error,
                persistence_expected=record is not None,
            )
            if record is not None and saved is None:
                raise RuntimeError("verification lease was lost before completion")
        await _emit(
            event_callback,
            "verification_passed" if passed else "verification_failed",
            {
                **_event_payload(spec, verification_id, attempt, turn_id),
                "evidence": evidence,
                "error": error,
            },
        )
        if not passed and spec.required:
            failures.append(
                {
                    "verifier_id": spec.verifier_id,
                    "type": spec.verifier_type,
                    "repairable": spec.repairable,
                    "message": str((error or {}).get("message") or "verification failed"),
                }
            )
    if failures:
        repairable = all(item["repairable"] for item in failures)
        return VerificationDecision(
            passed=False,
            repairable=repairable,
            repair_prompt=_repair_prompt(failures),
            attempt=attempt,
            input_hash=input_hash,
            failures=tuple(failures),
        )
    if structured_output is None:
        structured_output = _structured_value(content, specs)
    return VerificationDecision(
        passed=True,
        structured_output=structured_output,
        attempt=attempt,
        input_hash=input_hash,
    )


async def _evaluate(
    context: RunContext,
    spec: VerifierSpec,
    content: str | None,
    input_hash: str,
) -> tuple[bool, dict[str, Any], dict[str, Any] | None, Any]:
    evidence: dict[str, Any] = {
        "input_hash": input_hash,
        "content_length": len(content or ""),
    }
    try:
        if spec.verifier_type == "schema":
            schema = spec.policy.get("schema")
            if not isinstance(schema, dict):
                raise ValueError("schema verifier requires an object schema")
            structured = parse_structured_output(content, schema)
            evidence.update(
                {"schema_hash": payload_hash(schema), "value_type": type(structured).__name__}
            )
            return True, evidence, None, structured
        if spec.verifier_type == "artifact":
            artifact_evidence = await _verify_artifacts(context, spec.policy, content)
            evidence.update(artifact_evidence)
            return True, evidence, None, None
        _verify_deterministic(content, spec.policy)
        evidence["rule"] = str(spec.policy.get("rule") or "non_empty")
        return True, evidence, None, None
    except (StructuredOutputError, ValueError) as exc:
        evidence["failure_hash"] = payload_hash(str(exc))
        return (
            False,
            evidence,
            {
                "code": "VERIFICATION_FAILED",
                "message": _safe_failure_message(spec.verifier_type, str(exc)),
            },
            None,
        )


async def _verify_artifacts(
    context: RunContext, policy: dict[str, Any], content: str | None
) -> dict[str, Any]:
    store = context.trace_store
    if store is None or not hasattr(store, "list_runtime_artifacts"):
        raise ValueError("artifact verification requires a durable artifact store")
    artifacts = await asyncio.to_thread(store.list_runtime_artifacts, context.run_id)
    names = _string_set(policy.get("names") or policy.get("name"))
    media_types = _string_set(policy.get("media_types") or policy.get("media_type"))
    artifact_task_id = str(
        context.metadata.get("verification_source_task_id") or context.task_id or ""
    )
    if not names or "final-output" in names:
        try:
            json.loads(content or "")
            candidate_type = "application/json"
        except (TypeError, json.JSONDecodeError):
            candidate_type = "text/plain"
        artifacts.append(
            {
                "artifact_id": f"{context.run_id}:final",
                "run_id": context.run_id,
                "task_id": artifact_task_id or None,
                "name": "final-output",
                "media_type": candidate_type,
                "content": content,
            }
        )
    scoped = [
        item
        for item in artifacts
        if not artifact_task_id or item.get("task_id") == artifact_task_id
    ]
    matched = [
        item
        for item in scoped
        if (not names or str(item.get("name")) in names)
        and (not media_types or str(item.get("media_type")) in media_types)
    ]
    minimum = max(1, int(policy.get("min_count") or 1))
    if len(matched) < minimum:
        raise ValueError(
            f"artifact requirement failed: expected at least {minimum}, found {len(matched)}"
        )
    projected = [
        {
            "artifact_id": str(item.get("artifact_id") or ""),
            "name": str(item.get("name") or ""),
            "media_type": str(item.get("media_type") or ""),
            "content_hash": _artifact_hash(item.get("content"), item.get("uri")),
        }
        for item in matched
    ]
    expected_hashes = _string_set(policy.get("hashes") or policy.get("sha256"))
    observed_hashes = {item["content_hash"] for item in projected}
    if expected_hashes and not expected_hashes.issubset(observed_hashes):
        raise ValueError("artifact requirement failed: expected content hash was not found")
    return {
        "artifact_count": len(matched),
        "artifacts": projected,
    }


def _verify_deterministic(content: str | None, policy: dict[str, Any]) -> None:
    rule = str(policy.get("rule") or "non_empty")
    text = content or ""
    if rule == "non_empty":
        if not text.strip():
            raise ValueError("final output must not be empty")
        return
    if rule == "min_length":
        configured = policy.get("min_length", policy.get("value"))
        if configured is None:
            raise ValueError("min_length verifier requires min_length")
        minimum = max(0, int(configured))
        if len(text) < minimum:
            raise ValueError(f"final output must contain at least {minimum} characters")
        return
    if rule == "contains":
        needle = str(policy.get("value") or "")
        if not needle or needle not in text:
            raise ValueError("final output does not contain the required marker")
        return
    if rule == "json_path_exists":
        path = str(policy.get("path") or "").strip()
        if not path:
            raise ValueError("json_path_exists verifier requires path")
        value: Any = json.loads(text)
        for part in [item for item in path.removeprefix("$.").split(".") if item]:
            if not isinstance(value, dict) or part not in value:
                raise ValueError(f"required JSON path is missing: {path}")
            value = value[part]
        return
    raise ValueError(f"unsupported deterministic verification rule: {rule}")


def _string_set(value: Any) -> set[str]:
    if value is None:
        return set()
    values = value if isinstance(value, list) else [value]
    return {str(item) for item in values if str(item)}


def _artifact_hash(content: Any, uri: Any) -> str:
    value = content if content is not None else uri or ""
    encoded = value if isinstance(value, str) else canonical_json(value)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _safe_failure_message(verifier_type: str, message: str) -> str:
    if verifier_type == "schema":
        if "not valid JSON" in message:
            return "final output is not valid JSON"
        return "final output does not match the required JSON Schema"
    if verifier_type == "artifact":
        return "required artifact evidence is missing or does not match policy"
    if "at least" in message:
        return message
    if "JSON path" in message:
        return "required JSON path is missing"
    if "required marker" in message:
        return "final output does not contain the required marker"
    return "deterministic output verification failed"


def _structured_value(content: str | None, specs: tuple[VerifierSpec, ...]) -> Any:
    for spec in specs:
        if spec.verifier_type == "schema" and isinstance(spec.policy.get("schema"), dict):
            return parse_structured_output(content, spec.policy["schema"])
    return None


def _passed_attempt(
    records: list[Any], specs: tuple[VerifierSpec, ...], input_hash: str
) -> int | None:
    required = {spec.verifier_id for spec in specs if spec.required}
    by_attempt: dict[int, dict[str, Any]] = {}
    for record in records:
        if record.input_hash == input_hash:
            by_attempt.setdefault(record.attempt, {})[record.verifier_id] = record
    for attempt in sorted(by_attempt, reverse=True):
        values = by_attempt[attempt]
        expected = {spec.verifier_id: spec for spec in specs if spec.required}
        if all(
            item in values
            and values[item].status == "passed"
            and values[item].verifier_type == expected[item].verifier_type
            and values[item].verifier_version == expected[item].version
            and values[item].policy == expected[item].policy
            for item in required
        ):
            return attempt
    return None


async def _list_records(context: RunContext) -> list[Any]:
    store = context.trace_store
    if store is None or not hasattr(store, "list_verification_records"):
        return []
    records = await asyncio.to_thread(store.list_verification_records, context.run_id)
    return [item for item in records if item.task_id == context.task_id]


async def _begin_record(
    context: RunContext,
    spec: VerifierSpec,
    verification_id: str,
    turn_id: str | None,
    attempt: int,
    input_hash: str,
) -> Any | None:
    store = context.trace_store
    if (
        store is None
        or not hasattr(store, "begin_verification")
        or not context.worker_id
        or (context.run_lease_version is None and context.task_lease_version is None)
    ):
        return None
    record = await asyncio.to_thread(
        store.begin_verification,
        verification_id=verification_id,
        run_id=context.run_id,
        task_id=context.task_id,
        turn_id=turn_id,
        user_id=context.user_id,
        attempt=attempt,
        verifier_id=spec.verifier_id,
        verifier_type=spec.verifier_type,
        verifier_version=spec.version,
        required=spec.required,
        repairable=spec.repairable,
        policy=spec.policy,
        input_hash=input_hash,
        worker_id=context.worker_id,
        run_lease_version=context.run_lease_version,
        task_lease_version=context.task_lease_version,
    )
    if record is None:
        raise RuntimeError("verification could not acquire the current Run lease")
    return record


async def _complete_record(
    context: RunContext,
    verification_id: str,
    *,
    status: str,
    evidence: dict[str, Any],
    error: dict[str, Any] | None,
    persistence_expected: bool,
) -> Any | None:
    if not persistence_expected:
        return None
    return await asyncio.to_thread(
        context.trace_store.complete_verification,
        verification_id,
        status=status,
        evidence=evidence,
        error=error,
        worker_id=context.worker_id,
        run_lease_version=context.run_lease_version,
        task_lease_version=context.task_lease_version,
    )


def _verification_id(
    context: RunContext, turn_id: str | None, attempt: int, verifier_id: str
) -> str:
    raw = "\0".join(
        [context.run_id, context.task_id or "", turn_id or "final", str(attempt), verifier_id]
    )
    return "ver_" + sha256(raw.encode("utf-8")).hexdigest()[:32]


def _event_payload(
    spec: VerifierSpec, verification_id: str, attempt: int, turn_id: str | None
) -> dict[str, Any]:
    return {
        "verification_id": verification_id,
        "verifier_id": spec.verifier_id,
        "verifier_type": spec.verifier_type,
        "verifier_version": spec.version,
        "required": spec.required,
        "repairable": spec.repairable,
        "attempt": attempt,
        "turn_id": turn_id,
    }


async def _emit(
    callback: VerificationEvent | None, event_type: str, payload: dict[str, Any]
) -> None:
    if callback is not None:
        await callback(event_type, payload)


def _repair_prompt(failures: list[dict[str, Any]]) -> str:
    details = "\n".join(
        f"- {item['verifier_id']} ({item['type']}): {item['message']}" for item in failures
    )
    return (
        "Your previous final answer failed required verification. Correct the result using "
        "the same goal and available tools, then return a complete replacement final answer.\n"
        f"Verification failures:\n{details}"
    )
