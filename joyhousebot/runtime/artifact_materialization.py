"""Materialize plugin-produced Artifact contracts into the durable Runtime plane."""

from __future__ import annotations

from typing import Any


def materialize_capability_artifacts(
    store: Any,
    *,
    run_id: str,
    task_id: str | None,
    agent_id: str,
    capability_result: Any,
    capability_id: str | None = None,
) -> tuple[str, ...]:
    """Persist successful capability Artifacts with framework-owned provenance."""
    if capability_result is None or not bool(getattr(capability_result, "ok", False)):
        return ()
    stored: list[str] = []
    for raw in tuple(getattr(capability_result, "artifacts", ()) or ()):
        value = raw.to_dict() if callable(getattr(raw, "to_dict", None)) else dict(raw)
        artifact_id = str(value.get("artifact_id") or "").strip()
        artifact_type = str(value.get("artifact_type") or "").strip()
        if not artifact_id or not artifact_type:
            raise ValueError("capability Artifact requires artifact_id and artifact_type")
        metadata = dict(value.get("metadata") or {})
        provenance = {
            **dict(value.get("provenance") or {}),
            "producer": "capability",
            "capability_id": str(capability_id or ""),
            "invocation_id": str(getattr(capability_result, "invocation_id", "") or ""),
            "agent_id": agent_id,
        }
        store.add_runtime_artifact(
            artifact_id=artifact_id,
            run_id=run_id,
            task_id=task_id,
            name=str(metadata.get("name") or artifact_type),
            media_type=str(value.get("media_type") or "application/json"),
            content=value.get("data"),
            uri=str(value.get("uri") or "") or None,
            artifact_type=artifact_type,
            operation=str(value.get("operation") or "create"),
            schema_version=int(value.get("schema_version") or 1),
            metadata=metadata,
            content_sha256=str(value.get("content_sha256") or ""),
            object_version=str(value.get("object_version") or ""),
            provenance=provenance,
            evidence=dict(value.get("evidence") or {}),
        )
        stored.append(artifact_id)
    return tuple(stored)


__all__ = ["materialize_capability_artifacts"]
