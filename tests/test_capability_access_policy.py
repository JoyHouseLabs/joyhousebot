from __future__ import annotations

from pathlib import Path

import pytest

from joyhousebot.application.platform import PlatformService
from joyhousebot.contracts.capability_extensions import CapabilityExtensionManifest
from joyhousebot.domain.agents import AgentDefinition, AgentRevision
from joyhousebot.domain.capabilities import (
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
    requires_explicit_grant,
    resolve_capability_policy,
)
from joyhousebot.runtime.context import ToolExecutionContext
from joyhousebot.runtime.permissions import permission_engine
from tests.support.postgres_store import PostgresTestStore

_DIGEST = "sha256:" + "a" * 64


def _definition(
    capability_id: str,
    *,
    side_effect: str = "none",
    cost_policy: dict | None = None,
    permissions: tuple[str, ...] = (),
) -> CapabilityDefinition:
    return CapabilityDefinition(
        ref=CapabilityRef(
            capability_id,
            "1.0.0",
            CapabilityKind.CAPABILITY,
            "test-capabilities",
            "1.0.0",
            _DIGEST,
        ),
        name=capability_id,
        description="test capability",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        adapter="test",
        side_effect=side_effect,
        cost_policy=dict(cost_policy or {}),
        permissions=permissions,
    )


def test_catalog_requires_explicit_grant_for_external_and_metered_capabilities() -> None:
    safe = _definition("safe.read")
    external = _definition("external.write", side_effect="external")
    metered = _definition(
        "media.generate", cost_policy={"metered_external_service": True}
    )

    implicit = resolve_capability_policy({"mode": "catalog"}, [safe, external, metered])
    explicit = resolve_capability_policy(
        {"mode": "catalog", "allowed": ["media.generate"]},
        [safe, external, metered],
    )

    assert implicit["resolved"] == ["safe.read"]
    assert explicit["resolved"] == ["safe.read", "media.generate"]
    assert requires_explicit_grant(external) is True
    assert requires_explicit_grant(metered) is True


def test_allowlist_is_strict_and_rejects_unpublished_references() -> None:
    definitions = [_definition("safe.read"), _definition("safe.write")]

    policy = resolve_capability_policy(
        {"mode": "allowlist", "allowed": ["safe.write"]}, definitions, strict=True
    )

    assert policy["resolved"] == ["safe.write"]
    with pytest.raises(ValueError, match="unpublished capabilities"):
        resolve_capability_policy(
            {"mode": "allowlist", "allowed": ["missing.tool"]},
            definitions,
            strict=True,
        )


def test_empty_frozen_allowlist_denies_every_tool() -> None:
    context = ToolExecutionContext(
        run_id="run-a",
        session_key="session-a",
        channel="api",
        chat_id="chat-a",
        metadata={"capability_allowlist_enforced": True},
    )

    decision = permission_engine.evaluate("safe.read", context)

    assert decision.allowed is False
    assert "run allowlist" in decision.reason


def test_enabled_rerank_policy_requires_exact_published_capability_and_grant(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "rerank-policy.db")
    store.publish_capability(_definition("retrieval.rerank", permissions=("context.read",)))
    rerank = store.list_capability_definitions()[0]
    service = PlatformService(store)
    revision = AgentRevision(
        revision_id="agent:v1",
        agent_id="agent",
        version=1,
        model_policy={"primary": "test/model"},
        capability_policy={"mode": "allowlist", "allowed": ["retrieval.rerank"]},
        memory_policy={
            "retrieval": {
                "rerank": {
                    "enabled": True,
                    "capability_id": "retrieval.rerank",
                    "version": "1.0.0",
                    "candidate_limit": 20,
                    "top_k": 10,
                    "failure_mode": "fallback",
                }
            }
        },
    )
    policy = resolve_capability_policy(revision.capability_policy, [rerank], strict=True)

    service._validate_retrieval_rerank_policy(revision, [rerank], policy)

    revision.memory_policy["retrieval"]["rerank"]["version"] = "0.9.0"
    with pytest.raises(ValueError, match="not published and active"):
        service._validate_retrieval_rerank_policy(revision, [rerank], policy)

    revision.memory_policy["retrieval"]["rerank"]["version"] = "1.0.0"
    without_rerank = resolve_capability_policy(
        {"mode": "allowlist", "allowed": []}, [rerank], strict=True
    )
    with pytest.raises(ValueError, match="must authorize retrieval.rerank"):
        service._validate_retrieval_rerank_policy(
            revision, [rerank], without_rerank
        )


def test_run_snapshot_freezes_effective_catalog(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "capability-policy-snapshot.db")
    safe = _definition("safe.read")
    media = _definition(
        "media.generate", cost_policy={"metered_external_service": True}
    )
    store.publish_capability(safe)
    store.publish_capability(media)
    revision = AgentRevision(
        revision_id="agent:v1",
        agent_id="agent",
        version=1,
        model_policy={"primary": "test/model"},
        capability_policy={"mode": "catalog", "allowed": ["media.generate"]},
        status="published",
    )
    store.save_agent_revision(AgentDefinition("agent", "Agent"), revision)
    store.create_runtime_run(
        run_id="run-policy",
        user_id="user-a",
        session_id="session-a",
        agent_id="agent",
        kind="agent",
        prompt="test",
        options={},
    )

    snapshot = store.create_run_execution_snapshot("run-policy", "agent")
    store.publish_capability(_definition("safe.later"))

    assert snapshot.capability_policy["resolved"] == ["media.generate", "safe.read"]
    assert store.get_run_execution_snapshot("run-policy").capability_policy[
        "resolved"
    ] == ["media.generate", "safe.read"]


@pytest.mark.asyncio
async def test_saving_agent_allowlist_pins_required_plugin_release(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "agent-capability-pin.db")
    release = CapabilityExtensionManifest(
        extension_id="test-capabilities",
        version="1.0.0",
        name="Test capabilities",
        build_digest=_DIGEST,
    ).to_release_dict()
    store.upsert_extension_release(release)
    store.register_runtime_worker(
        worker_id="agent-worker",
        capabilities={"agent": True},
        metadata={"extensions": [release]},
    )
    store.stage_extension_release(
        "test-capabilities", "1.0.0", actor_id="test"
    )
    store.acknowledge_configuration_revision(
        worker_id="agent-worker",
        aggregate_type="extension",
        aggregate_id="test-capabilities",
        revision_id="1.0.0",
    )
    store.publish_capability(
        _definition(
            "media.generate",
            side_effect="external",
            permissions=("media.generate",),
        )
    )
    service = PlatformService(store)
    revision = AgentRevision(
        revision_id="agent:v1",
        agent_id="agent",
        version=1,
        model_policy={"primary": "test/model"},
        capability_policy={"mode": "allowlist", "allowed": ["media.generate"]},
    )

    await service.save_agent_revision(AgentDefinition("agent", "Agent"), revision)
    saved = store.get_agent_revision("agent:v1")

    assert saved is not None
    assert [item.to_dict() for item in saved.extension_requirements] == [
        {
            "extension_id": "test-capabilities",
            "version": "1.0.0",
            "build_digest": _DIGEST,
        }
    ]
    with pytest.raises(ValueError, match="missing execution permissions: media.generate"):
        await service.publish_agent_revision(
            "agent", "agent:v1", actor_id="test"
        )
