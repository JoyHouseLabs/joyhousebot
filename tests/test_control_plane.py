from pathlib import Path
from uuid import uuid4

import pytest

from joyhousebot.domain.agents import AgentDefinition, AgentRevision
from joyhousebot.domain.capabilities import CapabilityDefinition, CapabilityKind, CapabilityRef
from joyhousebot.domain.scenarios import ScenarioVersion
from tests.support.postgres_store import PostgresTestStore, require_postgres


def _revision(version: int, *, status: str = "draft") -> AgentRevision:
    return AgentRevision(
        revision_id=f"joy:v{version}",
        agent_id="joy",
        version=version,
        instructions=f"revision {version}",
        model_policy={"primary": "test/model"},
        created_by="test-admin",
        status=status,
    )


def _agent_store(path: Path) -> PostgresTestStore:
    """Create the arbitrary Agent under rollout without relying on Core defaults."""
    store = PostgresTestStore(path)
    store.save_agent_revision(
        AgentDefinition(
            agent_id="joy",
            name="Joy",
            description="Rollout test Agent",
            role="coordinator",
        ),
        _revision(1, status="published"),
    )
    return store


def test_agent_rollout_activates_only_after_all_target_workers_ack(tmp_path: Path) -> None:
    store = _agent_store(tmp_path / "rollout.db")
    current = store.get_agent_definition("joy")
    assert current is not None
    store.register_runtime_worker(worker_id="agent-a", capabilities={"agent": True})
    store.register_runtime_worker(worker_id="agent-b", capabilities={"agent": True})
    store.register_runtime_worker(worker_id="scheduler", capabilities={"scheduler": True})
    store.save_agent_revision(current, _revision(2))

    published = store.publish_agent_revision("joy", "joy:v2", actor_id="admin-a")
    assert published.revision.revision_id == "joy:v2"
    assert store.get_agent_profile("joy").revision.revision_id == "joy:v1"
    rollout = store.list_configuration_rollouts()[0]
    assert rollout.status == "rolling_out"
    assert rollout.target_worker_count == 2
    assert [
        item["revision_id"] for item in store.list_pending_agent_revisions("agent-a")
    ] == ["joy:v2"]

    assert store.acknowledge_agent_revision(
        worker_id="agent-a", agent_id="joy", revision_id="joy:v2"
    )
    assert store.get_agent_profile("joy").revision.revision_id == "joy:v1"
    assert store.acknowledge_agent_revision(
        worker_id="agent-b", agent_id="joy", revision_id="joy:v2"
    )

    assert store.get_agent_profile("joy").revision.revision_id == "joy:v2"
    rollout = store.list_configuration_rollouts()[0]
    assert rollout.status == "completed"
    assert rollout.acknowledged_worker_count == 2
    assert [event.event_type for event in store.list_configuration_events()[:2]] == [
        "activated",
        "publish.requested",
    ]


def test_failed_agent_rollout_keeps_previous_revision_active(tmp_path: Path) -> None:
    store = _agent_store(tmp_path / "rollout-failed.db")
    current = store.get_agent_definition("joy")
    assert current is not None
    store.register_runtime_worker(worker_id="agent-a", capabilities={"agent": True})
    store.save_agent_revision(current, _revision(2))
    store.publish_agent_revision("joy", "joy:v2", actor_id="admin-a")
    store.acknowledge_agent_revision(
        worker_id="agent-a",
        agent_id="joy",
        revision_id="joy:v2",
        status="failed",
        error={"message": "provider unavailable"},
    )

    assert store.get_agent_profile("joy").revision.revision_id == "joy:v1"
    rollout = store.list_configuration_rollouts()[0]
    assert rollout.status == "failed"
    assert rollout.failed_worker_count == 1
    assert store.list_configuration_events()[0].event_type == "rollout.failed"


