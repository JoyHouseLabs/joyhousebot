"""Lazy, revision-aware Agent runtime catalog for long-lived workers."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import httpx
from jsonschema import Draft202012Validator
from loguru import logger

from joyhousebot.bootstrap.agents import build_agent_executor
from joyhousebot.domain.capabilities import CapabilityKind, CapabilityRef


class AgentRuntimeCatalog:
    """Resolve immutable Agent revisions without requiring a Worker restart."""

    def __init__(self, *, config: Any, store: Any, cron_service: Any = None, outbound_sink: Any = None) -> None:
        self.config = config
        self.store = store
        self.cron_service = cron_service
        self.outbound_sink = outbound_sink
        self._lock = threading.RLock()
        self._agents: dict[str, Any] = {}
        self._shared_http_client: httpx.AsyncClient | None = None
        self._runtime: Any = None

    def resolve(self, key: str) -> Any | None:
        """Load a current Agent id or exact published revision on first use."""
        normalized = str(key or "").strip()
        if not normalized:
            return None
        with self._lock:
            cached = self._agents.get(normalized)
            if cached is not None:
                cached_revision_id = getattr(
                    getattr(cached, "agent_revision", None), "revision_id", None
                )
                if cached_revision_id == normalized:
                    return cached
                current = self.store.get_agent_profile(normalized)
                if (
                    current is not None
                    and current.revision.revision_id == cached_revision_id
                ):
                    return cached

            revision = self.store.get_agent_revision(normalized)
            profile = None
            if revision is None:
                profile = self.store.get_agent_profile(normalized)
                if profile is None:
                    return None
                revision = profile.revision
            if revision.status not in {"draft", "published"}:
                return None
            profile = profile or self.store.get_agent_profile(revision.agent_id)
            definition = (
                profile.definition
                if profile is not None
                else self.store.get_agent_definition(revision.agent_id)
            )
            if definition is None:
                return None
            revision_cached = self._agents.get(revision.revision_id)
            if revision_cached is not None:
                if definition.current_revision_id == revision.revision_id:
                    self._agents[definition.agent_id] = revision_cached
                return revision_cached
            loop = build_agent_executor(
                config=self.config,
                store=self.store,
                definition=definition,
                revision=revision,
                cron_service=self.cron_service,
                client=self._shared_http_client,
                outbound_sink=self.outbound_sink,
            )
            if self._shared_http_client is None:
                self._shared_http_client = getattr(loop.provider, "http_client", None)
            if self._runtime is not None:
                loop.subagents.set_runtime(self._runtime)
            self._agents[revision.revision_id] = loop
            if definition.current_revision_id == revision.revision_id:
                self._agents[definition.agent_id] = loop
            self._acknowledge(revision.agent_id, revision.revision_id, status="loaded")
            return loop

    def set_runtime(self, runtime: Any) -> None:
        with self._lock:
            self._runtime = runtime
            for loop in self._unique_agents():
                loop.subagents.set_runtime(runtime)
                revision = getattr(loop, "agent_revision", None)
                if revision is not None:
                    self._acknowledge(
                        revision.agent_id,
                        revision.revision_id,
                        status="loaded",
                    )

    def _acknowledge(
        self,
        agent_id: str,
        revision_id: str,
        *,
        status: str,
        error: dict[str, Any] | None = None,
    ) -> None:
        acknowledge = getattr(self.store, "acknowledge_configuration_revision", None)
        if acknowledge is None or self._runtime is None:
            return
        acknowledge(
            worker_id=self._runtime.worker_id,
            aggregate_type="agent",
            aggregate_id=agent_id,
            revision_id=revision_id,
            status=status,
            error=error,
        )

    def _acknowledge_configuration(
        self,
        item: dict[str, str],
        *,
        status: str,
        error: dict[str, Any] | None = None,
    ) -> None:
        acknowledge = getattr(self.store, "acknowledge_configuration_revision", None)
        if acknowledge is None or self._runtime is None:
            return
        acknowledge(
            worker_id=self._runtime.worker_id,
            aggregate_type=item["aggregate_type"],
            aggregate_id=item["aggregate_id"],
            revision_id=item["revision_id"],
            status=status,
            error=error,
        )

    async def refresh_pending(self) -> int:
        """Preload every revision targeted to this worker and persist its ACK."""
        if self._runtime is None:
            return 0
        pending = await asyncio.to_thread(
            self.store.list_pending_configuration_revisions, self._runtime.worker_id
        )
        loaded = 0
        for item in pending:
            revision_id = item["revision_id"]
            try:
                await asyncio.to_thread(self._preheat_configuration, item)
                loaded += 1
                await asyncio.to_thread(
                    self._acknowledge_configuration, item, status="loaded"
                )
            except Exception as exc:
                logger.exception(
                    "failed to preheat configuration rollout revision={} worker={}",
                    revision_id,
                    self._runtime.worker_id,
                )
                await asyncio.to_thread(
                    self._acknowledge_configuration,
                    item,
                    status="failed",
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
        return loaded

    def _preheat_configuration(self, item: dict[str, str]) -> None:
        aggregate_type = item["aggregate_type"]
        if aggregate_type == "agent":
            loop = self.resolve(item["revision_id"])
            if loop is None:
                raise RuntimeError("published Agent revision is unavailable")
            # resolve() also ACKs normal on-demand loads; refresh_pending's
            # generic acknowledgement below is intentionally idempotent.
            return
        if aggregate_type == "plugin":
            release = self.store.get_plugin_release(
                item["aggregate_id"], item["revision_id"]
            )
            if release is None:
                raise RuntimeError("staged plugin release is unavailable")
            expected = (
                str(release["plugin_id"]),
                str(release["version"]),
                str(release["build_digest"]),
            )
            loaded = {
                (
                    str(value.get("plugin_id") or ""),
                    str(value.get("version") or ""),
                    str(value.get("build_digest") or ""),
                )
                for value in getattr(self._runtime, "plugin_releases", ())
            }
            if expected not in loaded:
                raise RuntimeError(
                    "plugin is not installed with the exact staged version and build digest: "
                    f"{expected[0]}@{expected[1]}"
                )
            return
        loop = self.resolve(self._runtime.default_agent_id)
        if loop is None:
            raise RuntimeError("default Agent runtime is unavailable for preflight")
        registry = loop.capabilities
        if aggregate_type == "capability":
            expected = self.store.get_capability_release_definition(
                item["aggregate_id"], item["revision_id"]
            )
            if expected is None:
                raise RuntimeError("staged capability definition is unavailable")
            self._assert_capability_loaded(registry, expected)
            return
        if aggregate_type == "scenario":
            scenario = self.store.get_scenario_version(
                item["aggregate_id"], int(item["revision_id"])
            )
            if scenario is None:
                raise RuntimeError("staged scenario definition is unavailable")
            for reference in scenario.allowed_capabilities:
                if reference.kind is CapabilityKind.SKILL:
                    definition = self.store.get_capability_definition(
                        reference.capability_id, reference.version
                    )
                    if (
                        definition is None
                        or CapabilityRef.from_dict(dict(definition["ref"])).identity
                        != reference.identity
                    ):
                        raise RuntimeError(
                            "scenario Skill is not active with the exact version: "
                            f"{reference.capability_id}@{reference.version}"
                        )
                    continue
                definition = registry.get_definition(
                    reference.capability_id, reference.version
                )
                if definition is None or definition.ref.identity != reference.identity:
                    raise RuntimeError(
                        "scenario capability is not loaded with the exact plugin build: "
                        f"{reference.capability_id}@{reference.version}"
                    )
            return
        raise RuntimeError(f"unsupported configuration rollout type: {aggregate_type}")

    @staticmethod
    def _assert_capability_loaded(registry: Any, expected: dict[str, Any]) -> None:
        reference = CapabilityRef.from_dict(dict(expected["ref"]))
        for schema_name in ("input_schema", "output_schema", "configuration_schema"):
            schema = dict(expected.get(schema_name) or {})
            if schema:
                Draft202012Validator.check_schema(schema)
        if reference.kind is CapabilityKind.SKILL and str(
            expected.get("adapter") or ""
        ).startswith("prompt-skill:"):
            return
        definition = registry.get_definition(reference.capability_id, reference.version)
        if definition is None or definition.ref.identity != reference.identity:
            raise RuntimeError(
                "capability is not loaded with the exact plugin build: "
                f"{reference.capability_id}@{reference.version}"
            )
        actual = definition.to_dict()
        for field in ("adapter", "input_schema", "output_schema", "permissions"):
            if actual.get(field) != expected.get(field):
                raise RuntimeError(
                    f"loaded capability metadata mismatch for {reference.capability_id}: {field}"
                )

    async def watch(self, *, poll_interval: float = 1.0) -> None:
        """Continuously reconcile database rollouts into this worker."""
        interval = max(0.1, float(poll_interval))
        while True:
            await self.refresh_pending()
            await asyncio.sleep(interval)

    def _unique_agents(self) -> list[Any]:
        return list({id(value): value for value in self._agents.values()}.values())

    async def close(self) -> None:
        with self._lock:
            agents = self._unique_agents()
            shared_client = self._shared_http_client
            self._agents.clear()
            self._shared_http_client = None
        for agent in agents:
            agent.stop()
        await asyncio.gather(
            *(agent.close_tool_connectors() for agent in agents),
            return_exceptions=True,
        )
        # Revisions after the first share the first provider's HTTP client and
        # therefore do not own its lifecycle. Close it explicitly so hot-reload
        # and worker shutdown cannot leak sockets/file descriptors.
        if shared_client is not None:
            await shared_client.aclose()

    @property
    def loaded_revision_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                sorted(
                    key
                    for key, value in self._agents.items()
                    if getattr(getattr(value, "agent_revision", None), "revision_id", None) == key
                )
            )
