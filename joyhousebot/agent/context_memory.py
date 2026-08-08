"""Memory admission and source descriptors for model context."""

from __future__ import annotations

from typing import Any

from joyhousebot.agent.context_manifest import source_entry, stable_hash
from joyhousebot.agent.memory import MemoryStore


def build_memory_context(
    builder: Any, scope_key: str | None = None
) -> tuple[str, list[dict[str, Any]]]:
    """Resolve policy-admitted Memory text with content-free provenance."""
    if not builder.memory_policy.can_read_context:
        return "", []
    memory_first = False
    include_daily = False
    try:
        from joyhousebot.config.access import get_config

        retrieval = getattr(getattr(get_config(), "tools", None), "retrieval", None)
        if retrieval is not None:
            memory_first = getattr(retrieval, "memory_first", False)
            include_daily = getattr(retrieval, "memory_include_daily_in_context", False)
    except Exception:
        pass
    store = MemoryStore(builder.runtime_store, scope_key=scope_key) if scope_key else builder.memory
    sections: list[str] = []
    sources: list[dict[str, Any]] = []
    scope_id = stable_hash(scope_key or "shared")[:16]

    def admit(kind: str, source_id: str, content: str, priority: int, freshness: str) -> None:
        sections.append(content)
        sources.append(
            source_entry(
                source_kind=kind,
                source_id=source_id,
                content=content,
                classification="confidential",
                authority="user",
                freshness=freshness,
                priority=priority,
                included_reason="memory_policy_allowed",
            )
        )

    if builder.memory_policy.layer_enabled("profile", "read"):
        profile = store.read_profile()
        if profile:
            admit(
                "memory_profile",
                f"memory:profile:{scope_id}",
                f"## User Profile\n{profile}",
                75,
                "current_memory",
            )
    if builder.memory_policy.layer_enabled("long_term", "read"):
        long_term = store.read_long_term()
        if long_term:
            admit(
                "memory_long_term",
                f"memory:long-term:{scope_id}",
                f"## Long-term Memory\n{long_term}",
                70,
                "current_memory",
            )
    memory = "\n\n".join(sections)
    if include_daily and builder.memory_policy.layer_enabled("episodic", "read"):
        daily = store.read_daily_logs_today_yesterday()
        if daily:
            rendered = "## Recent daily log (today + yesterday)\n\n" + daily
            memory = (memory + "\n\n" + rendered) if memory else rendered
            sources.append(
                source_entry(
                    source_kind="memory_episodic",
                    source_id=f"memory:daily:{scope_id}",
                    content=rendered,
                    classification="confidential",
                    authority="user",
                    freshness="today_yesterday",
                    priority=60,
                    included_reason="memory_policy_allowed",
                )
            )
    if memory and memory_first:
        guidance = (
            "When answering, consider consulting memory first: read memory/.abstract or "
            'use retrieve(scope="memory", query=...) before searching the knowledge base.'
        )
        memory += "\n\n" + guidance
        sources.append(
            source_entry(
                source_kind="memory_policy",
                source_id="memory:retrieval-policy:v1",
                content=guidance,
                classification="internal",
                authority="system",
                freshness="configuration",
                priority=80,
                included_reason="memory_first_enabled",
            )
        )
    return memory, sources
