import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from joyhousebot.contracts.plugins import PluginManifest
from joyhousebot.domain.agents import AgentDefinition, AgentRevision, PluginReleaseRequirement
from joyhousebot.domain.capabilities import (
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
)
from tests.support.postgres_store import PostgresTestStore


def test_builtin_agent_model_uses_openrouter_slug_and_repairs_legacy_value(tmp_path) -> None:
    path = tmp_path / "agents.db"
    store = PostgresTestStore(path)
    try:
        profile = store.get_agent_profile("joy")
        assert profile is not None
        assert profile.revision.model_policy["primary"] == "openrouter/deepseek/deepseek-v4-flash"
        with store._pool.connection() as conn, conn.transaction():
            policy = dict(profile.revision.model_policy)
            policy["primary"] = "anthropic/claude-opus-4-5"
            conn.execute(
                "UPDATE agent_revisions SET model_policy=%s WHERE revision_id='joy:v1'",
                (Jsonb(policy),),
            )
    finally:
        store.close()

    reopened = PostgresTestStore(path)
    try:
        repaired = reopened.get_agent_profile("joy")
        assert repaired is not None
        assert repaired.revision.model_policy["primary"] == "openrouter/deepseek/deepseek-v4-flash"
    finally:
        reopened.close()


def _profile(*, status: str = "draft") -> tuple[AgentDefinition, AgentRevision]:
    return (
        AgentDefinition(
            agent_id="researcher",
            name="Researcher",
            description="Evidence specialist",
            role="specialist",
        ),
        AgentRevision(
            revision_id="researcher:v1",
            agent_id="researcher",
            version=1,
            instructions="Use primary sources.",
            model_policy={"primary": "test/model"},
            status=status,
            created_by="test-user",
        ),
    )


def test_default_agents_are_seeded_from_database(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "agents.db")

    default = store.get_agent_profile()
    assert default is not None
    assert default.definition.agent_id == "joy"
    assert default.definition.is_default
    assert default.revision.revision_id == "joy:v1"
    assert {profile.definition.agent_id for profile in store.list_agent_profiles()} == {
        "joy",
        "main-coordinator",
    }


def test_default_agent_seed_does_not_restore_pruned_revision(tmp_path: Path) -> None:
    """An operator-selected current revision survives a process restart."""
    store = PostgresTestStore(tmp_path / "agent-seed-prune.db")
    definition = AgentDefinition(
        agent_id="main-coordinator",
        name="Main Coordinator",
        description="Operator managed coordinator",
        role="coordinator",
    )
    revision = AgentRevision(
        revision_id="main-coordinator:v2",
        agent_id="main-coordinator",
        version=2,
        instructions="Use approved Dinq capabilities.",
        model_policy={"primary": "test/model"},
        capability_policy={"permissions": ["dinq.search.read"]},
        status="published",
    )
    store.save_agent_revision(definition, revision)
    with store._pool.connection() as conn, conn.transaction():
        conn.execute("DELETE FROM agent_revisions WHERE revision_id='main-coordinator:v1'")

    store._seed_default_agents()

    assert store.get_agent_revision("main-coordinator:v1") is None
    profile = store.get_agent_profile("main-coordinator")
    assert profile is not None
    assert profile.revision.revision_id == "main-coordinator:v2"


def test_agent_revision_requires_exact_active_plugin_release(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "agent-plugin-requirements.db")
    definition, revision = _profile()
    pinned = replace(
        revision,
        plugin_requirements=(
            PluginReleaseRequirement("dinq.discover", "0.4.0", "sha256:dinq-040"),
        ),
    )
    with pytest.raises(ValueError, match="unavailable plugin release"):
        store.save_agent_revision(definition, pinned)
    store.upsert_plugin_release(
        PluginManifest(
            plugin_id="dinq.discover",
            version="0.4.0",
            name="Dinq Discover",
            build_digest="sha256:dinq-040",
        ).to_dict()
    )
    store.save_agent_revision(definition, pinned)
    restored = store.get_agent_revision("researcher:v1")
    assert restored and restored.plugin_requirements == pinned.plugin_requirements


