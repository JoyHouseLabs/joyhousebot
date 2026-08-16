"""Versioned model-facing controls over Core Runtime mechanisms."""

from __future__ import annotations

import asyncio
from datetime import datetime
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
from porthouse.extension_sdk.network import sanitize_error_message
from porthouse.extension_sdk.schedules import (
    CronSchedule,
    ScratchRevisionConflictError,
)


def _failure(
    code: str,
    message: str,
    *,
    retryable: bool = False,
) -> CapabilityResult:
    return CapabilityResult(
        success=False,
        error={"code": code, "message": message, "retryable": retryable},
    )


def _receipt(context: CapabilityContext, operation_id: str) -> WriteReceipt:
    if not context.action_id or not context.idempotency_key:
        raise ValueError("Runtime control writes require a frozen Action identity")
    return WriteReceipt(
        action_id=context.action_id,
        idempotency_key=context.idempotency_key,
        provider_operation_id=operation_id,
    )


def _identity_error(context: CapabilityContext) -> CapabilityResult | None:
    if context.action_id and context.idempotency_key:
        return None
    return _failure(
        "ACTION_IDENTITY_REQUIRED",
        "Runtime control writes require a frozen Action identity",
    )


class MessageHandler:
    async def execute(
        self, context: CapabilityContext, input: dict[str, Any]
    ) -> CapabilityResult:
        if error := _identity_error(context):
            return error
        content = str(input.get("content") or "").strip()
        if not content:
            return _failure("INVALID_PARAMETERS", "content is required")
        if context.services is None:
            return _failure("CAPABILITY_UNAVAILABLE", "message delivery is not configured")
        channel = str(context.metadata.get("channel") or "")
        chat_id = str(context.metadata.get("chat_id") or "")
        try:
            target = await context.services.delivery.send(
                context,
                content=content,
                channel=channel,
                chat_id=chat_id,
            )
        except ValueError as exc:
            return _failure("DELIVERY_TARGET_REQUIRED", str(exc))
        except Exception as exc:
            return _failure("MESSAGE_SEND_FAILED", sanitize_error_message(str(exc)), retryable=True)
        return CapabilityResult(
            success=True,
            output={"sent": True, **target},
            write_receipt=_receipt(context, target["outbound_id"]),
        )


class SpawnHandler:
    async def execute(
        self, context: CapabilityContext, input: dict[str, Any]
    ) -> CapabilityResult:
        if error := _identity_error(context):
            return error
        if context.services is None:
            return _failure("CAPABILITY_UNAVAILABLE", "child Run service is unavailable")
        try:
            output = await context.services.child_runs.spawn(context, **input)
        except Exception as exc:
            code = "SUBAGENT_FANOUT_LIMIT" if "fan-out limit" in str(exc) else (
                "SPAWN_FAILED"
            )
            return _failure(code, sanitize_error_message(str(exc)), retryable=code == "SPAWN_FAILED")
        status = getattr(output.status, "value", str(output.status))
        operation = dict(output.operation or {})
        return CapabilityResult(
            success=True,
            output={
                "summary": output.summary or output.content,
                **dict(output.data or {}),
            },
            status=status,
            operation=operation or None,
            write_receipt=_receipt(
                context,
                str(operation.get("run_id") or f"child:{context.action_id}"),
            ),
        )

    async def reconcile_operation(
        self,
        context: CapabilityContext,
        operation: dict[str, Any],
    ) -> Any:
        if context.services is None:
            raise RuntimeError("child Run service is unavailable")
        return await context.services.child_runs.reconcile(context, operation)