def test_manual_rollout_requires_approval_and_can_be_rolled_back(tmp_path: Path) -> None:
    store = _agent_store(tmp_path / "rollout-manual.db")
    current = store.get_agent_definition("joy")
    assert current is not None
    store.register_runtime_worker(worker_id="agent-a", capabilities={"agent": True})
    store.save_agent_revision(current, _revision(2))

    store.publish_agent_revision(
        "joy", "joy:v2", actor_id="publisher", activation_mode="manual"
    )
    rollout = store.list_configuration_rollouts()[0]
    assert rollout.previous_revision_id == "joy:v1"
    assert rollout.activation_mode == "manual"
    assert store.acknowledge_agent_revision(
        worker_id="agent-a", agent_id="joy", revision_id="joy:v2"
    )
    rollout = store.get_configuration_rollout(rollout.rollout_id)
    assert rollout is not None and rollout.status == "awaiting_approval"
    assert store.get_agent_profile("joy").revision.revision_id == "joy:v1"

    assert store.approve_configuration_rollout(
        rollout.rollout_id, actor_id="release-approver"
    )
    assert store.get_agent_profile("joy").revision.revision_id == "joy:v2"
    assert store.rollback_configuration_rollout(
        rollout.rollout_id, actor_id="release-operator"
    )
    assert store.get_agent_profile("joy").revision.revision_id == "joy:v2"
    rolled_back = store.get_configuration_rollout(rollout.rollout_id)
    assert rolled_back is not None
    assert rolled_back.status == "rollback_pending"
    assert rolled_back.rollback_revision_id == "joy:v1"
    rollback_rollout = next(
        item
        for item in store.list_configuration_rollouts()
        if item.rollback_of_rollout_id == rollout.rollout_id
    )
    assert rollback_rollout.revision_id == "joy:v1"
    assert store.acknowledge_agent_revision(
        worker_id="agent-a", agent_id="joy", revision_id="joy:v1"
    )
    assert store.get_agent_profile("joy").revision.revision_id == "joy:v1"
    assert store.get_configuration_rollout(rollout.rollout_id).status == "rolled_back"


def test_failed_rollback_preheat_keeps_current_revision_active(tmp_path: Path) -> None:
    store = _agent_store(tmp_path / "rollout-safe-rollback.db")
    current = store.get_agent_definition("joy")
    assert current is not None
    store.register_runtime_worker(worker_id="agent-a", capabilities={"agent": True})
    store.save_agent_revision(current, _revision(2))
    store.publish_agent_revision("joy", "joy:v2", actor_id="publisher")
    rollout = store.list_configuration_rollouts()[0]
    assert store.acknowledge_agent_revision(
        worker_id="agent-a", agent_id="joy", revision_id="joy:v2"
    )

    assert store.rollback_configuration_rollout(rollout.rollout_id, actor_id="operator")
    assert store.acknowledge_agent_revision(
        worker_id="agent-a",
        agent_id="joy",
        revision_id="joy:v1",
        status="failed",
        error={"message": "old plugin package unavailable"},
    )

    assert store.get_agent_profile("joy").revision.revision_id == "joy:v2"
    assert store.get_configuration_rollout(rollout.rollout_id).status == "completed"
    assert store.list_configuration_events()[0].event_type == "rollback.failed"


def test_failed_worker_target_can_be_retried_without_reloading_successes(
    tmp_path: Path,
) -> None:
    store = _agent_store(tmp_path / "rollout-retry.db")
    current = store.get_agent_definition("joy")
    assert current is not None
    store.register_runtime_worker(worker_id="agent-a", capabilities={"agent": True})
    store.register_runtime_worker(worker_id="agent-b", capabilities={"agent": True})
    store.save_agent_revision(current, _revision(2))
    store.publish_agent_revision("joy", "joy:v2", actor_id="publisher")
    rollout = store.list_configuration_rollouts()[0]

    assert store.acknowledge_agent_revision(
        worker_id="agent-a", agent_id="joy", revision_id="joy:v2"
    )
    assert store.acknowledge_agent_revision(
        worker_id="agent-b",
        agent_id="joy",
        revision_id="joy:v2",
        status="failed",
        error={"message": "plugin unavailable"},
    )
    assert store.get_configuration_rollout(rollout.rollout_id).status == "failed"
    assert store.retry_configuration_rollout(rollout.rollout_id, actor_id="operator")
    targets = {
        item["worker_id"]: item
        for item in store.list_configuration_rollout_targets(rollout.rollout_id)
    }
    assert targets["agent-a"]["status"] == "loaded"
    assert targets["agent-a"]["attempt_count"] == 1
    assert targets["agent-b"]["status"] == "pending"
    assert targets["agent-b"]["attempt_count"] == 2
    assert store.acknowledge_agent_revision(
        worker_id="agent-b", agent_id="joy", revision_id="joy:v2"
    )
    assert store.get_agent_profile("joy").revision.revision_id == "joy:v2"


