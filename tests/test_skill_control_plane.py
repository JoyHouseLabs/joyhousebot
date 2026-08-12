from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from joyhousebot.domain.skills import validate_skill_document
from tests.support.postgres_store import PostgresTestStore


def _draft(version: str = "1.0.0", instruction: str | None = None) -> dict:
    return {
        "skill_id": "skill.market-research",
        "version": version,
        "name": "市场研究",
        "description": "形成可验证的市场机会研究方法。",
        "instruction_content": instruction
        or "先明确研究问题，再从多个一手来源收集证据，标注来源和时间，最后区分事实、推断与建议。",
        "tags": ["research", "opc"],
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "required_capabilities": [],
        "required_integrations": [],
        "examples": [{"input": "研究市场", "output": "机会简报"}],
        "eval_cases": [
            {
                "name": "evidence-first",
                "input": "分析一个新市场",
                "expected_behavior": "结论必须区分证据和推断",
            }
        ],
        "templates": [],
        "change_note": "initial",
        "source": {"kind": "managed"},
    }


def test_skill_document_validation_reports_release_evidence() -> None:
    report = validate_skill_document(_draft())
    assert report["valid"]
    assert report["content_sha256"].startswith("sha256:")
    assert all(item["passed"] for item in report["checks"])

    invalid = validate_skill_document(_draft(instruction="too short"))
    assert not invalid["valid"]
    assert "at least 40" in invalid["errors"][0]


def test_skill_versions_are_immutable_and_can_be_reactivated(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "skill-control-plane.db")
    created = store.save_skill_draft(_draft(), actor_id="admin")
    assert created["status"] == "draft"
    assert store.validate_skill_version(
        created["skill_id"], created["version"], actor_id="admin"
    )["valid"]
    store.stage_skill_version(
        created["skill_id"],
        created["version"],
        actor_id="admin",
        require_healthy_workers=False,
    )
    first = store.get_published_skill(created["skill_id"])
    assert first and first["version"] == "1.0.0"

    with pytest.raises(ValueError, match="immutable"):
        store.save_skill_draft(
            _draft(instruction="这是试图覆盖已经发布内容的修改，因此必须被不可变版本规则拒绝并要求创建新版本。"),
            actor_id="admin",
        )

    second = store.save_skill_draft(
        _draft(
            "1.1.0",
            "先定义问题和决策边界，再收集一手证据并交叉验证，记录反例，最后输出事实、推断、风险和建议。",
        ),
        actor_id="admin",
    )
    store.stage_skill_version(
        second["skill_id"],
        second["version"],
        actor_id="admin",
        require_healthy_workers=False,
    )
    assert store.get_published_skill(second["skill_id"])["version"] == "1.1.0"
    assert store.get_skill_version(second["skill_id"], "1.0.0")["status"] == "retired"

    store.stage_skill_version(
        second["skill_id"],
        "1.0.0",
        actor_id="admin",
        require_healthy_workers=False,
    )
    assert store.get_published_skill(second["skill_id"])["version"] == "1.0.0"


def test_skill_admin_api_closes_draft_validate_publish_and_disable_loop(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "skill-api.db")
    store.create_api_access_token(user_id="operator", actor_id="test", token="skill-token")
    store.upsert_platform_admin(user_id="operator", permissions=["*"], actor_id="test")
    container = build_api_container(config=Config(), store=store)
    headers = {"Authorization": "Bearer skill-token"}
    with TestClient(create_app(container)) as client:
        created = client.post("/v1/admin/skills", headers=headers, json=_draft())
        assert created.status_code == 200, created.text
        checked = client.post(
            "/v1/admin/skills/skill.market-research/versions/1.0.0/validate",
            headers=headers,
        )
        assert checked.status_code == 200 and checked.json()["valid"]
        published = client.post(
            "/v1/admin/skills/skill.market-research/versions/1.0.0/publish",
            headers=headers,
            json={"require_healthy_workers": False},
        )
        assert published.status_code == 200, published.text
        assert published.json()["rollout_id"].startswith("rollout_")
        listed = client.get("/v1/admin/skills", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["items"][0]["current_version"] == "1.0.0"
        disabled = client.put(
            "/v1/admin/skills/skill.market-research/status",
            headers=headers,
            json={"status": "disabled"},
        )
        assert disabled.status_code == 200
        assert store.get_published_skill("skill.market-research") is None
