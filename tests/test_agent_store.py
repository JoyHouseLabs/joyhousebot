from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from porthouse.contracts.plugins import PluginManifest
from porthouse.domain.agents import AgentDefinition, AgentRevision, PluginReleaseRequirement
from tests.support.postgres_store import PostgresTestStore, require_postgres

TEST_PLUGIN_DIGEST = f"sha256:{'d' * 64}"


def test_neutral_agent_uses_bootstrap_model_without_rewriting_revision(tmp_path) -> None:
    path = tmp_path / "agents.db"
    store = PostgresTestStore(path)
    try:
        profile = store.get_agent_profile("default")
        assert profile is not None
        assert profile.revision.model_policy["primary"] == "test/default"
        with store._pool.connection() as conn, conn.transaction():
            policy = dict(profile.revision.model_policy)
            policy["primary"] = "operator/model-v1"
            conn.execute(
                "UPDATE agent_revisions SET model_policy=%s WHERE revision_id='default:v1'",
                (Jsonb(policy),),
            )
    finally:
        store.close()

    reopened = PostgresTestStore(path)
    try:
        repaired = reopened.get_agent_profile("default")
        assert repaired is not None
        assert repaired.revision.model_policy["primary"] == "operator/model-v1"
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
    assert default.definition.agent_id == "default"
    assert default.definition.is_default
    assert default.definition.role == "executor"
    assert default.revision.revision_id == "default:v1"
    assert {profile.definition.agent_id for profile in store.list_agent_profiles()} == {"default"}


def test_default_agent_seed_does_not_restore_pruned_revision(tmp_path: Path) -> None:
    """An operator-selected current revision survives a process restart."""
    store = PostgresTestStore(tmp_path / "agent-seed-prune.db")
    definition = AgentDefinition(
        agent_id="default",
        name="Default Agent",
        description="Operator managed coordinator",
        role="coordinator",
    )
    revision = AgentRevision(
        revision_id="default:v2",
        agent_id="default",
        version=2,
        instructions="Use approved catalog capabilities.",
        model_policy={"primary": "test/model"},
        capability_policy={"permissions": ["catalog.search.read"]},
        status="published",
    )
    store.save_agent_revision(definition, revision)
    with store._pool.connection() as conn, conn.transaction():
        conn.execute("DELETE FROM agent_revisions WHERE revision_id='default:v1'")

    store._seed_default_agents()

    assert store.get_agent_revision("default:v1") is None
    profile = store.get_agent_profile("default")
    assert profile is not None
    assert profile.revision.revision_id == "default:v2"


def test_agent_revision_requires_exact_active_plugin_release(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "agent-plugin-requirements.db")
    definition, revision = _profile()
    pinned = replace(
        revision,
        plugin_requirements=(
            PluginReleaseRequirement("sample.catalog", "0.4.0", TEST_PLUGIN_DIGEST),
        ),
    )
    with pytest.raises(ValueError, match="unavailable plugin release"):
        store.save_agent_revision(definition, pinned)
    store.upsert_plugin_release(
        PluginManifest(
            plugin_id="sample.catalog",
            version="0.4.0",
            name="Sample Catalog",
            build_digest=TEST_PLUGIN_DIGEST,
        ).to_dict()
    )
    store.stage_plugin_release(
        "sample.catalog",
        "0.4.0",
        actor_id="test:trusted-fixture",
        require_healthy_workers=False,
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
        "researcher",
        "researcher:v1",
        actor_id="admin-a",
        require_healthy_workers=False,
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

    skill = store.save_skill_draft(
        {
            "skill_id": "skill.research",
            "version": "1.0.0",
            "name": "Research",
            "description": "Research instructions",
            "instruction_content": (
                "Collect primary evidence, cite every source, and distinguish facts from inference."
            ),
            "eval_cases": [
                {
                    "name": "evidence",
                    "input": "research",
                    "expected_behavior": "cite sources",
                }
            ],
        },
        actor_id="test",
    )
    store.stage_skill_version(
        "skill.research",
        "1.0.0",
        actor_id="test",
        require_healthy_workers=False,
    )
    store.bind_agent_skill(
        agent_revision_id=revision.revision_id,
        skill_id="skill.research",
        skill_version="1.0.0",
        activation_mode="always",
        priority=10,
        configuration={"depth": "high"},
    )
    store.publish_agent_revision(
        "researcher", revision.revision_id, require_healthy_workers=False
    )

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
            "content_sha256": skill["content_sha256"],
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
    store.publish_agent_revision(
        "researcher", "researcher:v2", require_healthy_workers=False
    )

    frozen = store.get_run_execution_snapshot("run-snapshot")
    assert frozen is not None
    assert frozen.agent_revision_id == "researcher:v1"
    assert frozen.model_policy == {"primary": "test/model"}


@pytest.mark.postgres
def test_postgres_run_snapshot_round_trip() -> None:
    database_url = require_postgres()
    from porthouse.storage.postgres_store import PostgresRuntimeStore

    store = PostgresRuntimeStore(database_url, application_name="test-agent-snapshot")
    run_id = f"agent-snapshot-{uuid4().hex}"
    try:
        store.create_runtime_run(
            run_id=run_id,
            user_id="snapshot-user",
            session_id=run_id,
            agent_id="default",
            kind="agent",
            prompt="snapshot",
            options={},
        )
        snapshot = store.create_run_execution_snapshot(run_id, "default")
        restored = store.get_run_execution_snapshot(run_id)
        assert restored == snapshot
        assert restored.agent_revision_id == "default:v1"
    finally:
        store.close()
