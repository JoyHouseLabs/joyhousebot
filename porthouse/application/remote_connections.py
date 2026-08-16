"""Use cases for the remote Capability connection control plane."""

from __future__ import annotations

import asyncio
from typing import Any

from porthouse.domain.capabilities import (
    CapabilityDefinition,
    CapabilityRef,
)


class RemoteConnectionService:
    def __init__(self, store: Any, platform: Any) -> None:
        self.store = store
        self.platform = platform

    async def list_connections(self) -> list[dict[str, Any]]:
        values = await asyncio.to_thread(self.store.list_remote_connections)
        return [await self._enrich(item) for item in values]

    async def get_connection(self, connection_id: str) -> dict[str, Any] | None:
        value = await asyncio.to_thread(self.store.get_remote_connection, connection_id)
        return await self._enrich(value) if value is not None else None

    async def save_revision(
        self,
        connection_id: str,
        *,
        name: str,
        description: str,
        configuration: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.store.save_remote_connection_revision,
            connection_id,
            name=name,
            description=description,
            configuration=configuration,
            actor_id=actor_id,
        )

    async def publish_revision(
        self,
        connection_id: str,
        revision_id: str,
        *,
        actor_id: str,
        rollout_policy: dict[str, Any],
    ) -> dict[str, Any]:
        rollout_id = await asyncio.to_thread(
            self.store.stage_remote_connection_revision,
            connection_id,
            revision_id,
            actor_id=actor_id,
            **rollout_policy,
        )
        revision = await asyncio.to_thread(
            self.store.get_remote_connection_revision, connection_id, revision_id
        )
        assert revision is not None
        return {**revision, "status": "staged", "rollout_id": rollout_id}

    async def publish_capability(
        self,
        connection_id: str,
        capability_id: str,
        version: str,
        *,
        actor_id: str,
        rollout_policy: dict[str, Any],
    ) -> dict[str, Any]:
        connection = await asyncio.to_thread(
            self.store.get_remote_connection, connection_id
        )
        if connection is None or connection.get("current_revision") is None:
            raise ValueError("remote connection is not active")
        current = dict(connection["current_revision"]["configuration"] or {})
        declared = {
            (str(item.get("capability_id") or ""), str(item.get("version") or ""))
            for item in current.get("capabilities") or ()
        }
        if (capability_id, version) not in declared:
            raise ValueError("capability is not declared by the active connection revision")
        raw = await asyncio.to_thread(
            self.store.get_capability_release_definition, capability_id, version
        )
        if raw is None:
            raise ValueError("Worker has not loaded this remote capability definition")
        reference = CapabilityRef.from_dict(dict(raw["ref"]))
        if reference.kind.value != "connector" or connection_id not in set(
            raw.get("connection_ids") or ()
        ):
            raise ValueError("loaded capability does not belong to this remote connection")
        definition = CapabilityDefinition(
            ref=reference,
            name=str(raw["name"]),
            description=str(raw.get("description") or ""),
            input_schema=dict(raw.get("input_schema") or {}),
            output_schema=dict(raw.get("output_schema") or {}),
            adapter=str(raw["adapter"]),
            tags=tuple(raw.get("tags") or ()),
            execution_mode=str(raw.get("execution_mode") or "immediate"),
            expected_duration_seconds=int(raw.get("expected_duration_seconds") or 10),
            timeout_seconds=int(raw.get("timeout_seconds") or 60),
            idempotent=bool(raw.get("idempotent", True)),
            retryable=bool(raw.get("retryable", True)),
            side_effect=str(raw.get("side_effect") or "none"),
            invocation_concurrency=str(
                raw.get("invocation_concurrency") or "sequential"
            ),
            max_concurrent_invocations=int(
                raw.get("max_concurrent_invocations") or 1
            ),
            supports_stream=bool(raw.get("supports_stream", False)),
            permissions=tuple(raw.get("permissions") or ()),
            data_classification=str(raw.get("data_classification") or "confidential"),
            connection_ids=tuple(raw.get("connection_ids") or ()),
            cost_policy=dict(raw.get("cost_policy") or {}),
            origin=dict(raw.get("origin") or {}),
            configuration_schema=dict(raw.get("configuration_schema") or {}),
            configuration=dict(raw.get("configuration") or {}),
        )
        return await self.platform.publish_capability(
            definition,
            actor_id=actor_id,
            rollout_policy=rollout_policy,
        )

    async def _enrich(self, value: dict[str, Any]) -> dict[str, Any]:
        revision = value.get("current_revision") or value.get("latest_revision")
        configuration = dict((revision or {}).get("configuration") or {})
        capabilities = await asyncio.to_thread(
            self.store.get_remote_capability_release_statuses, configuration
        )
        rollouts = await asyncio.to_thread(
            self.store.list_configuration_rollouts, limit=1000
        )
        related = [
            item
            for item in rollouts
            if item.aggregate_type == "remote_connection"
            and item.aggregate_id == value["connection_id"]
        ]
        latest_rollout = related[0] if related else None
        targets = (
            await asyncio.to_thread(
                self.store.list_configuration_rollout_targets,
                latest_rollout.rollout_id,
            )
            if latest_rollout is not None
            else []
        )
        connector_release = await asyncio.to_thread(
            self.store.get_active_plugin_release, "connector-http-capability"
        )
        workers = await asyncio.to_thread(
            self.store.list_plugin_workers, "connector-http-capability"
        )
        eligible_workers = [
            item
            for item in workers
            if item.get("healthy") and item.get("plugin") is not None
        ]
        loaded_workers = sum(
            1 for item in eligible_workers if item.get("execution_eligible")
        )
        current_active = value.get("current_revision") is not None
        all_capabilities_published = bool(capabilities) and all(
            item["release_status"] == "published" for item in capabilities
        )
        blockers: list[str] = []
        if connector_release is None:
            blockers.append("HTTP Capability Connector 扩展尚未发布生效")
        if not current_active:
            blockers.append("远程连接尚无已生效 Revision")
        if not loaded_workers:
            blockers.append("没有健康 Worker 加载当前 Connector 发布")
        if capabilities and not all_capabilities_published:
            blockers.append("仍有远程 Capability 未通过发布门禁")
        return {
            **value,
            "capabilities": capabilities,
            "connector_release": connector_release,
            "worker_summary": {
                "total": len(eligible_workers),
                "loaded": loaded_workers,
            },
            "latest_rollout": (
                {**latest_rollout.to_dict(), "targets": targets}
                if latest_rollout is not None
                else None
            ),
            "execution_ready": not blockers,
            "execution_blockers": blockers,
        }


__all__ = ["RemoteConnectionService"]