def test_draft_can_be_published_and_published_revision_is_immutable(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "publish-agent.db")
    definition, revision = _profile()
    store.save_agent_revision(definition, revision)
    assert store.get_agent_profile("researcher") is None

    published = store.publish_agent_revision(
        "researcher", "researcher:v1", actor_id="admin-a"
    )
    assert published.revision.status == "published"
    assert published.definition.current_revision_id == "researcher:v1"

    with pytest.raises(ValueError, match="immutable"):
        store.save_agent_revision(
            definition,
            replace(revision, instructions="Unapproved mutable change"),
        )


def test_agent_skill_binding_requires_published_skill(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "agent-skills.db")
    definition, revision = _profile()
    store.save_agent_revision(definition, revision)

    with pytest.raises(ValueError, match="published Skill"):
        store.bind_agent_skill(
            agent_revision_id=revision.revision_id,
            skill_id="skill.research",
            skill_version="1.0.0",
        )

    store.publish_capability(
        CapabilityDefinition(
            ref=CapabilityRef("skill.research", "1.0.0", CapabilityKind.SKILL, "test.plugin", "1.0.0", "sha256:test"),
            name="Research",
            description="Research instructions",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            adapter="prompt-skill:research",
        )
    )
    store.bind_agent_skill(
        agent_revision_id=revision.revision_id,
        skill_id="skill.research",
        skill_version="1.0.0",
        activation_mode="always",
        priority=10,
        configuration={"depth": "high"},
    )
    store.publish_agent_revision("researcher", revision.revision_id)

    with pytest.raises(ValueError, match="draft Agent revisions"):
        store.bind_agent_skill(
            agent_revision_id=revision.revision_id,
            skill_id="skill.research",
            skill_version="1.0.0",
        )

    assert store.list_agent_skill_bindings(revision.revision_id) == [
        {
            "agent_revision_id": "researcher:v1",
            "skill_id": "skill.research",
            "skill_version": "1.0.0",
            "activation_mode": "always",
            "priority": 10,
            "configuration": {"depth": "high"},
        }
    ]


def test_run_snapshot_freezes_agent_revision_and_skill_bindings(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "agent-snapshot.db")
    definition, revision = _profile(status="published")
    store.save_agent_revision(definition, revision)
    store.create_runtime_run(
        run_id="run-snapshot",
        user_id="user-a",
        session_id="session-a",
        agent_id="researcher",
        kind="agent",
        prompt="research",
        options={},
    )

    snapshot = store.create_run_execution_snapshot("run-snapshot", "researcher")
    assert snapshot.agent_revision_id == "researcher:v1"
    assert snapshot.model_policy == {"primary": "test/model"}

    second = AgentRevision(
        revision_id="researcher:v2",
        agent_id="researcher",
        version=2,
        instructions="A newer policy.",
        model_policy={"primary": "test/model-v2"},
    )
    store.save_agent_revision(definition, second)
    store.publish_agent_revision("researcher", "researcher:v2")

    frozen = store.get_run_execution_snapshot("run-snapshot")
    assert frozen is not None
    assert frozen.agent_revision_id == "researcher:v1"
    assert frozen.model_policy == {"primary": "test/model"}


@pytest.mark.postgres
def test_postgres_run_snapshot_round_trip() -> None:
    database_url = os.environ.get("JOYHOUSEBOT_TEST_POSTGRES_URL", "").strip()
    if not database_url:
        pytest.skip("JOYHOUSEBOT_TEST_POSTGRES_URL is not configured")
    from joyhousebot.storage.postgres_store import PostgresRuntimeStore

    store = PostgresRuntimeStore(database_url, application_name="test-agent-snapshot")
    run_id = f"agent-snapshot-{uuid4().hex}"
    try:
        store.create_runtime_run(
            run_id=run_id,
            user_id="snapshot-user",
            session_id=run_id,
            agent_id="joy",
            kind="agent",
            prompt="snapshot",
            options={},
        )
        snapshot = store.create_run_execution_snapshot(run_id, "joy")
        restored = store.get_run_execution_snapshot(run_id)
        assert restored == snapshot
        assert restored.agent_revision_id == "joy:v1"
    finally:
        store.close()
