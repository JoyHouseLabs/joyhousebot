"""Prompt assets are immutable, gated, and frozen into Run snapshots."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from joyhousebot.domain.agents import AgentDefinition, AgentRevision
from tests.support.postgres_store import PostgresTestStore


def _prompt(version: int = 1, content: str | None = None) -> dict:
    return {
        "prompt_id": "prompt.evidence-policy",
        "version": version,
        "name": "证据优先输出",
        "description": "要求清晰区分证据、推断和下一步。",
        "content": content
        or "输出必须区分已验证证据、合理推断和下一步行动；不要把不确定内容伪装成事实。",
        "input_schema": {"type": "object", "properties": {}},
        "output_contract": {"type": "object"},
        "tags": ["quality", "evidence"],
        "change_note": "initial",
    }


def _published_agent(store: PostgresTestStore) -> tuple[str, str]:
    definition = AgentDefinition(
        agent_id="prompt-agent", name="Prompt Agent", role="specialist"
    )
    revision = AgentRevision(
        revision_id="prompt-agent:v1",
        agent_id="prompt-agent",
        version=1,
        status="published",
        model_policy={"primary": "test/model"},
    )
    store.save_agent_revision(definition, revision)
    return definition.agent_id, revision.revision_id


def test_prompt_revision_binding_is_frozen_into_run_snapshot(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "prompt-control-plane.db")
    created = store.save_prompt_draft(_prompt(), actor_id="admin")
    assert created["revision_id"] == "prompt.evidence-policy:v1"
    assert store.validate_prompt_revision("prompt.evidence-policy", 1, actor_id="admin")[
        "valid"
    ]
    published = store.publish_prompt_revision("prompt.evidence-policy", 1, actor_id="admin")
    agent_id, agent_revision_id = _published_agent(store)
    binding = store.bind_prompt_revision(
        binding_id="promptbind_evidence",
        target_type="agent",
        target_id=agent_id,
        target_revision_id=agent_revision_id,
        prompt_revision_id=published["revision_id"],
        purpose="system_instruction",
        position=10,
        enabled=True,
        actor_id="admin",
    )
    assert binding["status"] == "active"
    store.create_runtime_run(
        run_id="prompt-run",
        user_id="user-a",
        session_id="session-a",
        agent_id=agent_id,
        kind="agent",
        prompt="research this",
        options={},
    )
    snapshot = store.create_run_execution_snapshot("prompt-run", agent_id)
    assert snapshot.prompt_bindings[0]["revision_id"] == published["revision_id"]
    assert "区分已验证证据" in snapshot.prompt_bindings[0]["content"]

    store.save_prompt_draft(
        _prompt(2, "新版输出必须给出证据链接、置信度和待验证假设。"), actor_id="admin"
    )
    store.publish_prompt_revision("prompt.evidence-policy", 2, actor_id="admin")
    frozen = store.get_run_execution_snapshot("prompt-run")
    assert frozen is not None
    assert frozen.prompt_bindings[0]["revision_id"] == published["revision_id"]


def test_prompt_rejects_undeclared_template_variable(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "prompt-template-contract.db")
    invalid = _prompt(content="请分析 {{market}}，并明确给出证据与不确定性。")
    with pytest.raises(ValueError, match="must be declared"):
        store.save_prompt_draft(invalid, actor_id="admin")


def test_prompt_admin_api_has_draft_validate_publish_and_bind_loop(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "prompt-api.db")
    store.upsert_platform_admin(user_id="admin", permissions=["*"], actor_id="test")
    store.create_operator_access_token(
        user_id="admin", actor_id="test", token="prompt-token"
    )
    agent_id, agent_revision_id = _published_agent(store)
    headers = {"Authorization": "Bearer prompt-token"}
    with TestClient(create_app(build_api_container(config=Config(), store=store))) as client:
        saved = client.put(
            "/control/v1/admin/prompts/prompt.evidence-policy/versions/1",
            headers=headers,
            json=_prompt(),
        )
        assert saved.status_code == 200, saved.text
        checked = client.post(
            "/control/v1/admin/prompts/prompt.evidence-policy/versions/1/validate",
            headers=headers,
        )
        assert checked.status_code == 200 and checked.json()["valid"]
        released = client.post(
            "/control/v1/admin/prompts/prompt.evidence-policy/versions/1/publish",
            headers=headers,
        )
        assert released.status_code == 200, released.text
        bound = client.put(
            "/control/v1/admin/prompts/bindings",
            headers=headers,
            json={
                "target_id": agent_id,
                "target_revision_id": agent_revision_id,
                "prompt_revision_id": released.json()["revision_id"],
            },
        )
        assert bound.status_code == 200, bound.text
        listed = client.get("/control/v1/admin/prompts", headers=headers)
        assert listed.status_code == 200 and listed.json()["items"][0]["current"]
