"""Business-neutral projection helpers for the Dinq run workspace.

The runtime stores opaque artifacts and events.  This module is deliberately a
thin read model: it recognizes the public Dinq artifact conventions without
putting Dinq business logic into the runtime scheduler or storage schema.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, is_dataclass
from typing import Any

from joyhousebot.runtime.narrative import public_event_dict

_COLLECTION_TYPES = {
    "dinq.candidates.collection",
    "dinq.candidates.aggregate",
    "dinq.search.results",
    "dinq.talent.filter.output",
}
_PROFILE_TYPES = {
    "dinq.candidate.profile",
    "dinq.candidate.profile.merge",
    "dinq.candidate.enrichment",
    "dinq.candidate.detail",
}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _artifact_type(artifact: dict[str, Any]) -> str:
    value = artifact.get("artifact_type") or artifact.get("type") or artifact.get("name")
    return str(value or "").strip().lower()


def _content(artifact: dict[str, Any]) -> Any:
    return artifact.get("content", artifact.get("data", artifact.get("payload")))


def _candidate_id(value: dict[str, Any]) -> str | None:
    for key in ("candidate_id", "candidateId", "user_id", "userId", "identifier", "id"):
        item = _text(value.get(key))
        if item:
            return item
    profile = value.get("profile")
    if isinstance(profile, dict):
        return _candidate_id(profile)
    return None


def _candidate_from(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    item = dict(value)
    identifier = _candidate_id(item)
    if not identifier:
        return None
    profile = item.get("profile") if isinstance(item.get("profile"), dict) else {}
    name = _text(item.get("name")) or _text(profile.get("name")) or identifier
    title = _text(item.get("title")) or _text(item.get("headline")) or _text(profile.get("title")) or _text(profile.get("headline"))
    company = _text(item.get("company")) or _text(item.get("company_name")) or _text(profile.get("company")) or _text(profile.get("company_name"))
    score = item.get("match_score", item.get("score", item.get("match")))
    try:
        score = float(score) if score is not None else None
    except (TypeError, ValueError):
        score = None
    reasons = item.get("match_reasons", item.get("match_reason", item.get("reasons", [])))
    if isinstance(reasons, str):
        reasons = [reasons]
    if not isinstance(reasons, list):
        reasons = []
    sources = item.get("sources", item.get("source", []))
    if isinstance(sources, str):
        sources = [sources]
    if not isinstance(sources, list):
        sources = []
    return {
        "candidate_id": identifier,
        "name": name,
        "title": title,
        "company": company,
        "match_score": score,
        "match_reasons": [str(reason) for reason in reasons],
        "sources": sources,
        "profile": profile or item.get("profile"),
        "enrichment": item.get("enrichment") if isinstance(item.get("enrichment"), dict) else None,
        "enrichment_status": _text(item.get("enrichment_status")) or _text(item.get("enrichmentStatus")) or "not_requested",
        "evidence": item.get("evidence", []),
    }


def _items(value: Any) -> Iterable[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("candidates", "items", "results", "data", "output", "profile", "candidate", "payload", "result"):
            nested = value.get(key)
            if isinstance(nested, list):
                return nested
        if _candidate_id(value):
            return [value]
    return []


def build_dinq_projection(
    *,
    run: Any,
    artifacts: list[dict[str, Any]],
    events: list[Any],
    invocations: list[Any],
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Build a stable, UI-oriented projection from opaque runtime records."""
    candidates: dict[str, dict[str, Any]] = {}
    profiles: dict[str, dict[str, Any]] = {}

    def ingest(kind: str, payload: Any) -> None:
        if kind in _COLLECTION_TYPES or "candidate" in kind or "talent" in kind:
            for raw in _items(payload):
                candidate = _candidate_from(raw)
                if candidate:
                    candidates.setdefault(candidate["candidate_id"], {}).update(candidate)
        if kind in _PROFILE_TYPES or kind.endswith(".enrich"):
            for raw in _items(payload):
                candidate = _candidate_from(raw)
                if candidate:
                    profiles[candidate["candidate_id"]] = candidate

    for artifact in artifacts:
        ingest(_artifact_type(artifact), _content(artifact))
    # Tool results are durable on invocation rows even when the plugin does
    # not emit a separate runtime Artifact.  Read them as a fallback so the
    # workspace works for both direct Tool Runs and coordinator Tasks.
    for invocation in invocations:
        if hasattr(invocation, "to_dict"):
            row = invocation.to_dict()
        elif isinstance(invocation, dict):
            row = dict(invocation)
        else:
            row = vars(invocation) if hasattr(invocation, "__dict__") else {}
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        ingest(str(row.get("capability_id") or ""), result.get("data", result))
        for artifact in result.get("artifacts") or []:
            if isinstance(artifact, dict):
                ingest(_artifact_type(artifact), _content(artifact))
    for identifier, profile in profiles.items():
        current = candidates.setdefault(identifier, {"candidate_id": identifier, "name": identifier})
        for key, value in profile.items():
            if value not in (None, [], {}, "not_requested"):
                current[key] = value
        if current.get("enrichment_status") == "not_requested":
            current["enrichment_status"] = "ready"

    ordered = list(candidates.values())
    ordered.sort(key=lambda item: (item.get("match_score") is None, -(item.get("match_score") or 0), item["name"].lower()))
    selected = next((item for item in ordered if item["candidate_id"] == candidate_id), None) if candidate_id else None
    event_rows: list[dict[str, Any]] = []
    for event in events:
        if hasattr(event, "to_dict"):
            row = public_event_dict(event)
        elif is_dataclass(event):
            row = asdict(event)
        elif isinstance(event, dict):
            row = dict(event)
        else:
            row = vars(event)
        event_type = str(row.get("type") or "")
        if event_type in {"message.delta", "usage.updated"}:
            continue
        event_rows.append({
            "event_id": row.get("event_id"),
            "sequence": row.get("sequence"),
            "type": event_type,
            "phase": row.get("phase"),
            "status": row.get("status"),
            "summary": row.get("summary") or event_type,
            "data": row.get("data") or {},
            "created_at": row.get("created_at"),
        })
    if hasattr(run, "to_dict"):
        record = run.to_dict()
    elif is_dataclass(run):
        record = asdict(run)
    elif isinstance(run, dict):
        record = dict(run)
    else:
        record = vars(run)
    options = record.get("options") if isinstance(record.get("options"), dict) else {}
    status = record.get("status")
    verified = sum(1 for item in ordered if item.get("enrichment_status") in {"ready", "verified", "completed"})
    return {
        "schema_version": 1,
        "view": "dinq.search",
        "run": record,
        "session": {"session_id": record.get("session_id"), "agent_id": record.get("agent_id")},
        "search": {
            "query": record.get("prompt") or options.get("query") or "",
            "status": status,
            "phase": record.get("current_phase"),
            "next_action": record.get("next_action"),
            "summary": record.get("status_summary"),
            "total_candidates": len(ordered),
            "verified_candidates": verified,
            "tool_calls": len(invocations),
        },
        "candidates": ordered,
        "selected_candidate_id": selected.get("candidate_id") if selected else None,
        "selected_candidate": selected,
        "activity": event_rows,
        "events_cursor": max((int(row.get("sequence") or 0) for row in event_rows), default=0),
    }
