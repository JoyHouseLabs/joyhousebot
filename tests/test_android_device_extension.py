"""Contracts for the device-routed Android capability extension."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from porthouse_capability_android_device import plugin as android_device

from porthouse.capabilities import CapabilityPluginRegistry
from porthouse.capabilities.registry import _PluginTool
from porthouse.extension_sdk import CapabilityContext, InvocationStatus

_REPO_ROOT = Path(__file__).resolve().parents[1]
# The Phase-0 probe lives in the companion ai-market checkout; the drift
# check runs when it is present and skips cleanly in isolated CI.
_PROBE_PATH = Path(
    os.environ.get("PORTHOUSE_ANDROID_PROBE", str(_REPO_ROOT.parent / "ai-market" / "android" / "probe" / "android_probe.py"))
)


def _load_probe():
    spec = importlib.util.spec_from_file_location("android_probe_contract", _PROBE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _context(**overrides) -> CapabilityContext:
    values = {
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_id": "agent-a",
        "metadata": {"permissions": ["android.device"]},
    }
    values.update(overrides)
    return CapabilityContext(**values)


def _tool_context(**overrides) -> SimpleNamespace:
    values = {
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "task_id": "task-a",
        "agent_id": "agent-a",
        "request_id": "req-a",
        "tracker_id": None,
        "action_id": "action-a",
        "idempotency_key": "action:action-a",
        "memory_scope": None,
        "memory_policy": {},
        "root_run_id": "run-a",
        "metadata": {},
        "granted_permissions": ["android.device", "android.observe", "android.actuate"],
        "permission_mode": "enforced",
        "allowed_tools": [],
        "disallowed_tools": [],
        "worker_id": "worker-a",
        "turn_id": "turn-a",
        "turn_index": 0,
        "action_index": 0,
        "channel": "",
        "chat_id": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_extension_registers_two_device_routed_capabilities() -> None:
    registry = CapabilityPluginRegistry()
    registry.register_plugin(android_device.AndroidDeviceCapabilityPlugin())
    definitions = {
        item.ref.capability_id: item for item in registry.list_capabilities()
    }
    assert set(definitions) == {"android.observe", "android.actuate"}
    observe = definitions["android.observe"]
    actuate = definitions["android.actuate"]
    assert observe.side_effect == "internal"
    assert actuate.side_effect == "external"
    assert observe.ref.plugin_id == "capability-android-device"
    assert observe.permissions == ("android.observe",)
    assert actuate.permissions == ("android.actuate",)
    assert actuate.idempotent is False
    assert actuate.data_classification == "confidential"
    assert actuate.max_concurrent_invocations == 1
    manifest = next(iter(registry.manifests()))
    assert manifest.required_permissions == ("android.device",)
    assert manifest.execution_isolation == "in_process"


def test_extension_isolatable_from_the_plugin_registry() -> None:
    with_plugin = CapabilityPluginRegistry()
    with_plugin.register_plugin(android_device.AndroidDeviceCapabilityPlugin())
    without_plugin = CapabilityPluginRegistry()
    assert len(with_plugin.list_capabilities()) == 2
    assert without_plugin.list_capabilities() == []


@pytest.mark.asyncio
async def test_observe_handler_freezes_accepted_operation_with_receipt() -> None:
    handler = android_device.AndroidDeviceStubHandler(
        ops=android_device.OBSERVE_OPS
    )
    result = await handler.execute(
        _context(action_id="action-a", idempotency_key="action:action-a"),
        {"op": "ui_dump", "max_nodes": 50},
    )
    assert result.success is True
    assert result.status == "accepted"
    assert result.operation["kind"] == "android.device"
    assert result.operation["op"] == "ui_dump"
    assert result.write_receipt.action_id == "action-a"
    assert result.write_receipt.provider_operation_id.startswith("android:ui_dump:")


@pytest.mark.asyncio
async def test_actuate_handler_freezes_accepted_operation_with_receipt() -> None:
    handler = android_device.AndroidDeviceStubHandler(ops=android_device.ACTUATE_OPS)
    result = await handler.execute(
        _context(action_id="action-b", idempotency_key="action:action-b"),
        {"op": "tap", "x": 540, "y": 1200},
    )
    assert result.success is True
    assert result.status == "accepted"
    assert result.operation["provider_operation_id"].startswith("android:tap:")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "op,params",
    [
        ("tap", {"x": -1, "y": 10}),
        ("tap", {"y": 10}),
        ("swipe", {"x1": 0, "y1": 0, "x2": 10}),
        ("input_text", {"text": ""}),
        ("input_text", {"text": "你好世界"}),
        ("input_text", {"text": "x" * 501}),
        ("press_key", {"key": "SHELL"}),
        ("press_key", {}),
        ("launch_app", {"package": "com.evil; input text hi"}),
        ("launch_app", {"package": "settings"}),
        ("launch_app", {"package": "com.ok", "activity": "../escape"}),
        ("shell", {"cmd": "rm -rf /"}),
    ],
)
async def test_handler_fails_closed_on_invalid_input(op: str, params: dict) -> None:
    handler = android_device.AndroidDeviceStubHandler(
        ops=(*android_device.OBSERVE_OPS, *android_device.ACTUATE_OPS)
    )
    result = await handler.execute(
        _context(action_id="action-a", idempotency_key="action:action-a"),
        {"op": op, **params},
    )
    assert result.success is False
    assert result.error["code"] == "INVALID_PARAMETERS"


@pytest.mark.asyncio
async def test_handler_requires_frozen_action_identity() -> None:
    handler = android_device.AndroidDeviceStubHandler(ops=android_device.OBSERVE_OPS)
    result = await handler.execute(_context(), {"op": "screen_state"})
    assert result.success is False
    assert result.error["code"] == "ACTION_IDENTITY_REQUIRED"


@pytest.mark.asyncio
async def test_plugin_tool_maps_accepted_result_and_enforces_receipt() -> None:
    registry = CapabilityPluginRegistry()
    registry.register_plugin(android_device.AndroidDeviceCapabilityPlugin())
    resolved = registry.get("android.actuate", "1.0.0")
    assert resolved is not None
    definition, handler = resolved
    tool = _PluginTool(definition, handler)
    output = await tool.execute(
        op="launch_app",
        package="com.android.settings",
        tool_context=_tool_context(),
    )
    assert output.status == InvocationStatus.ACCEPTED
    assert output.operation["action_id"] == "action-a"
    assert output.operation["idempotency_key"] == "action:action-a"
    assert output.operation["provider_operation_id"].startswith("android:launch_app:")
    assert output.operation["kind"] == "android.device"

    class _NoReceiptHandler:
        async def execute(self, context, input):
            return android_device.CapabilityResult(
                success=True,
                status="accepted",
                operation={"kind": "android.device"},
            )

    rogue = _PluginTool(definition, _NoReceiptHandler())
    with pytest.raises(Exception) as raised:
        await rogue.execute(op="wake", tool_context=_tool_context())
    assert getattr(raised.value, "code", "") == "WRITE_RECEIPT_REQUIRED"


def test_parameter_contract_matches_the_phase0_probe() -> None:
    if not _PROBE_PATH.is_file():
        pytest.skip("companion ai-market probe checkout is not present")
    probe = _load_probe()
    assert android_device.PRESS_KEYS == probe.PRESS_KEYS
    valid = [
        ("ui_dump", {"max_nodes": 50}),
        ("screenshot", {}),
        ("screen_state", {}),
        ("current_app", {}),
        ("tap", {"x": 540, "y": 1200}),
        ("swipe", {"x1": 540, "y1": 1800, "x2": 540, "y2": 600, "duration_ms": 250}),
        ("input_text", {"text": "hello world"}),
        ("press_key", {"key": "back"}),
        ("launch_app", {"package": "com.android.settings"}),
        (
            "launch_app",
            {"package": "com.android.settings", "activity": ".MainSettings"},
        ),
        ("wake", {}),
    ]
    for op, params in valid:
        assert android_device._validate_params(op, params) is None, (op, params)
        if op != "screenshot":
            # screenshot is an exec-out op the probe handles outside build_shell_argv
            probe.build_shell_argv(op, params)
    invalid = [
        ("tap", {"x": -1, "y": 2}),
        ("input_text", {"text": "你好"}),
        ("press_key", {"key": "SHELL"}),
        ("launch_app", {"package": "com.evil; rm"}),
    ]
    for op, params in invalid:
        assert android_device._validate_params(op, params) is not None, (op, params)
        with pytest.raises(probe.ProbeError):
            probe.build_shell_argv(op, params)
