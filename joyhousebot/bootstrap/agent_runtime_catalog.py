"""Lazy, revision-aware Agent runtime catalog for long-lived workers."""

from __future__ import annotations

import asyncio
import threading
from contextlib import AsyncExitStack
from typing import Any

import httpx
from jsonschema import Draft202012Validator
from loguru import logger

from joyhousebot.bootstrap.agents import build_agent_executor
from joyhousebot.capabilities import CapabilityRegistry
from joyhousebot.config.schema import ProviderConfig
from joyhousebot.domain.capabilities import CapabilityRef
from joyhousebot.domain.model_providers import materialize_model_provider
from joyhousebot.domain.remote_connections import materialize_remote_connection
from joyhousebot.providers.factory import create_model_provider
from joyhousebot.providers.registry import get_provider_registry


class AgentRuntimeCatalog:
    """Resolve immutable Agent revisions without requiring a Worker restart."""

    def __init__(self, *, config: Any, store: Any, cron_service: Any = None, outbound_sink: Any = None) -> None:
        self.config = config
        self.store = store
        self.cron_service = cron_service
        self.outbound_sink = outbound_sink
        self._lock = threading.RLock()
        self._agents: dict[str, Any] = {}
        self._retired_agents: list[Any] = []
        self._shared_http_client: httpx.AsyncClient | None = None
        self._retired_shared_http_clients: list[httpx.AsyncClient] = []
        self._runtime: Any = None
        self._active_model_provider_signature: tuple[tuple[str, str], ...] | None = None
        self._active_remote_connection_signature: tuple[tuple[str, str], ...] | None = None

    async def start(self) -> None:
        """Load external Tool catalogs before the Worker accepts its first Run."""
        with self._lock:
            agents = self._unique_agents()
        for agent in agents:
            await agent._connect_tool_connectors()
        self._active_model_provider_signature = self._model_provider_signature()
        self._active_remote_connection_signature = self._remote_connection_signature()

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
                config=self._runtime_model_config(),
                store=self.store,
                definition=definition,
                revision=revision,
                cron_service=self.cron_service,
                client=self._shared_http_client,
                outbound_sink=self.outbound_sink,
                embedding_provider_resolver=self.resolve_embedding_provider,
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

    def resolve_embedding_provider(self, profile: dict[str, Any]) -> Any:
        """Build the exact published Provider revision frozen by an embedding profile."""
        configuration = self.store.get_model_provider_revision(
            profile["provider_id"], profile["provider_revision_id"]
        )
        if configuration is None or configuration["status"] not in {"published", "retired"}:
            raise RuntimeError("embedding profile provider revision is unavailable")
        raw = dict(configuration["configuration"])
        raw.pop("api_key_variable", None)
        raw.pop("extra_header_variables", None)
        runtime_config = self._runtime_model_config({profile["provider_id"]: raw})
        return create_model_provider(
            config=runtime_config,
            model=profile["model_id"],
            provider_name=profile["provider_id"],
            request_timeout_seconds=float(raw.get("request_timeout_seconds") or 120),
        )

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
                if item["aggregate_type"] == "remote_connection":
                    await self._preheat_remote_connection(item)
                elif item["aggregate_type"] == "model_provider":
                    await self._preheat_model_provider(item)
                elif item["aggregate_type"] == "capability":
                    # A connection rollout becomes authoritative when the last
                    # targeted Worker ACKs. Earlier Workers may therefore see
                    # the subsequent Capability rollout before their live
                    # connector registry has swapped to that new generation.
                    # Synchronize the PostgreSQL-active connection set before
                    # exact capability identity preflight.
                    await self._refresh_active_remote_connections()
                    await asyncio.to_thread(self._preheat_configuration, item)
                else:
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
        await self._refresh_active_model_providers()
        await self._refresh_active_remote_connections()
        return loaded

    async def _preheat_model_provider(self, item: dict[str, str]) -> None:
        revision = await asyncio.to_thread(
            self.store.get_model_provider_revision,
            item["aggregate_id"],
            item["revision_id"],
        )
        if revision is None:
            raise RuntimeError("staged model provider revision is unavailable")
        configuration = dict(revision["configuration"])
        configuration.pop("api_key_variable", None)
        configuration.pop("extra_header_variables", None)
        provider_id = item["aggregate_id"]
        staged_config = self._runtime_model_config({provider_id: configuration})
        extension = get_provider_registry(staged_config).extension_for(provider_id)
        extension_id = str(configuration.get("extension_id") or "")
        if extension is None or extension.manifest.extension_id != extension_id:
            raise RuntimeError(
                f"Worker does not provide {provider_id!r} through {extension_id!r}"
            )
        expected = (
            extension.manifest.extension_id,
            extension.manifest.version,
            extension.manifest.build_digest,
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
                "model provider extension is not loaded with the active exact build: "
                f"{expected[0]}@{expected[1]}"
            )
        default_model = next(
            str(model["model_id"])
            for model in configuration.get("models") or ()
            if model.get("enabled", True)
        )
        provider = create_model_provider(
            config=staged_config,
            model=default_model,
            provider_name=provider_id,
            request_timeout_seconds=float(
                configuration.get("request_timeout_seconds") or 120
            ),
        )
        close = getattr(provider, "close", None)
        if callable(close):
            result = close()
            if asyncio.iscoroutine(result):
                await result

    def _runtime_model_config(
        self, overrides: dict[str, dict[str, Any]] | None = None
    ) -> Any:
        copied = type(self.config).model_validate(self.config.model_dump())
        reader = getattr(self.store, "list_active_model_provider_configurations", None)
        values = dict(reader()) if callable(reader) else {}
        values.update(overrides or {})
        for provider_id, raw in values.items():
            configuration = dict(raw)
            configuration.pop("_revision_id", None)
            configuration.pop("api_key_variable", None)
            configuration.pop("extra_header_variables", None)
            materialized = materialize_model_provider(configuration)
            copied.providers.settings[provider_id] = ProviderConfig(
                api_key=str(materialized.get("api_key") or ""),
                api_base=str(materialized.get("api_base") or ""),
                extra_headers=dict(materialized.get("extra_headers") or {}),
                request_timeout_seconds=float(
                    materialized.get("request_timeout_seconds") or 120
                ),
                models=[dict(item) for item in materialized.get("models") or []],
                revision_id=str(raw.get("_revision_id") or "") or None,
            )
        checker = getattr(self.store, "is_plugin_execution_enabled", None)
        if callable(checker):
            registry = get_provider_registry(copied)
            for provider_id in list(copied.providers.settings):
                extension = registry.extension_for(provider_id)
                if extension is not None and not checker(
                    extension.manifest.extension_id
                ):
                    copied.providers.settings.pop(provider_id, None)
        return copied

    def _model_provider_signature(self) -> tuple[tuple[str, str], ...]:
        reader = getattr(self.store, "list_active_model_provider_configurations", None)
        if not callable(reader):
            return ()
        values = [
            (provider_id, str(configuration.get("_revision_id") or ""))
            for provider_id, configuration in reader().items()
        ]
        inventory_reader = getattr(self.store, "list_extension_inventory", None)
        if callable(inventory_reader):
            values.extend(
                (
                    f"extension:{item['extension_id']}",
                    f"{int(item['deployment_allowed'])}:{int(item['desired_active'])}:"
                    f"{item.get('updated_at') or ''}",
                )
                for item in inventory_reader()
                if "model_provider" in set(item.get("extension_types") or ())
            )
        return tuple(sorted(values))

    async def _refresh_active_model_providers(self) -> None:
        signature = await asyncio.to_thread(self._model_provider_signature)
        if signature == self._active_model_provider_signature:
            return
        with self._lock:
            previous_agents = self._unique_agents()
            previous_client = self._shared_http_client
            self._retired_agents.extend(previous_agents)
            if previous_client is not None:
                self._retired_shared_http_clients.append(previous_client)
            self._agents.clear()
            self._shared_http_client = None
        default_agent = self.resolve(self._runtime.default_agent_id)
        if default_agent is None:
            raise RuntimeError("default Agent cannot load the active model provider revision")
        self._runtime.agent = default_agent
        with self._lock:
            agents = self._unique_agents()
        for agent in agents:
            await agent._connect_tool_connectors()
        self._active_model_provider_signature = signature

    async def _preheat_remote_connection(self, item: dict[str, str]) -> None:
        loop = self.resolve(self._runtime.default_agent_id)
        if loop is None:
            raise RuntimeError("default Agent runtime is unavailable for connector preflight")
        extension_id = "connector-http-capability"
        if loop.tool_connectors.get(extension_id) is None:
            raise RuntimeError("HTTP Capability Connector is not enabled on this Worker")
        revision = await asyncio.to_thread(
            self.store.get_remote_connection_revision,
            item["aggregate_id"],
            item["revision_id"],
        )
        if revision is None:
            raise RuntimeError("staged remote connection revision is unavailable")
        configuration = dict(revision["configuration"])
        configuration.pop("signing_secret_variable", None)
        materialized = materialize_remote_connection(configuration)
        staged = CapabilityRegistry()
        async with AsyncExitStack() as stack:
            await loop.tool_connectors.connect_configured(
                {extension_id: {"services": {item["aggregate_id"]: materialized}}},
                capability_registry=staged,
                lifecycle=stack,
            )
            for capability in configuration.get("capabilities") or ():
                capability_id = str(capability.get("capability_id") or "")
                version = str(capability.get("version") or "")
                definition = staged.get_definition(capability_id, version)
                if definition is None:
                    raise RuntimeError(
                        f"remote capability failed exact-definition preflight: "
                        f"{capability_id}@{version}"
                    )
                await asyncio.to_thread(
                    self.store.discover_capability_release,
                    definition,
                    actor_id=f"system:worker:{self._runtime.worker_id}",
                )

    def _remote_connection_signature(self) -> tuple[tuple[str, str], ...]:
        reader = getattr(self.store, "list_active_remote_connection_configurations", None)
        if not callable(reader):
            return ()
        values = reader()
        return tuple(
            sorted(
                (connection_id, str(configuration.get("_revision_id") or ""))
                for connection_id, configuration in values.items()
            )
        )

    async def _refresh_active_remote_connections(self) -> None:
        signature = await asyncio.to_thread(self._remote_connection_signature)
        if signature == self._active_remote_connection_signature:
            return
        with self._lock:
            agents = self._unique_agents()
        for agent in agents:
            await agent.reload_tool_connectors()
        self._active_remote_connection_signature = signature

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
        if aggregate_type == "remote_connection":
            raise RuntimeError("remote connection preflight must execute asynchronously")
        if aggregate_type == "model_provider":
            raise RuntimeError("model provider preflight must execute asynchronously")
        if aggregate_type == "skill":
            version = self.store.get_skill_version(
                item["aggregate_id"], item["revision_id"]
            )
            if version is None or str(version.get("status")) not in {
                "draft",
                "staged",
                "published",
                "retired",
            }:
                raise RuntimeError("staged Skill version is unavailable")
            report = self.store.validate_skill_version(
                item["aggregate_id"], item["revision_id"]
            )
            if not bool(report.get("valid")):
                raise RuntimeError(
                    "Skill validation failed during Worker preheat: "
                    + "; ".join(str(value) for value in report.get("errors") or [])
                )
            if str(report.get("content_sha256") or "") != str(
                version.get("content_sha256") or ""
            ):
                raise RuntimeError("Skill content digest changed after staging")
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
                definition = registry.get_definition(
                    reference.capability_id, reference.version
                )
                if definition is None or definition.ref.identity != reference.identity:
                    raise RuntimeError(
                        "scenario capability is not loaded with the exact plugin build: "
                        f"{reference.capability_id}@{reference.version}"
                    )
            for reference in scenario.required_skills:
                version = self.store.get_published_skill(
                    reference.skill_id, reference.version
                )
                if version is None or str(version.get("content_sha256") or "") != (
                    reference.content_sha256
                ):
                    raise RuntimeError(
                        "scenario Skill is not active with the exact digest: "
                        f"{reference.skill_id}@{reference.version}"
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
            agents = self._unique_agents() + self._retired_agents
            shared_client = self._shared_http_client
            retired_clients = list(self._retired_shared_http_clients)
            self._agents.clear()
            self._retired_agents.clear()
            self._shared_http_client = None
            self._retired_shared_http_clients.clear()
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
        for client in retired_clients:
            if client is not shared_client:
                await client.aclose()

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
