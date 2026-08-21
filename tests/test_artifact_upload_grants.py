from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.capabilities.dispatcher import CapabilityDispatcher
from joyhousebot.config.schema import Config
from joyhousebot.runtime.context import ActionOutcomeUnknownError
from tests.support.postgres_store import PostgresTestStore
from tests.test_operation_reconciliation import (
    _adapter,
    _AsyncOperationTool,
    _claimed_context,
)


@pytest.mark.asyncio
async def test_scoped_upload_is_one_use_and_worker_materializes_artifact(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(
        tmp_path / "artifact-grant.db",
        artifact_upload_directory=str(tmp_path / "objects"),
    )
    _, context = _claimed_context(store, "run-artifact-grant")
    with pytest.raises(ActionOutcomeUnknownError) as raised:
        await CapabilityDispatcher(store).invoke_tool(
            _adapter(_AsyncOperationTool()), {"value": "one"}, context=context
        )
    reconciliation = store.get_action_reconciliation(raised.value.action_id)
    assert reconciliation is not None
    store.create_api_access_token(user_id="user-a", actor_id="test", token="owner-token")
    store.create_api_access_token(user_id="user-b", actor_id="test", token="foreign-token")
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    body = b"verified host artifact"
    digest = hashlib.sha256(body).hexdigest()
    path = (
        f"/host/v1/runs/{context.run_id}/operations/"
        f"{reconciliation.reconciliation_id}/artifact-upload-grants"
    )
    with client:
        denied = client.post(
            path,
            headers={"Authorization": "Bearer foreign-token"},
            json={
                "operation_id": "provider-42",
                "name": "report.txt",
                "media_type": "text/plain",
                "content_sha256": digest,
                "byte_size": len(body),
            },
        )
        assert denied.status_code == 404
        created = client.post(
            path,
            headers={"Authorization": "Bearer owner-token"},
            json={
                "operation_id": "provider-42",
                "name": "report.txt",
                "media_type": "text/plain",
                "content_sha256": digest,
                "byte_size": len(body),
                "provenance": {"host_extension_build_digest": f"sha256:{'a' * 64}"},
            },
        )
        assert created.status_code == 201
        value = created.json()
        grant = value["grant"]
        upload_headers = {
            "Authorization": f"Bearer {value['upload_token']}",
            "Content-Type": "text/plain",
            "X-Content-SHA256": digest,
            "X-JoyHouseBot-Action-ID": reconciliation.action_id,
            "Content-Length": str(len(body)),
        }
        wrong_scope = client.put(
            value["upload_url"],
            params={"operation_id": "another-operation"},
            headers=upload_headers,
            content=body,
        )
        assert wrong_scope.status_code == 422
        uploaded = client.put(
            value["upload_url"],
            params={"operation_id": "provider-42"},
            headers=upload_headers,
            content=body,
        )
        assert uploaded.status_code == 202
        assert uploaded.json()["status"] == "uploaded"
        replay = client.put(
            value["upload_url"],
            params={"operation_id": "provider-42"},
            headers=upload_headers,
            content=body,
        )
        assert replay.status_code == 404
        second = client.post(
            path,
            headers={"Authorization": "Bearer owner-token"},
            json={
                "operation_id": "provider-42",
                "name": "second.txt",
                "media_type": "text/plain",
                "content_sha256": digest,
                "byte_size": len(body),
            },
        ).json()
        digest_mismatch = client.put(
            second["upload_url"],
            params={"operation_id": "provider-42"},
            headers={
                **upload_headers,
                "Authorization": f"Bearer {second['upload_token']}",
            },
            content=b"different artifact body",
        )
        assert digest_mismatch.status_code == 422

    claimed = store.claim_artifact_upload(worker_id="artifact-worker", lease_seconds=30)
    assert claimed is not None and claimed.grant_id == grant["grant_id"]
    assert store.materialize_artifact_upload(
        claimed.grant_id,
        worker_id="artifact-worker",
        lease_version=claimed.lease_version,
    )
    artifacts = store.list_runtime_artifacts(context.run_id, user_id="user-a")
    assert artifacts[0]["artifact_id"] == grant["artifact_id"]
    assert artifacts[0]["content_sha256"] == digest
    assert artifacts[0]["provenance"]["operation_id"] == "provider-42"
    assert artifacts[0]["uri"].startswith("joyhousebot-artifact://sha256/")
    assert len(artifacts) == 1
