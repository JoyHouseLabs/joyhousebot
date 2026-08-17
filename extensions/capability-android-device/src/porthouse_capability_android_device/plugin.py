"""Device-routed Android capabilities.

These capabilities never touch a phone themselves.  They freeze the request as
an accepted Runtime operation; the paired Android Device Host claims the frozen
delivery over the Device Host Transport and executes it locally (see the
companion ai-market repo: docs/ANDROID_DEVICE_HOST.md).  Consequences of the
routing model:

- the stub has no ``reconcile_operation`` on purpose, so a frozen operation
  parks at ``manual_required`` instead of being polled by a Worker;
- operation resolution happens when the device completes the delivery, which
  resumes the waiting Run through the normal reconciliation path;
- deliveries only ever exist for actions that already passed capability
  approval (``android.actuate`` is ``side_effect=external`` and therefore
  human-approved before this handler runs at all).

Parameter rules mirror the shared op contract maintained in the companion
ai-market repo (``android/probe``); tests cross-check against it when the
companion checkout is present so the Kotlin executor and this stub cannot
drift apart.
"""

from __future__ import annotations

import re
from typing import Any

from porthouse.extension_sdk import (
    CapabilityContext,
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
    CapabilityResult,
    PluginManifest,
    WriteReceipt,
)
from porthouse.extension_sdk.manifest import source_tree_digest

OBSERVE_OPS = ("ui_dump", "screenshot", "screen_state", "current_app")
ACTUATE_OPS = ("tap", "swipe", "input_text", "press_key", "launch_app", "wake")

PRESS_KEYS = frozenset(
    {
        "BACK",
        "HOME",
        "MENU",
        "ENTER",
        "DEL",
        "TAB",
        "ESC",
        "SPACE",
        "PAGE_UP",
        "PAGE_DOWN",
        "MOVE_HOME",
        "MOVE_END",
        "DPAD_UP",
        "DPAD_DOWN",
        "DPAD_LEFT",
        "DPAD_RIGHT",
        "VOLUME_UP",
        "VOLUME_DOWN",
        "VOLUME_MUTE",
        "POWER",
        "APP_SWITCH",
        "MEDIA_PLAY",
        "MEDIA_PAUSE",
        "MEDIA_NEXT",
        "MEDIA_PREVIOUS",
    }
)

_MAX_TEXT_CHARS = 500
_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")
_ACTIVITY_RE = re.compile(r"^[A-Za-z0-9_.$]+$")
# Keep in lockstep with the probe charset: adb `input text` only types this
# subset reliably, and the on-device Shizuku executor enforces the same set.
_INPUT_TEXT_RE = re.compile(r"^[A-Za-z0-9 @%:;,. _\-+=/()?!'\"<>#$&*]+$")

_CONFIGURATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {},
}


def _observe_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": list(OBSERVE_OPS)},
            "max_nodes": {"type": "integer", "minimum": 1, "maximum": 1000},
            "max_width": {"type": "integer", "minimum": 1, "maximum": 4320},
        },
    }


def _actuate_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["op"],
        "properties": {
            "op": {"type": "string", "enum": list(ACTUATE_OPS)},
            "x": {"type": "integer", "minimum": 0},
            "y": {"type": "integer", "minimum": 0},
            "x1": {"type": "integer", "minimum": 0},
            "y1": {"type": "integer", "minimum": 0},
            "x2": {"type": "integer", "minimum": 0},
            "y2": {"type": "integer", "minimum": 0},
            "duration_ms": {"type": "integer", "minimum": 0, "maximum": 10_000},
            "text": {"type": "string", "minLength": 1, "maxLength": _MAX_TEXT_CHARS},
            "key": {"type": "string", "enum": sorted(PRESS_KEYS)},
            "package": {"type": "string", "minLength": 1, "maxLength": 256},
            "activity": {"type": "string", "minLength": 1, "maxLength": 256},
        },
    }


def _validate_params(op: str, params: dict[str, Any]) -> str | None:
    def _int(name: str) -> int | None:
        value = params.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        return value

    if op in {"ui_dump", "screenshot", "screen_state", "current_app", "wake"}:
        return None
    if op == "tap":
        if _int("x") is None or _int("y") is None:
            return "tap requires non-negative integer x and y"
        return None
    if op == "swipe":
        if any(_int(name) is None for name in ("x1", "y1", "x2", "y2")):
            return "swipe requires non-negative integer x1, y1, x2 and y2"
        return None
    if op == "input_text":
        text = params.get("text")
        if not isinstance(text, str) or not text or len(text) > _MAX_TEXT_CHARS:
            return "input_text requires text of 1..500 characters"
        if not _INPUT_TEXT_RE.fullmatch(text):
            return "input_text contains characters outside the shared charset"
        return None
    if op == "press_key":
        key = str(params.get("key") or "").strip().upper()
        if key not in PRESS_KEYS:
            return "press_key requires a key from the allowlist"
        return None
    if op == "launch_app":
        package = str(params.get("package") or "")
        if not _PACKAGE_RE.fullmatch(package):
            return "launch_app requires a valid Android package name"
        activity = params.get("activity")
        if activity is not None and (
            not isinstance(activity, str) or not _ACTIVITY_RE.fullmatch(activity)
        ):
            return "launch_app activity must be a plain Android component name"
        return None
    return f"unsupported op: {op}"