class ScheduleHandler:
    async def execute(
        self, context: CapabilityContext, input: dict[str, Any]
    ) -> CapabilityResult:
        if error := _identity_error(context):
            return error
        if context.services is None:
            return _failure("CAPABILITY_UNAVAILABLE", "Schedule service is unavailable")
        action = str(input.get("action") or "")
        try:
            if action == "list":
                jobs = await asyncio.to_thread(context.services.schedules.list, context)
                output = {"jobs": jobs, "count": len(jobs)}
                operation_id = f"schedule-list:{context.user_id}"
            elif action == "remove":
                job_id = str(input.get("job_id") or "").strip()
                if not job_id:
                    return _failure("INVALID_PARAMETERS", "job_id is required for remove")
                removed = await asyncio.to_thread(
                    context.services.schedules.remove,
                    context,
                    job_id=job_id,
                )
                output = {"job_id": job_id, "removed": removed}
                operation_id = job_id
            elif action == "add":
                schedule, delete_after = _schedule_from_input(input)
                message = str(input.get("message") or "").strip()
                if not message:
                    return _failure("INVALID_PARAMETERS", "message is required for add")
                channel = str(context.metadata.get("channel") or "")
                chat_id = str(context.metadata.get("chat_id") or "")
                if not channel or not chat_id:
                    return _failure(
                        "DELIVERY_TARGET_REQUIRED",
                        "Run context has no delivery target",
                    )
                monitor = bool(input.get("monitor", False))
                session_mode = str(input.get("session_mode") or "isolated")
                preflight_mode = str(input.get("preflight_mode") or "always")
                context_mode = str(input.get("context_mode") or "full")
                _validate_monitor_modes(session_mode, preflight_mode, context_mode)
                active_hours = _active_hours(input)
                output = await asyncio.to_thread(
                    context.services.schedules.add,
                    context,
                    schedule=schedule,
                    message=message,
                    channel=channel,
                    chat_id=chat_id,
                    monitor=monitor,
                    session_mode=session_mode,
                    preflight_mode=preflight_mode,
                    context_mode=context_mode,
                    active_hours=active_hours,
                    session_id=context.session_id,
                    delete_after_run=delete_after,
                )
                operation_id = str(output["job_id"])
            else:
                return _failure("INVALID_ACTION", f"Unknown action: {action}")
        except (TypeError, ValueError) as exc:
            return _failure("INVALID_PARAMETERS", sanitize_error_message(str(exc)))
        except Exception as exc:
            return _failure("SCHEDULE_OPERATION_FAILED", sanitize_error_message(str(exc)), retryable=True)
        return CapabilityResult(
            success=True,
            output=output,
            write_receipt=_receipt(context, operation_id),
        )


class MonitorScratchHandler:
    async def execute(
        self, context: CapabilityContext, input: dict[str, Any]
    ) -> CapabilityResult:
        if error := _identity_error(context):
            return error
        if context.services is None:
            return _failure("CAPABILITY_UNAVAILABLE", "Monitor scratch service is unavailable")
        action = str(input.get("action") or "")
        try:
            if action == "get":
                state = await asyncio.to_thread(
                    context.services.schedules.get_monitor_scratch,
                    context,
                )
            elif action == "update":
                if input.get("content") is None or input.get("expected_revision") is None:
                    return _failure(
                        "INVALID_PARAMETERS",
                        "content and expected_revision are required for update",
                    )
                state = await asyncio.to_thread(
                    context.services.schedules.update_monitor_scratch,
                    context,
                    content=str(input["content"]),
                    expected_revision=int(input["expected_revision"]),
                )
            else:
                return _failure("INVALID_ACTION", f"Unknown action: {action}")
        except PermissionError as exc:
            return _failure("MONITOR_CONTEXT_REQUIRED", str(exc))
        except ScratchRevisionConflictError as exc:
            return _failure("REVISION_CONFLICT", str(exc), retryable=True)
        except ValueError as exc:
            return _failure("INVALID_PARAMETERS", str(exc))
        except Exception as exc:
            return _failure("MONITOR_SCRATCH_FAILED", sanitize_error_message(str(exc)))
        if state is None:
            return _failure("MONITOR_NOT_FOUND", "Agent Monitor not found")
        return CapabilityResult(
            success=True,
            output={"revision": state["revision"], "content": state["content"]},
            write_receipt=_receipt(
                context,
                f"monitor:{context.metadata.get('schedule_id')}:{state['revision']}",
            ),
        )


def _schedule_from_input(input: dict[str, Any]) -> tuple[CronSchedule, bool]:
    if input.get("every_seconds") is not None:
        every_seconds = int(input["every_seconds"])
        if every_seconds < 60:
            raise ValueError("every_seconds must be at least 60")
        return CronSchedule(kind="every", every_ms=every_seconds * 1000), False
    if input.get("cron_expr"):
        return CronSchedule(kind="cron", expr=str(input["cron_expr"])), False
    if input.get("at"):
        instant = datetime.fromisoformat(str(input["at"]))
        return CronSchedule(kind="at", at_ms=int(instant.timestamp() * 1000)), True
    raise ValueError("either every_seconds, cron_expr, or at is required")


def _validate_monitor_modes(session_mode: str, preflight_mode: str, context_mode: str) -> None:
    if session_mode not in {"isolated", "main"}:
        raise ValueError("session_mode must be isolated or main")
    if preflight_mode not in {"always", "runtime_attention"}:
        raise ValueError("preflight_mode must be always or runtime_attention")
    if context_mode not in {"full", "light"}:
        raise ValueError("context_mode must be full or light")


