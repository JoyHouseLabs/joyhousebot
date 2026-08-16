"""Versioned sandbox command capability."""

from __future__ import annotations

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


class ExecHandler:
    async def execute(
        self, context: CapabilityContext, input: dict[str, Any]
    ) -> CapabilityResult:
        command = str(input.get("command") or "").strip()
        if not command:
            return _failure("INVALID_PARAMETERS", "command is required")
        if not context.action_id or not context.idempotency_key:
            return _failure(
                "ACTION_IDENTITY_REQUIRED",
                "sandbox execution requires a frozen Runtime Action identity",
            )
        if context.services is None:
            return _failure("CONTEXT_REQUIRED", "Runtime sandbox service is unavailable")
        configuration = dict(
            context.metadata.get("capability_configuration") or {}
        )
        try:
            result = await context.services.sandbox.execute(
                context,
                command=command,
                working_dir=str(input.get("working_dir") or "."),
                configuration=configuration,
            )
        except (PermissionError, ValueError) as exc:
            return _failure("INVALID_PARAMETERS", str(exc))
        except Exception as exc:
            return _failure(
                "SANDBOX_EXECUTION_FAILED",
                sanitize_error_message(str(exc)),
            )
        if not result.get("success"):
            return _failure(
                str(result.get("code") or "SANDBOX_EXECUTION_FAILED"),
                sanitize_error_message(str(result.get("message") or "sandbox execution failed")),
                retryable=bool(result.get("retryable", False)),
            )
        return CapabilityResult(
            success=True,
            output={
                "output": str(result.get("output") or ""),
                "exit_code": int(result.get("exit_code") or 0),
            },
            write_receipt=WriteReceipt(
                action_id=context.action_id,
                idempotency_key=context.idempotency_key,
                provider_operation_id=f"sandbox:{context.action_id}",
            ),
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


CONFIGURATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "timeout": {"type": "integer", "minimum": 1, "maximum": 3600},
        "shell_mode": {"type": "boolean"},
        "container_image": {"type": "string", "minLength": 1},
        "container_user": {"type": "string"},
        "container_network": {"type": "string", "enum": ["none"]},
        "container_memory": {"type": "string", "minLength": 1},
        "container_cpus": {"type": "string", "minLength": 1},
        "container_pids_limit": {
            "type": "integer",
            "minimum": 16,
            "maximum": 4096,
        },
        "deny_patterns": {"type": "array", "items": {"type": "string"}},
        "allow_patterns": {"type": "array", "items": {"type": "string"}},
    },
}


class ShellCapabilityPlugin:
    plugin_id = "capability-shell"
    version = "1.0.0"

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id=self.plugin_id,
            version=self.version,
            name="Sandbox Command",
            description="Execute an approved command in a fail-closed isolated container.",
            distribution_name="porthouse-capability-shell",
            build_digest=source_tree_digest(__file__),
            runtime_contract_version=2,
            execution_isolation="container",
            required_permissions=("sandbox.exec",),
            dependencies=(
                {"id": "docker-sandbox", "kind": "service", "required": True},
                {"id": "runtime-scratch-service", "kind": "service", "required": True},
            ),
        )

    def register(self, registry: Any) -> None:
        registry.register_capability(
            CapabilityDefinition(
                ref=CapabilityRef("exec", self.version, CapabilityKind.TOOL),
                name="Execute sandbox command",
                description="Execute one approved command inside the current Run's container.",
                input_schema={
                    "type": "object",
                    "required": ["command"],
                    "properties": {
                        "command": {"type": "string", "minLength": 1},
                        "working_dir": {"type": "string", "default": "."},
                    },
                },
                output_schema={"type": "object"},
                adapter="plugin",
                tags=("sandbox", "shell"),
                execution_mode="immediate",
                expected_duration_seconds=10,
                timeout_seconds=3660,
                idempotent=False,
                retryable=False,
                side_effect="external",
                invocation_concurrency="sequential",
                max_concurrent_invocations=1,
                permissions=("sandbox.exec",),
                data_classification="confidential",
                configuration_schema=CONFIGURATION_SCHEMA,
            ),
            ExecHandler(),
        )

    def health_checks(self) -> tuple[Any, ...]:
        return ()


def create_plugin() -> ShellCapabilityPlugin:
    return ShellCapabilityPlugin()
