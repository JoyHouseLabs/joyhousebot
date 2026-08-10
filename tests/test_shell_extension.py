"""Contracts for the optional fail-closed shell capability."""

import pytest
from joyhousebot_capability_shell import plugin as shell

from joyhousebot.capabilities import CapabilityPluginRegistry
from joyhousebot.capabilities.services import CapabilityServiceBroker
from joyhousebot.extension_sdk import CapabilityContext


class _FakeSandboxServices:
    def __init__(self, result=None) -> None:
        self.sandbox = self
        self.result = result or {"success": True, "output": "ok", "exit_code": 0}
        self.calls = []

    async def execute(self, context, **kwargs):  # noqa: ANN001
        self.calls.append({"user_id": context.user_id, **kwargs})
        return self.result


def _context(services, **overrides):
    values = {
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "root_run_id": "root-a",
        "agent_id": "agent-a",
        "action_id": "action-a",
        "idempotency_key": "action:action-a",
        "services": services,
        "metadata": {"permissions": ["sandbox.exec"]},
    }
    values.update(overrides)
    return CapabilityContext(**values)


def test_shell_extension_registers_non_idempotent_external_capability() -> None:
    registry = CapabilityPluginRegistry()
    registry.register_plugin(shell.ShellCapabilityPlugin())
    definition, _handler = registry.get("exec", "1.0.0")
    assert definition.side_effect == "external"
    assert definition.idempotent is False
    assert definition.retryable is False
    assert definition.ref.plugin_id == "capability-shell"
    assert registry.manifests()[0].execution_isolation == "container"


@pytest.mark.asyncio
async def test_shell_handler_preserves_action_and_operator_configuration() -> None:
    services = _FakeSandboxServices()
    result = await shell.ExecHandler().execute(
        _context(
            services,
            metadata={
                "permissions": ["sandbox.exec"],
                "capability_configuration": {"timeout": 12},
            },
        ),
        {"command": "echo ok", "working_dir": "reports"},
    )
    assert result.success is True
    assert result.write_receipt.action_id == "action-a"
    assert result.write_receipt.idempotency_key == "action:action-a"
    assert services.calls[0]["configuration"] == {"timeout": 12}


@pytest.mark.asyncio
async def test_shell_handler_requires_action_before_sandbox_call() -> None:
    services = _FakeSandboxServices()
    result = await shell.ExecHandler().execute(
        _context(services, action_id=None, idempotency_key=None),
        {"command": "echo must-not-run"},
    )
    assert result.success is False
    assert result.error["code"] == "ACTION_IDENTITY_REQUIRED"
    assert services.calls == []


@pytest.mark.asyncio
async def test_core_sandbox_service_blocks_command_before_docker(tmp_path) -> None:
    services = CapabilityServiceBroker(None, scratch_root=tmp_path)
    result = await services.sandbox.execute(
        _context(services),
        command="rm -rf /workspace",
    )
    assert result["success"] is False
    assert result["code"] == "COMMAND_BLOCKED"


@pytest.mark.asyncio
async def test_core_sandbox_service_rejects_network_enablement(tmp_path, monkeypatch) -> None:
    async def available():
        return True

    monkeypatch.setattr(
        "joyhousebot.capabilities.services.sandbox.is_docker_available", available
    )
    services = CapabilityServiceBroker(None, scratch_root=tmp_path)
    result = await services.sandbox.execute(
        _context(services),
        command="echo ok",
        configuration={"container_network": "host"},
    )
    assert result["success"] is False
    assert result["code"] == "SANDBOX_MISCONFIGURED"
