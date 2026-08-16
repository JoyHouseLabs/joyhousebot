"""Deterministic admission and full-input budget allocation for model Turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from porthouse.agent.context_manifest import estimate_tokens, source_entry, stable_hash
from porthouse.runtime.context import ContextBudgetExceededError


def context_candidate(
    *,
    candidate_id: str,
    target: str,
    content: Any,
    source_keys: list[tuple[str, str]],
    priority: int,
    required: bool,
    order: int,
    separator: str = "",
) -> dict[str, Any]:
    """Create a private in-memory candidate; its content is never persisted."""

    return {
        "candidate_id": candidate_id,
        "target": target,
        "content": content,
        "source_keys": tuple(source_keys),
        "priority": int(priority),
        "required": bool(required),
        "order": int(order),
        "separator": separator,
    }


@dataclass(slots=True)
class PreparedContext:
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    entries: list[dict[str, Any]]
    base_message_count: int
    estimated_tokens: int
    excluded_tokens: int
    budget_strategy: str


def _source_key(item: dict[str, Any]) -> tuple[str, str]:
    return str(item["source_kind"]), str(item["source_id"])


def _dynamic_candidates(
    messages: list[dict[str, Any]], start_order: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        role = str(message.get("role") or "unknown")
        kind = {
            "assistant": "assistant_turn",
            "tool": "tool_result",
            "user": "loop_followup",
            "system": "runtime_instruction",
        }.get(role, "runtime_message")
        source = source_entry(
            source_kind=kind,
            source_id=f"dynamic:{index}",
            content=message,
            classification="confidential",
            authority="runtime" if role != "user" else "user",
            freshness="turn",
            priority=95 if role == "tool" else 90,
            included_reason="required_by_model_protocol",
            metadata={"role": role, "dynamic_index": index},
        )
        sources.append(source)
        candidates.append(
            context_candidate(
                candidate_id=f"dynamic:{index}",
                target="message",
                content=dict(message),
                source_keys=[_source_key(source)],
                priority=int(source["priority"]),
                required=True,
                order=start_order + index,
            )
        )
    return candidates, sources


def _tool_candidates(
    tools: list[dict[str, Any]], start_order: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for index, tool in enumerate(tools):
        function = dict(tool.get("function") or {})
        name = str(function.get("name") or tool.get("name") or f"tool-{index}")
        source = source_entry(
            source_kind="tool_schema",
            source_id=f"tool:{name}",
            content=tool,
            classification="internal",
            authority="platform",
            freshness="agent_revision",
            priority=90,
            included_reason="authorized_capability",
            metadata={"tool_name": name},
        )
        sources.append(source)
        candidates.append(
            context_candidate(
                candidate_id=f"tool:{index}:{name}",
                target="tool",
                content=dict(tool),
                source_keys=[_source_key(source)],
                priority=90,
                required=True,
                order=start_order + index,
            )
        )
    return candidates, sources


def _render(
    candidates: list[dict[str, Any]], selected: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    system_parts: list[str] = []
    base_messages: list[dict[str, Any]] = []
    dynamic_messages: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    for item in sorted(candidates, key=lambda value: int(value["order"])):
        if item["candidate_id"] not in selected:
            continue
        target = item["target"]
        if target == "system":
            prefix = str(item.get("separator") or "") if system_parts else ""
            system_parts.append(prefix + str(item["content"]))
        elif target == "message":
            message = dict(item["content"])
            if str(item["candidate_id"]).startswith("dynamic:"):
                dynamic_messages.append(message)
            else:
                base_messages.append(message)
        elif target == "tool":
            tools.append(dict(item["content"]))
    messages = [{"role": "system", "content": "".join(system_parts)}] if system_parts else []
    messages.extend(base_messages)
    base_count = len(messages)
    messages.extend(dynamic_messages)
    return messages, tools, base_count


def _total_tokens(
    candidates: list[dict[str, Any]], selected: set[str]
) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]], int]:
    messages, tools, base_count = _render(candidates, selected)
    return estimate_tokens(messages) + estimate_tokens(tools), messages, tools, base_count


def _mark_candidate(
    entries: list[dict[str, Any]],
    candidate: dict[str, Any],
    *,
    included: bool,
    reason: str,
) -> None:
    keys = set(candidate["source_keys"])
    for entry in entries:
        if _source_key(entry) not in keys:
            continue
        entry["included"] = included
        entry["included_reason"] = reason if included else None
        entry["excluded_reason"] = None if included else reason


def _compress_tool_results(
    candidates: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    selected: set[str],
    budget: int,
) -> None:
    """Compress protocol-required Tool results deterministically, oldest/largest first."""

    compressible = [
        item
        for item in candidates
        if item["candidate_id"] in selected
        and item["target"] == "message"
        and dict(item["content"]).get("role") == "tool"
        and isinstance(dict(item["content"]).get("content"), str)
        and len(str(dict(item["content"])["content"])) > 512
    ]
    compressible.sort(
        key=lambda item: (-len(str(dict(item["content"])["content"])), int(item["order"]))
    )
    for candidate in compressible:
        current, _messages, _tools, _base = _total_tokens(candidates, selected)
        if current <= budget:
            return
        message = dict(candidate["content"])
        original = str(message["content"])
        overflow_chars = max(256, (current - budget) * 4)
        target = max(512, len(original) - overflow_chars - 96)
        if target >= len(original):
            continue
        head = max(256, target * 2 // 3)
        tail = max(128, target - head)
        marker = "\n\n[... tool result compressed for context budget ...]\n\n"
        message["content"] = original[:head] + marker + original[-tail:]
        candidate["content"] = message
        effective_hash = stable_hash(message)
        for entry in entries:
            if _source_key(entry) not in set(candidate["source_keys"]):
                continue
            entry["included_reason"] = "compressed_for_context_budget"
            entry.setdefault("metadata", {})["compression"] = {
                "method": "head_tail_v1",
                "original_tokens": estimate_tokens(original),
                "effective_tokens": estimate_tokens(message["content"]),
                "effective_content_hash": effective_hash,
            }


def allocate_context(
    *,
    base_candidates: list[dict[str, Any]],
    base_sources: list[dict[str, Any]],
    dynamic_messages: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    budget_tokens: int | None = None,
) -> PreparedContext:
    """Apply one deterministic budget to every component entering the model."""

    candidates = [
        {
            **item,
            "content": dict(item["content"])
            if isinstance(item["content"], dict)
            else item["content"],
        }
        for item in base_candidates
    ]
    entries = [{**item, "metadata": dict(item.get("metadata") or {})} for item in base_sources]
    dynamic, dynamic_sources = _dynamic_candidates(dynamic_messages or [], 100_000)
    tool_items, tool_sources = _tool_candidates(tools or [], 200_000)
    candidates.extend(dynamic)
    candidates.extend(tool_items)
    entries.extend(dynamic_sources)
    entries.extend(tool_sources)

    admitted = [item for item in candidates if item["source_keys"]]
    required = {str(item["candidate_id"]) for item in admitted if item["required"]}
    optional = sorted(
        (item for item in admitted if not item["required"]),
        key=lambda item: (-int(item["priority"]), -int(item["order"])),
    )
    bounded = budget_tokens is not None and budget_tokens > 0
    selected = set(required)
    if bounded:
        _compress_tool_results(candidates, entries, selected, int(budget_tokens))
        required_tokens, _messages, _tools, _base_count = _total_tokens(candidates, selected)
        if required_tokens > int(budget_tokens):
            raise ContextBudgetExceededError(
                budget_tokens=int(budget_tokens), required_tokens=required_tokens
            )
        for item in optional:
            trial = {*selected, str(item["candidate_id"])}
            total, _messages, _tools, _base_count = _total_tokens(candidates, trial)
            if total <= int(budget_tokens):
                selected = trial
                _mark_candidate(entries, item, included=True, reason="selected_by_context_priority")
            else:
                _mark_candidate(
                    entries,
                    item,
                    included=False,
                    reason="lower_priority_context_budget",
                )
    else:
        selected.update(str(item["candidate_id"]) for item in optional)
    for item in admitted:
        if item["required"]:
            keys = set(item["source_keys"])
            compressed = any(
                _source_key(entry) in keys
                and bool((entry.get("metadata") or {}).get("compression"))
                for entry in entries
            )
            _mark_candidate(
                entries,
                item,
                included=True,
                reason=(
                    "compressed_for_context_budget" if compressed else "required_context_contract"
                ),
            )
    total, messages, selected_tools, base_count = _total_tokens(candidates, selected)
    excluded_tokens = sum(int(item["estimated_tokens"]) for item in entries if not item["included"])
    return PreparedContext(
        messages=messages,
        tools=selected_tools,
        entries=entries,
        base_message_count=base_count,
        estimated_tokens=total,
        excluded_tokens=excluded_tokens,
        budget_strategy="priority_budget_v1" if bounded else "unbounded_v1",
    )
