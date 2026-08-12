from pathlib import Path

import pytest

from joyhousebot.agent.skills import SkillsLoader
from joyhousebot.domain.capabilities import (
    CapabilityDefinition,
    CapabilityInvocation,
    CapabilityKind,
    CapabilityRef,
    CapabilityResult,
)
from tests.support.postgres_store import PostgresTestStore


def _definition(description: str = "Search public pages") -> CapabilityDefinition:
    return CapabilityDefinition(
        ref=CapabilityRef("web.search", "1.0.0", CapabilityKind.TOOL, "test.plugin", "1.0.0", "sha256:test"),
        name="Web search",
        description=description,
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        output_schema={"type": "object"},
        adapter="builtin.web_search",
    )


def _run(store: PostgresTestStore) -> None:
    store.create_runtime_run(
        run_id="run-1",
        user_id="user-a",
        session_id="session-1",
        agent_id="coordinator",
        kind="agent",
        prompt="search",
        options={},
    )


def _publish_skill(
    store: PostgresTestStore,
    skill_id: str,
    version: str,
    instruction: str,
) -> None:
    store.save_skill_draft(
        {
            "skill_id": skill_id,
            "version": version,
            "name": skill_id.removeprefix("skill."),
            "description": "Versioned prompt policy",
            "instruction_content": instruction,
            "eval_cases": [
                {
                    "name": "basic",
                    "input": "apply policy",
                    "expected_behavior": "follow the complete policy",
                }
            ],
        },
        actor_id="test",
    )
    store.stage_skill_version(
        skill_id,
        version,
        actor_id="test",
        require_healthy_workers=False,
    )


def test_capability_versions_are_immutable(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "capabilities.db")
    store.publish_capability(_definition())
    store.publish_capability(_definition())
    assert store.get_capability_definition("web.search")["ref"]["version"] == "1.0.0"
    assert len(
        [
            row
            for row in store.list_capability_definitions()
            if row["ref"]["capability_id"] == "web.search"
        ]
    ) == 1

    with pytest.raises(ValueError, match="immutable"):
        store.publish_capability(_definition("changed after publication"))


def test_capability_invocation_is_idempotent_and_user_scoped(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "invocations.db")
    _run(store)
    invocation = CapabilityInvocation(
        capability=CapabilityRef("web.search", "1.0.0", CapabilityKind.TOOL, "test.plugin", "1.0.0", "sha256:test"),
        user_id="user-a",
        agent_id="coordinator",
        session_id="session-1",
        run_id="run-1",
        task_id=None,
        trace_id="trace-1",
        input={"query": "postgres agents"},
        timeout_seconds=60,
        idempotency_key="call-1",
        invocation_id="inv-1",
    )
    first, created = store.create_capability_invocation(invocation)
    assert created and first.status == "queued"
    second, created = store.create_capability_invocation(invocation)
    assert not created and second.invocation_id == first.invocation_id
    assert store.start_capability_invocation("inv-1", worker_id="worker-1")

    result = CapabilityResult.succeeded("inv-1", summary="done", data={"items": []})
    assert store.finish_capability_invocation(
        "inv-1", status="succeeded", result=result.to_dict(), error=None
    )
    assert store.list_capability_invocations("run-1", expected_user_id="user-b") == []
    rows = store.list_capability_invocations("run-1", expected_user_id="user-a")
    assert len(rows) == 1
    assert rows[0].result["status"] == "succeeded"


def test_published_skill_content_is_worker_shared_and_not_public(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "skills.db")
    instruction = (
        "---\ndescription: Evidence research\n---\n"
        "Use primary sources, record citations, and separate facts from inference."
    )
    _publish_skill(store, "skill.research", "1.0.0", instruction)
    worker_loader = SkillsLoader(store)
    assert worker_loader.load_skill("research") == instruction
    assert "research" in {item["name"] for item in worker_loader.list_skills()}

    stored = store.get_skill_version("skill.research", "1.0.0")
    assert stored is not None
    assert stored["instruction_content"] == instruction
    assert not any(
        item["ref"]["capability_id"] == "skill.research"
        for item in store.list_capability_definitions()
    )


def test_skill_loader_can_pin_an_immutable_skill_version(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "versioned-skills.db")
    for version, instruction in (("1.0.0", "legacy policy"), ("1.0.1", "current policy")):
        _publish_skill(
            store,
            "skill.enrich",
            version,
            instruction + " with enough detail to be safely applied in every execution context",
        )
    loader = SkillsLoader(store)
    assert loader.load_skill("enrich").startswith("current policy")
    assert loader.load_skill("enrich", "1.0.0").startswith("legacy policy")
    assert loader.load_skills_for_context(["enrich"], versions={"enrich": "1.0.0"}) == (
        "### Skill: enrich\n\nlegacy policy with enough detail to be safely applied in every execution context"
    )


def test_skill_disable_is_an_emergency_stop_without_mutating_content(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "runtime-settings.db")
    instruction = "Original immutable instructions that contain enough detail for safe execution."
    _publish_skill(
        store, "skill.configurable", "1.0.0", instruction
    )
    loader = SkillsLoader(store)
    assert loader.load_skill("configurable") == instruction
    assert loader.get_always_skills() == []
    assert store.set_skill_status(
        "skill.configurable", status="disabled", actor_id="admin-a"
    )
    assert loader.load_skill("configurable") is None
    stored = store.get_skill_version("skill.configurable", "1.0.0")
    assert stored and stored["instruction_content"] == instruction
