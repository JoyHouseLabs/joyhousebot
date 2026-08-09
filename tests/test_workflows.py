from pathlib import Path

from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from tests.support.postgres_store import PostgresTestStore


def _client(tmp_path: Path) -> tuple[TestClient, PostgresTestStore]:
    store = PostgresTestStore(tmp_path / "workflows.db")
    store.create_api_access_token(user_id="user-a", actor_id="test", token="token-a")
    store.create_api_access_token(user_id="user-b", actor_id="test", token="token-b")
    return TestClient(create_app(build_api_container(config=Config(), store=store))), store


def _graph() -> dict:
    return {
        "name": "研究并发布方案",
        "summary": "完成研究、形成方案并在发布前由用户确认。",
        "risk_level": "medium",
        "estimated_duration_minutes": 30,
        "nodes": [
            {
                "id": "research",
                "name": "收集证据",
                "objective": "研究目标并保留可核验来源。",
                "kind": "agent",
                "agent_id": "default",
                "dependencies": [],
                "allowed_tools": [],
                "skills": [],
                "max_attempts": 2,
            },
            {
                "id": "draft",
                "name": "形成方案",
                "objective": "基于研究结果形成可执行方案。",
                "kind": "agent",
                "agent_id": "default",
                "dependencies": ["research"],
                "allowed_tools": [],
                "skills": [],
                "max_attempts": 1,
            },
            {
                "id": "approve",
                "name": "确认发布",
                "objective": "请用户确认方案是否可以发布。",
                "kind": "approval",
                "agent_id": None,
                "dependencies": ["draft"],
                "allowed_tools": [],
                "skills": [],
                "max_attempts": 1,
            },
        ],
        "policies": {"max_concurrent": 3, "fail_fast": True, "aggregate": True},
    }


def test_workflow_versions_publish_and_compile_to_runtime_graph(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    owner = {"Authorization": "Bearer token-a"}
    other = {"Authorization": "Bearer token-b"}
    payload = {
        "name": "研究并发布方案",
        "description": "从证据到经过确认的成果",
        "goal": "为新产品形成一份有证据支持的发布方案",
        "graph": _graph(),
        "change_note": "initial AI draft reviewed by user",
    }
    with client:
        created = client.post("/v1/workflows", headers=owner, json=payload)
        assert created.status_code == 201, created.text
        workflow = created.json()
        workflow_id = workflow["workflow_id"]
        first_revision = workflow["current_revision_id"]
        assert workflow["revision"]["version"] == 1
        assert workflow["revision"]["graph"]["edges"] == [
            {"source": "research", "target": "draft"},
            {"source": "draft", "target": "approve"},
        ]

        owner_list = client.get("/v1/workflows", headers=owner).json()["items"]
        assert owner_list[0]["workflow_id"] == workflow_id
        assert "user_id" not in owner_list[0]
        assert client.get("/v1/workflows", headers=other).json()["items"] == []
        assert client.get(f"/v1/workflows/{workflow_id}", headers=other).status_code == 404

        changed = {**payload, "change_note": "tighten the publish gate"}
        revised = client.post(
            f"/v1/workflows/{workflow_id}/revisions", headers=owner, json=changed
        )
        assert revised.status_code == 201, revised.text
        workflow = revised.json()
        second_revision = workflow["current_revision_id"]
        assert second_revision != first_revision
        assert [item["version"] for item in workflow["revisions"]] == [2, 1]

        unpublished = client.post(
            f"/v1/workflows/{workflow_id}/runs",
            headers=owner,
            json={"revision_id": second_revision},
        )
        assert unpublished.status_code == 409

        published = client.post(
            f"/v1/workflows/{workflow_id}/publish",
            headers=owner,
            json={"revision_id": second_revision},
        )
        assert published.status_code == 200, published.text
        assert published.json()["published_revision_id"] == second_revision

        started = client.post(
            f"/v1/workflows/{workflow_id}/runs",
            headers={**owner, "Idempotency-Key": "workflow-run-1"},
            json={"input": "重点关注可验证的用户价值"},
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["run_id"]
        run = store.get_runtime_run(run_id, expected_user_id="user-a")
        assert run is not None
        assert run.kind == "graph"
        assert run.options["metadata"]["workflow_id"] == workflow_id
        tasks = client.get(f"/v1/runs/{run_id}/tasks", headers=owner).json()["items"]
        assert len(tasks) == 3
        assert {item["payload"]["node_type"] for item in tasks} == {"agent", "approval"}

        assert client.delete(f"/v1/workflows/{workflow_id}", headers=other).status_code == 404
        assert client.delete(f"/v1/workflows/{workflow_id}", headers=owner).status_code == 204


def test_workflow_generation_is_design_only_and_invalid_graphs_are_rejected(
    tmp_path: Path,
) -> None:
    client, store = _client(tmp_path)
    owner = {"Authorization": "Bearer token-a"}
    invalid = _graph()
    invalid["nodes"][0]["dependencies"] = ["draft"]
    payload = {
        "name": "invalid",
        "goal": "must fail",
        "graph": invalid,
    }
    with client:
        rejected = client.post("/v1/workflows", headers=owner, json=payload)
        assert rejected.status_code == 422
        assert "cycle" in rejected.json()["error"]["message"]

        submitted = client.post(
            "/v1/workflows/generations",
            headers={**owner, "Idempotency-Key": "workflow-design-1"},
            json={"goal": "每天汇总重要信息，生成一份需要我确认的简报"},
        )
        assert submitted.status_code == 202, submitted.text
        run_id = submitted.json()["run_id"]
        run = store.get_runtime_run(run_id, expected_user_id="user-a")
        assert run is not None
        assert run.options["metadata"]["workflow_design"] is True
        assert run.options["metadata"]["coordinator_required"] is False
        assert run.options["permission_mode"] == "coordinator"
        assert run.options["allowed_tools"] == []
        assert run.options["output_schema"]["properties"]["nodes"]["maxItems"] == 32

        generation = client.get(
            f"/v1/workflows/generations/{run_id}", headers=owner
        )
        assert generation.status_code == 200
        assert generation.json()["status"] == "queued"
        assert (
            client.get(f"/v1/workflows/generations/{run_id}", headers={"Authorization": "Bearer token-b"}).status_code
            == 404
        )
