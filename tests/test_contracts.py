from __future__ import annotations

import pytest

from joyhousebot.contracts import Artifact, CapabilityContext, CapabilityResult


def test_artifact_is_business_opaque_and_serializable() -> None:
    artifact = Artifact(
        artifact_type="catalog.item",
        data={"name": "Ada"},
        operation="upsert",
        metadata={"namespace": "catalog"},
    )

    value = artifact.to_dict()

    assert value["artifact_type"] == "catalog.item"
    assert value["operation"] == "upsert"
    assert value["data"] == {"name": "Ada"}
    assert value["metadata"]["namespace"] == "catalog"
    assert value["artifact_id"].startswith("artifact_")


def test_capability_context_keeps_framework_identity_only() -> None:
    context = CapabilityContext(
        user_id="user_1",
        session_id="session_1",
        run_id="run_1",
        task_id="task_1",
        metadata={"trace_id": "trace_1"},
    )
    result = CapabilityResult(success=True, output={"ok": True})

    assert context.user_id == "user_1"
    assert context.run_id == "run_1"
    assert context.metadata["trace_id"] == "trace_1"
    assert result.success is True
    assert result.output == {"ok": True}


@pytest.mark.asyncio
async def test_capability_handler_contract_can_be_implemented_without_framework_imports() -> None:
    class Echo:
        async def execute(self, context: CapabilityContext, input: dict) -> CapabilityResult:
            return CapabilityResult(success=True, output={"user": context.user_id, **input})

    result = await Echo().execute(
        CapabilityContext(user_id="user_1", session_id="s", run_id="r"),
        {"value": 42},
    )

    assert result.output == {"user": "user_1", "value": 42}
