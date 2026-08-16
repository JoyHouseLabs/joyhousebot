"""Source-level context provenance and per-Turn manifest construction."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from porthouse.runtime.context import RunContext


def stable_hash(value: Any) -> str:
    """Hash a JSON-compatible value without persisting its sensitive content."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def estimate_tokens(value: Any) -> int:
    """Return the same deliberately conservative chars/4 estimate used by prompts."""

    if isinstance(value, str):
        size = len(value)
    else:
        size = len(json.dumps(value, ensure_ascii=False, default=str))
    return 4 + max(0, size) // 4


def source_entry(
    *,
    source_kind: str,
    source_id: str,
    content: Any,
    classification: str,
    authority: str,
    freshness: str,
    priority: int,
    included: bool = True,
    included_reason: str | None = None,
    excluded_reason: str | None = None,
    citation_id: str | None = None,
    redaction_policy: str = "hash_only",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a content-free source descriptor safe to carry in RunContext."""

    return {
        "source_kind": source_kind,
        "source_id": source_id,
        "classification": classification,
        "authority": authority,
        "freshness": freshness,
        "content_hash": stable_hash(content),
        "estimated_tokens": estimate_tokens(content),
        "priority": int(priority),
        "included": bool(included),
        "included_reason": included_reason if included else None,
        "excluded_reason": excluded_reason if not included else None,
        "citation_id": citation_id,
        "redaction_policy": redaction_policy,
        "metadata": dict(metadata or {}),
    }


def _dynamic_message_entries(messages: list[dict[str, Any]], start: int) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for index, message in enumerate(messages[max(0, start) :], start=max(0, start)):
        role = str(message.get("role") or "unknown")
        kind = {
            "assistant": "assistant_turn",
            "tool": "tool_result",
            "user": "loop_followup",
            "system": "runtime_instruction",
        }.get(role, "runtime_message")
        entries.append(
            source_entry(
                source_kind=kind,
                source_id=f"message:{index}",
                content=message,
                classification="confidential",
                authority="runtime" if role != "user" else "user",
                freshness="turn",
                priority=85 if role == "tool" else 75,
                included_reason="required_by_model_protocol",
                metadata={"role": role, "message_index": index},
            )
        )
    return entries


def _tool_entries(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for index, tool in enumerate(tools):
        function = dict(tool.get("function") or {})
        name = str(function.get("name") or tool.get("name") or f"tool-{index}")
        entries.append(
            source_entry(
                source_kind="tool_schema",
                source_id=f"tool:{name}",
                content=tool,
                classification="internal",
                authority="platform",
                freshness="agent_revision",
                priority=90,
                included_reason="allowed_capability",
                metadata={"tool_name": name},
            )
        )
    return entries


def build_turn_manifest(
    context: RunContext,
    *,
    turn_id: str,
    turn_index: int,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the immutable, content-free manifest persisted before a model call."""

    source_entries = entries if entries is not None else list(context.context_sources)
    manifest_entries = [dict(item) for item in source_entries]
    if entries is None:
        manifest_entries.extend(
            _dynamic_message_entries(messages, context.context_initial_message_count)
        )
        manifest_entries.extend(_tool_entries(tools))
    for ordinal, entry in enumerate(manifest_entries):
        entry["ordinal"] = ordinal
        entry["entry_id"] = (
            "ctxe_"
            + stable_hash([turn_id, ordinal, entry["source_kind"], entry["content_hash"]])[:32]
        )
    request_hash = stable_hash({"messages": messages, "tools": tools})
    excluded_tokens = sum(
        int(item["estimated_tokens"]) for item in manifest_entries if not item["included"]
    )
    estimated_tokens = estimate_tokens(messages) + estimate_tokens(tools)
    owner_scope = "user:" + stable_hash(context.user_id)[:20]
    identity = {
        "turn_id": turn_id,
        "run_id": context.run_id,
        "task_id": context.task_id,
        "scope": context.turn_scope,
        "turn_index": turn_index,
        "request_hash": request_hash,
        "entries": manifest_entries,
    }
    return {
        "manifest_id": "ctxm_" + stable_hash(turn_id)[:32],
        "turn_id": turn_id,
        "run_id": context.run_id,
        "task_id": context.task_id,
        "scope": context.turn_scope,
        "turn_index": turn_index,
        "owner_scope": owner_scope,
        "request_hash": request_hash,
        "manifest_hash": stable_hash(identity),
        "budget_tokens": context.context_budget_tokens,
        "budget_strategy": context.context_budget_strategy,
        "estimated_tokens": estimated_tokens,
        "included_tokens": estimated_tokens,
        "excluded_tokens": excluded_tokens,
        "entries": manifest_entries,
        "worker_id": context.worker_id,
        "run_lease_version": context.run_lease_version,
        "task_lease_version": context.task_lease_version,
    }