class AndroidDeviceStubHandler:
    """Freeze one device-routed op as an accepted operation for a paired phone."""

    def __init__(self, *, ops: tuple[str, ...]) -> None:
        self._ops = frozenset(ops)

    async def execute(
        self, context: CapabilityContext, input: dict[str, Any]
    ) -> CapabilityResult:
        op = str(input.get("op") or "").strip()
        if op not in self._ops:
            return _failure("INVALID_PARAMETERS", f"unsupported op: {op!r}")
        invalid = _validate_params(op, input)
        if invalid is not None:
            return _failure("INVALID_PARAMETERS", invalid)
        if not context.action_id or not context.idempotency_key:
            return _failure(
                "ACTION_IDENTITY_REQUIRED",
                "device-routed capabilities require a frozen Runtime Action identity",
            )
        provider_operation_id = f"android:{op}:{context.action_id}"
        return CapabilityResult(
            success=True,
            status="accepted",
            output={"op": op, "state": "queued_for_device"},
            operation={
                "kind": "android.device",
                "op": op,
                "provider_operation_id": provider_operation_id,
            },
            write_receipt=WriteReceipt(
                action_id=context.action_id,
                idempotency_key=context.idempotency_key,
                provider_operation_id=provider_operation_id,
            ),
        )


def _failure(code: str, message: str, *, retryable: bool = False) -> CapabilityResult:
    return CapabilityResult(
        success=False,
        error={"code": code, "message": message, "retryable": retryable},
    )


def _definition(
    *,
    capability_id: str,
    description: str,
    input_schema: dict[str, Any],
    side_effect: str,
    permissions: tuple[str, ...],
    tags: tuple[str, ...],
    idempotent: bool,
    retryable: bool,
    timeout_seconds: int,
) -> CapabilityDefinition:
    return CapabilityDefinition(
        ref=CapabilityRef(capability_id, "1.0.0", CapabilityKind.TOOL),
        name=capability_id,
        description=description,
        input_schema=input_schema,
        output_schema={"type": "object"},
        adapter="plugin",
        tags=tags,
        timeout_seconds=timeout_seconds,
        idempotent=idempotent,
        retryable=retryable,
        side_effect=side_effect,
        invocation_concurrency="sequential",
        max_concurrent_invocations=1,
        permissions=permissions,
        data_classification="confidential",
        configuration_schema=_CONFIGURATION_SCHEMA,
    )


class AndroidDeviceCapabilityPlugin:
    plugin_id = "capability-android-device"
    version = "1.0.0"

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id=self.plugin_id,
            version=self.version,
            name="Android Device",
            description=(
                "Route observe/actuate operations to a paired Android Device Host; "
                "execution happens on the device, never inside the Runtime."
            ),
            distribution_name="porthouse-capability-android-device",
            build_digest=source_tree_digest(__file__),
            runtime_contract_version=2,
            execution_isolation="in_process",
            required_permissions=("android.device",),
        )

    def register(self, registry: Any) -> None:
        registry.register_capability(
            _definition(
                capability_id="android.observe",
                description=(
                    "Read state from the paired Android device: ui_dump (compressed "
                    "accessibility node list), screenshot, screen_state, current_app. "
                    "Auto-approved; evidence returns as Run artifacts."
                ),
                input_schema=_observe_schema(),
                side_effect="internal",
                permissions=("android.observe",),
                tags=("android", "device", "observe"),
                idempotent=True,
                retryable=True,
                timeout_seconds=180,
            ),
            AndroidDeviceStubHandler(ops=OBSERVE_OPS),
        )
        registry.register_capability(
            _definition(
                capability_id="android.actuate",
                description=(
                    "Act on the paired Android device: tap, swipe, input_text, "
                    "press_key, launch_app, wake. Every call requires human approval "
                    "before the device receives anything."
                ),
                input_schema=_actuate_schema(),
                side_effect="external",
                permissions=("android.actuate",),
                tags=("android", "device", "actuate"),
                idempotent=False,
                retryable=False,
                timeout_seconds=300,
            ),
            AndroidDeviceStubHandler(ops=ACTUATE_OPS),
        )

    def health_checks(self) -> tuple[Any, ...]:
        return ()


def create_plugin() -> AndroidDeviceCapabilityPlugin:
    return AndroidDeviceCapabilityPlugin()
