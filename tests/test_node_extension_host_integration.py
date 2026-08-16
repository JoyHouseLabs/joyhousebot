from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from porthouse_connector_http_capability import RemoteCapabilityTool, connector

from porthouse.extension_sdk.tools import InvocationStatus, ToolInvocationError

ROOT = Path(__file__).resolve().parents[1]
HOST_ROOT = ROOT / "hosts" / "node" / "fixtures" / "echo-host"
HOST_ENTRYPOINT = HOST_ROOT / "dist" / "server.js"
SECRET = "echo-host-signing-secret-that-is-at-least-32-bytes"
KEY_ID = "echo-test-key"


@dataclass(slots=True)
class RunningHost:
    process: subprocess.Popen[str]
    port: int
    manifest_digest: str

    def stop(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def _start_host() -> RunningHost:
    if shutil.which("node") is None:
        pytest.skip("Node.js is not installed")
    if not HOST_ENTRYPOINT.exists():
        pytest.skip("Echo Host is not built; run npm run build in its package")
    env = {
        **os.environ,
        "ECHO_HOST_KEY_ID": KEY_ID,
        "ECHO_HOST_SIGNING_SECRET": SECRET,
        "ECHO_HOST_PORT": "0",
    }
    process = subprocess.Popen(
        ["node", str(HOST_ENTRYPOINT)],
        cwd=HOST_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    ready_line = process.stdout.readline()
    if not ready_line:
        stderr = process.stderr.read() if process.stderr else ""
        process.wait(timeout=5)
        raise AssertionError(f"Echo Host failed to start: {stderr}")
    ready = json.loads(ready_line)
    assert ready["event"] == "ready"
    return RunningHost(
        process=process,
        port=int(ready["port"]),
        manifest_digest=str(ready["manifest_digest"]),
    )


@pytest.fixture
def echo_host() -> RunningHost:
    host = _start_host()
    try:
        yield host
    finally:
        host.stop()


def _capability(capability_id: str, digest_character: str) -> dict[str, Any]:
    return {
        "capability_id": capability_id,
        "version": "1.0.0",
        "implementation_digest": f"sha256:{digest_character * 64}",
        "name": capability_id,
        "description": f"Contract fixture {capability_id}",
        "input_schema": {"type": "object", "additionalProperties": True},
        "output_schema": {"type": "object"},
        "permissions": [f"{capability_id}.invoke"],
        "side_effect": "read",
        "idempotent": True,
    }


def _service(port: int):
    return connector._service_config(
        "echo-host",
        {
            "service_profile": "business",
            "base_url": f"http://127.0.0.1:{port}/porthouse/v1",
            "allow_insecure_http": True,
            "key_id": KEY_ID,
            "signing_secret": SECRET,
            "capabilities": [
                _capability("host.echo", "1"),
                _capability("host.delayed_echo", "5"),
            ],
        },
    )


def _host_profile(host: RunningHost) -> dict[str, Any]:
    return {
        "service_profile": "extension_host",
        "base_url": f"http://127.0.0.1:{host.port}/porthouse/v1",
        "allow_insecure_http": True,
        "key_id": KEY_ID,
        "signing_secret": SECRET,
        "require_response_signature": True,
        "host_protocol_version": "1",
        "expected_host_manifest_digest": host.manifest_digest,
        "require_host_preflight": True,
        "capabilities": [
            _capability("host.echo", "1"),
            _capability("host.delayed_echo", "5"),
        ],
    }


def _context(capability_id: str, idempotency_key: str):
    return SimpleNamespace(
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
        session_key="api:user-1:agent-1:session-1",
        run_id="run-1",
        root_run_id="run-1",
        task_id="task-1",
        request_id="request-1",
        action_id=None,
        idempotency_key=idempotency_key,
        granted_permissions=frozenset({f"{capability_id}.invoke"}),
        permission_mode="default",
    )


def _tool(service: Any, capability_id: str, client: httpx.AsyncClient) -> RemoteCapabilityTool:
    spec = next(item for item in service.capabilities if item.capability_id == capability_id)
    return RemoteCapabilityTool(service, spec, client)


@pytest.mark.asyncio
async def test_node_echo_host_sync_and_async_reconciliation(echo_host: RunningHost) -> None:
    service = _service(echo_host.port)
    async with httpx.AsyncClient() as client:
        sync_tool = _tool(service, "host.echo", client)
        sync_result = await sync_tool.execute(
            message="hello",
            tool_context=_context("host.echo", "tool:echo-sync"),
        )
        assert sync_result.status == InvocationStatus.SUCCEEDED
        assert sync_result.data["output"] == {"message": "hello"}

        delayed_context = _context("host.delayed_echo", "tool:echo-delayed")
        delayed_tool = _tool(service, "host.delayed_echo", client)
        accepted = await delayed_tool.execute(
            message="later",
            delay_ms=10,
            tool_context=delayed_context,
        )
        assert accepted.status == InvocationStatus.ACCEPTED
        assert accepted.operation["request_digest"].startswith("sha256:")

        outcome = await delayed_tool.reconcile_operation(
            accepted.operation,
            tool_context=delayed_context,
        )
        if outcome.status == "pending":
            await asyncio.sleep(0.05)
            outcome = await delayed_tool.reconcile_operation(
                accepted.operation,
                tool_context=delayed_context,
            )
        assert outcome.status == "succeeded"
        assert outcome.output == {"message": "later", "delay_ms": 10}


@pytest.mark.asyncio
async def test_node_echo_host_signed_manifest_preflight(echo_host: RunningHost) -> None:
    result = await connector.preflight_extension_host(
        "echo-host", _host_profile(echo_host)
    )
    assert result["manifest_digest"] == echo_host.manifest_digest
    assert result["host"]["host_id"] == "porthouse-node-echo-host"
    assert result["runtime"]["language"] == "node"

    mismatched = {
        **_host_profile(echo_host),
        "expected_host_manifest_digest": f"sha256:{'9' * 64}",
    }
    with pytest.raises(RuntimeError, match="manifest digest does not match"):
        await connector.preflight_extension_host("echo-host", mismatched)


@pytest.mark.asyncio
async def test_node_echo_host_rejects_idempotency_conflict(echo_host: RunningHost) -> None:
    service = _service(echo_host.port)
    context = _context("host.echo", "tool:echo-conflict")
    async with httpx.AsyncClient() as client:
        tool = _tool(service, "host.echo", client)
        await tool.execute(message="first", tool_context=context)
        with pytest.raises(ToolInvocationError) as raised:
            await tool.execute(message="different", tool_context=context)
    assert getattr(raised.value, "code", "") == "IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_node_echo_host_restart_returns_explicit_unknown() -> None:
    first = _start_host()
    try:
        first_service = _service(first.port)
        context = _context("host.delayed_echo", "tool:echo-restart")
        async with httpx.AsyncClient() as client:
            first_tool = _tool(first_service, "host.delayed_echo", client)
            accepted = await first_tool.execute(
                message="restart",
                delay_ms=5_000,
                tool_context=context,
            )
    finally:
        first.stop()

    second = _start_host()
    try:
        second_service = _service(second.port)
        async with httpx.AsyncClient() as client:
            second_tool = _tool(second_service, "host.delayed_echo", client)
            outcome = await second_tool.reconcile_operation(
                accepted.operation,
                tool_context=context,
            )
        assert outcome.status == "unknown"
    finally:
        second.stop()
