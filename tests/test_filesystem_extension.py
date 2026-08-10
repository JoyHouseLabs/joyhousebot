"""Contracts for the optional Run-scoped filesystem extension."""

import pytest
from joyhousebot_capability_filesystem import plugin as filesystem

from joyhousebot.capabilities import CapabilityPluginRegistry
from joyhousebot.capabilities.services import CapabilityServiceBroker
from joyhousebot.extension_sdk import CapabilityContext


def _context(services, **overrides):
    values = {
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "root_run_id": "root-a",
        "agent_id": "agent-a",
        "services": services,
        "metadata": {"permissions": ["filesystem.read", "filesystem.write"]},
    }
    values.update(overrides)
    return CapabilityContext(**values)


def test_filesystem_extension_registers_four_versioned_capabilities() -> None:
    registry = CapabilityPluginRegistry()
    registry.register_plugin(filesystem.FilesystemCapabilityPlugin())
    definitions = {item.ref.capability_id: item for item in registry.list_capabilities()}
    assert set(definitions) == {"edit_file", "list_dir", "read_file", "write_file"}
    assert definitions["write_file"].side_effect == "write"
    assert definitions["read_file"].side_effect == "read"
    assert definitions["read_file"].ref.plugin_id == "capability-filesystem"


@pytest.mark.asyncio
async def test_scratch_service_isolates_users_and_root_runs(tmp_path) -> None:
    services = CapabilityServiceBroker(None, scratch_root=tmp_path)
    owner = _context(services)
    other_user = _context(services, user_id="user-b")
    other_run = _context(services, root_run_id="root-b")
    await services.scratch.write(owner, path="result.txt", content="private")
    assert await services.scratch.read(owner, path="result.txt") == "private"
    with pytest.raises(FileNotFoundError):
        await services.scratch.read(other_user, path="result.txt")
    with pytest.raises(FileNotFoundError):
        await services.scratch.read(other_run, path="result.txt")


@pytest.mark.asyncio
async def test_filesystem_write_preserves_frozen_action_identity(tmp_path) -> None:
    services = CapabilityServiceBroker(None, scratch_root=tmp_path)
    result = await filesystem.WriteFileHandler().execute(
        _context(
            services,
            action_id="action-a",
            idempotency_key="action:action-a",
        ),
        {"path": "reports/result.md", "content": "done"},
    )
    assert result.success is True
    assert result.write_receipt.action_id == "action-a"
    assert result.write_receipt.idempotency_key == "action:action-a"
    assert await services.scratch.read(
        _context(services), path="reports/result.md"
    ) == "done"


@pytest.mark.asyncio
async def test_filesystem_write_requires_action_before_touching_scratch(tmp_path) -> None:
    services = CapabilityServiceBroker(None, scratch_root=tmp_path)
    result = await filesystem.WriteFileHandler().execute(
        _context(services),
        {"path": "result.md", "content": "must-not-write"},
    )
    assert result.success is False
    assert result.error["code"] == "ACTION_IDENTITY_REQUIRED"
    with pytest.raises(FileNotFoundError):
        await services.scratch.read(_context(services), path="result.md")


@pytest.mark.asyncio
async def test_filesystem_capability_rejects_memory_namespace(tmp_path) -> None:
    services = CapabilityServiceBroker(None, scratch_root=tmp_path)
    result = await filesystem.ReadFileHandler().execute(
        _context(services),
        {"path": "memory/MEMORY.md"},
    )
    assert result.success is False
    assert result.error["code"] == "INVALID_PARAMETERS"