def _active_hours(input: dict[str, Any]) -> dict[str, str] | None:
    values = (
        input.get("active_hours_start"),
        input.get("active_hours_end"),
        input.get("active_hours_timezone"),
    )
    if any(values) and not all(values):
        raise ValueError("active hours require start, end, and timezone")
    if not all(values):
        return None
    return {"start": str(values[0]), "end": str(values[1]), "timezone": str(values[2])}


MESSAGE_SCHEMA = {
    "type": "object",
    "required": ["content"],
    "properties": {"content": {"type": "string", "minLength": 1}},
}
SPAWN_SCHEMA = {
    "type": "object",
    "required": ["task"],
    "properties": {
        "task": {"type": "string", "minLength": 1},
        "label": {"type": "string"},
        "agent_id": {"type": "string"},
        "output_schema": {"type": "object"},
    },
}
SCHEDULE_SCHEMA = {
    "type": "object",
    "required": ["action"],
    "properties": {
        "action": {"type": "string", "enum": ["add", "list", "remove"]},
        "message": {"type": "string"},
        "every_seconds": {"type": "integer", "minimum": 60},
        "cron_expr": {"type": "string"},
        "at": {"type": "string"},
        "job_id": {"type": "string"},
        "monitor": {"type": "boolean"},
        "session_mode": {"type": "string", "enum": ["isolated", "main"]},
        "preflight_mode": {"type": "string", "enum": ["always", "runtime_attention"]},
        "context_mode": {"type": "string", "enum": ["full", "light"]},
        "active_hours_start": {"type": "string"},
        "active_hours_end": {"type": "string"},
        "active_hours_timezone": {"type": "string"},
    },
}
MONITOR_SCHEMA = {
    "type": "object",
    "required": ["action"],
    "properties": {
        "action": {"type": "string", "enum": ["get", "update"]},
        "content": {"type": "string", "maxLength": 16384},
        "expected_revision": {"type": "integer", "minimum": 0},
    },
}


class RuntimeControlPlugin:
    plugin_id = "capability-runtime-control"
    version = "1.0.0"

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id=self.plugin_id,
            version=self.version,
            name="Runtime Control",
            description="Model-facing controls over Core delivery, child Runs, and Schedules.",
            distribution_name="porthouse-capability-runtime-control",
            build_digest=source_tree_digest(__file__),
            runtime_contract_version=2,
            required_permissions=(
                "channel.send",
                "runs.spawn",
                "schedule.manage",
                "monitor.scratch",
            ),
            dependencies=(
                {"id": "runtime-control-services", "kind": "service", "required": True},
                {"id": "postgresql", "kind": "database", "required": True},
            ),
        )

    def register(self, registry: Any) -> None:
        registry.register_capability(
            _definition("message", "Send origin message", MESSAGE_SCHEMA, "external", False, ("channel.send",)),
            MessageHandler(),
        )
        registry.register_capability(
            _definition("spawn", "Submit child Run", SPAWN_SCHEMA, "internal", True, ("runs.spawn",), retryable=True),
            SpawnHandler(),
        )
        registry.register_capability(
            _definition("cron", "Manage Schedules", SCHEDULE_SCHEMA, "write", True, ("schedule.manage",), retryable=True),
            ScheduleHandler(),
        )
        registry.register_capability(
            _definition("monitor_scratch", "Manage Monitor scratch", MONITOR_SCHEMA, "internal", True, ("monitor.scratch",), retryable=True),
            MonitorScratchHandler(),
        )

    def health_checks(self) -> tuple[Any, ...]:
        return ()


def _definition(
    capability_id: str,
    name: str,
    input_schema: dict[str, Any],
    side_effect: str,
    idempotent: bool,
    permissions: tuple[str, ...],
    *,
    retryable: bool = False,
) -> CapabilityDefinition:
    return CapabilityDefinition(
        ref=CapabilityRef(capability_id, "1.0.0", CapabilityKind.TOOL),
        name=name,
        description=name,
        input_schema=input_schema,
        output_schema={"type": "object"},
        adapter="plugin",
        tags=("runtime-control",),
        expected_duration_seconds=2,
        timeout_seconds=60,
        idempotent=idempotent,
        retryable=retryable,
        side_effect=side_effect,
        invocation_concurrency="sequential",
        max_concurrent_invocations=1,
        permissions=permissions,
        data_classification="confidential",
    )


def create_plugin() -> RuntimeControlPlugin:
    return RuntimeControlPlugin()
