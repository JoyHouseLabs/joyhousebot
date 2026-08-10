"""Build the global Agent catalog used only by execution workers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from joyhousebot.agent.executor import NativeAgentExecutor
from joyhousebot.providers.base import LLMProvider
from joyhousebot.providers.factory import create_model_provider
from joyhousebot.providers.registry import get_provider_registry
from joyhousebot.session.runtime_manager import RuntimeSessionManager


def build_provider(
    config: Any,
    model: str,
    *,
    client: httpx.AsyncClient | None = None,
    model_policy: dict[str, Any] | None = None,
) -> LLMProvider:
    return create_model_provider(
        config=config,
        model=model,
        client=client,
        model_policy=model_policy,
    )


def build_agent_executor(
    *,
    config: Any,
    store: Any,
    definition: Any,
    revision: Any,
    cron_service: Any | None = None,
    outbound_sink: Any = None,
    client: httpx.AsyncClient | None = None,
) -> NativeAgentExecutor:
    """Construct one immutable Agent revision runtime."""
    policy = revision.model_policy
    model = str(policy["primary"])
    provider = build_provider(config, model, client=client, model_policy=policy)
    for manifest in get_provider_registry(config).manifests():
        store.upsert_plugin_release(manifest.to_release_dict())
    scratch_root = Path(config.runtime.scratch_root).expanduser()
    scratch_root.mkdir(parents=True, exist_ok=True)
    return NativeAgentExecutor(
        provider=provider,
        scratch_root=scratch_root / definition.agent_id,
        agent_revision=revision,
        model=model,
        model_fallbacks=list(policy.get("fallbacks") or []),
        temperature=float(policy.get("temperature", 0.3)),
        max_tokens=int(policy.get("max_tokens", 8192)),
        max_iterations=int(policy.get("max_tool_iterations", 20)),
        memory_window=int(policy.get("memory_window", 50)),
        max_context_tokens=(
            int(policy["max_context_tokens"])
            if policy.get("max_context_tokens") is not None
            else None
        ),
        cron_service=cron_service,
        session_manager=RuntimeSessionManager(store, namespace=definition.agent_id),
        config=config,
        outbound_sink=outbound_sink,
    )


def build_agents(
    *, config: Any, store: Any, cron_service: Any | None = None, outbound_sink: Any = None
) -> tuple[dict[str, NativeAgentExecutor], str]:
    profiles = store.list_published_agent_profiles()
    if not profiles:
        raise RuntimeError("at least one published database Agent is required")
    agents: dict[str, NativeAgentExecutor] = {}
    default_id = ""
    shared_http_client: httpx.AsyncClient | None = None
    for profile in profiles:
        definition = profile.definition
        revision = profile.revision
        executor = build_agent_executor(
            config=config,
            store=store,
            definition=definition,
            revision=revision,
            cron_service=cron_service,
            outbound_sink=outbound_sink,
            client=shared_http_client,
        )
        if shared_http_client is None:
            shared_http_client = getattr(executor.provider, "http_client", None)
        agents[revision.revision_id] = executor
        if definition.current_revision_id == revision.revision_id:
            agents[definition.agent_id] = executor
        if definition.is_default and definition.current_revision_id == revision.revision_id:
            default_id = definition.agent_id
    if not default_id:
        current = store.get_agent_profile()
        if current is None:
            raise RuntimeError("no active published default Agent exists")
        default_id = current.definition.agent_id
    return agents, default_id