def test_failed_rollout_retry_retargets_replacement_workers(tmp_path: Path) -> None:
    store = _agent_store(tmp_path / "rollout-worker-replacement.db")
    current = store.get_agent_definition("joy")
    assert current is not None
    store.register_runtime_worker(worker_id="agent-old", capabilities={"agent": True})
    store.save_agent_revision(current, _revision(2))
    store.publish_agent_revision("joy", "joy:v2", actor_id="publisher")
    rollout = store.list_configuration_rollouts()[0]
    assert store.acknowledge_agent_revision(
        worker_id="agent-old",
        agent_id="joy",
        revision_id="joy:v2",
        status="failed",
        error={"message": "worker stopped after preheat failure"},
    )
    with store._pool.connection() as connection:  # noqa: SLF001 - worker replacement fixture
        connection.execute(
            "UPDATE runtime_workers SET status='offline' WHERE worker_id='agent-old'"
        )
    store.register_runtime_worker(worker_id="agent-new", capabilities={"agent": True})

    assert store.retry_configuration_rollout(rollout.rollout_id, actor_id="operator")
    targets = {
        item["worker_id"]: item
        for item in store.list_configuration_rollout_targets(rollout.rollout_id)
    }
    assert targets["agent-old"]["status"] == "superseded"
    assert targets["agent-new"]["status"] == "pending"
    assert store.acknowledge_agent_revision(
        worker_id="agent-new", agent_id="joy", revision_id="joy:v2"
    )
    assert store.get_agent_profile("joy").revision.revision_id == "joy:v2"


def test_rollout_timeout_is_reconciled_and_keeps_previous_revision(tmp_path: Path) -> None:
    store = _agent_store(tmp_path / "rollout-timeout.db")
    current = store.get_agent_definition("joy")
    assert current is not None
    store.register_runtime_worker(worker_id="agent-a", capabilities={"agent": True})
    store.save_agent_revision(current, _revision(2))
    store.publish_agent_revision("joy", "joy:v2", actor_id="publisher")
    rollout = store.list_configuration_rollouts()[0]
    with store._pool.connection() as connection:  # noqa: SLF001 - deadline fixture
        connection.execute(
            """UPDATE configuration_rollouts
               SET deadline_at=clock_timestamp()-interval '1 second'
               WHERE rollout_id=%s""",
            (rollout.rollout_id,),
        )

    assert store.reconcile_configuration_rollouts() == 1
    timed_out = store.get_configuration_rollout(rollout.rollout_id)
    assert timed_out is not None
    assert timed_out.status == "timed_out"
    assert timed_out.rollback_revision_id == "joy:v1"
    assert store.get_agent_profile("joy").revision.revision_id == "joy:v1"


def test_capability_and_scenario_use_the_same_worker_rollout_contract(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "rollout-catalogs.db")
    store.register_runtime_worker(worker_id="agent-a", capabilities={"agent": True})
    capability = CapabilityDefinition(
        ref=CapabilityRef(
            "demo.echo",
            "2.0.0",
            CapabilityKind.CAPABILITY,
            "demo.plugin",
            "2.0.0",
            "sha256:demo-v2",
        ),
        name="Demo echo",
        description="",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        adapter="demo.echo",
    )
    store.stage_capability_release(capability, actor_id="publisher")
    rollout = store.list_configuration_rollouts()[0]
    assert rollout.aggregate_type == "capability"
    assert store.get_capability_definition("demo.echo") is None
    assert store.acknowledge_configuration_revision(
        worker_id="agent-a",
        aggregate_type="capability",
        aggregate_id="demo.echo",
        revision_id="2.0.0",
    )
    assert store.get_capability_definition("demo.echo")["ref"]["version"] == "2.0.0"

    scenario = ScenarioVersion(
        scenario_id="demo.scenario",
        version=1,
        name="Demo scenario",
        description="",
        fields=(),
        nodes=(),
        edges=(),
        allowed_capabilities=(capability.ref,),
        planning_mode="dynamic",
        execution_policy={},
        routing_rules=(),
    )
    store.save_scenario_version(scenario)
    store.stage_scenario_release("demo.scenario", 1, actor_id="publisher")
    scenario_rollout = store.list_configuration_rollouts()[0]
    assert scenario_rollout.aggregate_type == "scenario"
    assert store.get_scenario_version("demo.scenario") is None
    assert store.acknowledge_configuration_revision(
        worker_id="agent-a",
        aggregate_type="scenario",
        aggregate_id="demo.scenario",
        revision_id="1",
    )
    assert store.get_scenario_version("demo.scenario").status == "published"


def test_last_admin_authority_is_transactionally_protected(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "admins.db")
    store.upsert_platform_admin(
        user_id="admin-a", permissions=["admins.write"], actor_id="bootstrap"
    )
    with pytest.raises(ValueError, match="at least one enabled administrator"):
        store.upsert_platform_admin(
            user_id="admin-a",
            permissions=["platform.read"],
            actor_id="admin-a",
        )
    with pytest.raises(ValueError, match="at least one enabled administrator"):
        store.delete_platform_admin("admin-a", actor_id="admin-a")

    store.upsert_platform_admin(
        user_id="admin-b", permissions=["admins.write"], actor_id="admin-a"
    )
    assert store.delete_platform_admin("admin-a", actor_id="admin-b")
    assert store.get_platform_admin("admin-b") is not None


def test_unknown_permissions_are_rejected(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "permissions.db")
    with pytest.raises(ValueError, match="unknown platform permissions"):
        store.upsert_platform_admin(
            user_id="admin-a", permissions=["made.up"], actor_id="bootstrap"
        )


def test_stale_worker_lease_is_reconciled_without_hiding_live_workers(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "worker-leases.db")
    store.register_runtime_worker(worker_id="stale-agent", capabilities={"agent": True})
    store.register_runtime_worker(worker_id="live-agent", capabilities={"agent": True})
    with store._pool.connection() as connection:  # noqa: SLF001 - integration fixture setup
        connection.execute(
            """UPDATE runtime_workers
               SET last_heartbeat=clock_timestamp()-INTERVAL '10 minutes'
               WHERE worker_id='stale-agent'"""
        )

    assert store.expire_stale_runtime_workers(stale_after_seconds=120) == 1
    workers = {item["worker_id"]: item for item in store.list_runtime_workers()}
    assert workers["stale-agent"]["status"] == "offline"
    assert workers["stale-agent"]["healthy"] is False
    assert workers["live-agent"]["status"] == "online"
    assert workers["live-agent"]["healthy"] is True


@pytest.mark.postgres
def test_postgres_staged_rollout_round_trip() -> None:
    database_url = require_postgres()
    from joyhousebot.storage.postgres_store import PostgresRuntimeStore

    suffix = uuid4().hex
    agent_id = f"rollout-{suffix}"
    worker_id = f"agent-{suffix}"
    revision_1 = AgentRevision(
        revision_id=f"{agent_id}:v1",
        agent_id=agent_id,
        version=1,
        model_policy={"primary": "test/model"},
        status="published",
    )
    definition = AgentDefinition(agent_id=agent_id, name="Rollout Test")
    store = PostgresRuntimeStore(database_url, application_name="test-control-rollout")
    try:
        store.save_agent_revision(definition, revision_1)
        store.register_runtime_worker(worker_id=worker_id, capabilities={"agent": True})
        revision_2 = AgentRevision(
            revision_id=f"{agent_id}:v2",
            agent_id=agent_id,
            version=2,
            model_policy={"primary": "test/model-v2"},
        )
        definition = store.get_agent_definition(agent_id)
        assert definition is not None
        store.save_agent_revision(definition, revision_2)
        store.publish_agent_revision(agent_id, revision_2.revision_id, actor_id="test")
        assert store.get_agent_profile(agent_id).revision.revision_id == revision_1.revision_id

        rollout = next(
            item
            for item in store.list_configuration_rollouts(limit=1000)
            if item.aggregate_id == agent_id
        )
        for target in store.list_configuration_rollout_targets(rollout.rollout_id):
            store.acknowledge_agent_revision(
                worker_id=target["worker_id"],
                agent_id=agent_id,
                revision_id=revision_2.revision_id,
            )
        assert store.get_agent_profile(agent_id).revision.revision_id == revision_2.revision_id
        token_record, plaintext = store.create_api_access_token(
            user_id=f"user-{suffix}", actor_id="test"
        )
        authenticated = store.authenticate_api_access_token(plaintext)
        assert authenticated and authenticated["token_id"] == token_record["token_id"]
        assert store.revoke_api_access_token(token_record["token_id"], actor_id="test")
        assert store.authenticate_api_access_token(plaintext) is None
        assert store.get_platform_overview()["runs"] >= 0
    finally:
        store.unregister_runtime_worker(worker_id)
        store.close()
