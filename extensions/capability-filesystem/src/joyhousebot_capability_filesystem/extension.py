"""Versioned capabilities for the current Run's isolated scratch space."""

from __future__ import annotations

from typing import Any

from joyhousebot.extension_sdk import (
    CapabilityContext,
    CapabilityDefinition,
    CapabilityExtensionManifest,
    CapabilityKind,
    CapabilityRef,
    CapabilityResult,
    WriteReceipt,
)
from joyhousebot.extension_sdk.manifest import source_tree_digest
from joyhousebot.extension_sdk.network import sanitize_error_message


class _ActionIdentityRequiredError(RuntimeError):
    pass


def _services(context: CapabilityContext) -> Any:
    if context.services is None:
        raise RuntimeError("Run scratch service is unavailable")
    return context.services.scratch


def _scratch_path(value: Any) -> str:
    path = str(value or "").strip().replace("\\", "/")
    if path == "memory" or path.startswith("memory/"):
        raise ValueError("Memory is not a scratch file; use the context-assets capabilities")
    return path


def _failure(code: str, message: str, *, retryable: bool = False) -> CapabilityResult:
    return CapabilityResult(
        success=False,
        error={"code": code, "message": message, "retryable": retryable},
    )


def _write_receipt(context: CapabilityContext, path: str) -> WriteReceipt:
    if not context.action_id or not context.idempotency_key:
        raise _ActionIdentityRequiredError(
            "scratch writes require a frozen Runtime Action identity"
        )
    return WriteReceipt(
        action_id=context.action_id,
        idempotency_key=context.idempotency_key,
        provider_operation_id=f"scratch:{path}",
    )


class ReadFileHandler:
    async def execute(
        self, context: CapabilityContext, input: dict[str, Any]
    ) -> CapabilityResult:
        try:
            path = _scratch_path(input.get("path"))
            content = await _services(context).read(context, path=path)
        except ValueError as exc:
            return _failure("INVALID_PARAMETERS", str(exc))
        except FileNotFoundError:
            return _failure("FILE_NOT_FOUND", "scratch file was not found")
        except IsADirectoryError:
            return _failure("NOT_A_FILE", "scratch path is not a file")
        except PermissionError as exc:
            return _failure("PATH_DENIED", str(exc))
        except Exception as exc:
            return _failure("FILE_READ_FAILED", sanitize_error_message(str(exc)))
        return CapabilityResult(success=True, output={"path": path, "content": content})


class WriteFileHandler:
    async def execute(
        self, context: CapabilityContext, input: dict[str, Any]
    ) -> CapabilityResult:
        try:
            path = _scratch_path(input.get("path"))
            receipt = _write_receipt(context, path)
            content = str(input.get("content") or "")
            await _services(context).write(context, path=path, content=content)
        except ValueError as exc:
            return _failure("INVALID_PARAMETERS", str(exc))
        except _ActionIdentityRequiredError as exc:
            return _failure("ACTION_IDENTITY_REQUIRED", str(exc))
        except PermissionError as exc:
            return _failure("PATH_DENIED", str(exc))
        except Exception as exc:
            return _failure("FILE_WRITE_FAILED", sanitize_error_message(str(exc)))
        return CapabilityResult(
            success=True,
            output={"path": path, "bytes": len(content.encode("utf-8"))},
            write_receipt=receipt,
        )


class EditFileHandler:
    async def execute(
        self, context: CapabilityContext, input: dict[str, Any]
    ) -> CapabilityResult:
        try:
            path = _scratch_path(input.get("path"))
            receipt = _write_receipt(context, path)
            old_text = str(input.get("old_text") or "")
            new_text = str(input.get("new_text") or "")
            if not old_text:
                raise ValueError("old_text is required")
            services = _services(context)
            content = await services.read(context, path=path)
            count = content.count(old_text)
            if count == 0:
                return _failure("TEXT_NOT_FOUND", "old_text was not found in the scratch file")
            if count > 1:
                return _failure(
                    "TEXT_NOT_UNIQUE",
                    f"old_text appears {count} times; provide more context",
                )
            content = content.replace(old_text, new_text, 1)
            await services.write(context, path=path, content=content)
        except ValueError as exc:
            return _failure("INVALID_PARAMETERS", str(exc))
        except _ActionIdentityRequiredError as exc:
            return _failure("ACTION_IDENTITY_REQUIRED", str(exc))
        except FileNotFoundError:
            return _failure("FILE_NOT_FOUND", "scratch file was not found")
        except PermissionError as exc:
            return _failure("PATH_DENIED", str(exc))
        except Exception as exc:
            return _failure("FILE_EDIT_FAILED", sanitize_error_message(str(exc)))
        return CapabilityResult(
            success=True,
            output={"path": path, "bytes": len(content.encode("utf-8"))},
            write_receipt=receipt,
        )


class ListDirHandler:
    async def execute(
        self, context: CapabilityContext, input: dict[str, Any]
    ) -> CapabilityResult:
        try:
            path = _scratch_path(input.get("path"))
            items = await _services(context).list(context, path=path)
        except ValueError as exc:
            return _failure("INVALID_PARAMETERS", str(exc))
        except FileNotFoundError:
            return _failure("DIRECTORY_NOT_FOUND", "scratch directory was not found")
        except NotADirectoryError:
            return _failure("NOT_A_DIRECTORY", "scratch path is not a directory")
        except PermissionError as exc:
            return _failure("PATH_DENIED", str(exc))
        except Exception as exc:
            return _failure("DIRECTORY_LIST_FAILED", sanitize_error_message(str(exc)))
        return CapabilityResult(success=True, output={"path": path, "items": items})


READ_SCHEMA = {
    "type": "object",
    "required": ["path"],
    "properties": {"path": {"type": "string", "minLength": 1}},
}
WRITE_SCHEMA = {
    "type": "object",
    "required": ["path", "content"],
    "properties": {
        "path": {"type": "string", "minLength": 1},
        "content": {"type": "string"},
    },
}
EDIT_SCHEMA = {
    "type": "object",
    "required": ["path", "old_text", "new_text"],
    "properties": {
        "path": {"type": "string", "minLength": 1},
        "old_text": {"type": "string", "minLength": 1},
        "new_text": {"type": "string"},
    },
}


class FilesystemCapabilityExtension:
    extension_id = "capability-filesystem"
    version = "1.0.0"

    def manifest(self) -> CapabilityExtensionManifest:
        return CapabilityExtensionManifest(
            extension_id=self.extension_id,
            version=self.version,
            name="Run Filesystem",
            description="Read and write only the current Run's isolated scratch space.",
            distribution_name="joyhousebot-capability-filesystem",
            build_digest=source_tree_digest(__file__),
            runtime_contract_version=2,
            required_permissions=("filesystem.read", "filesystem.write"),
            dependencies=(
                {"id": "runtime-scratch-service", "kind": "service", "required": True},
            ),
        )

    def register(self, registry: Any) -> None:
        registry.register_capability(
            _definition(
                "read_file",
                "Read scratch file",
                "Read a UTF-8 file from this Run's isolated scratch space.",
                READ_SCHEMA,
                "read",
                ("filesystem.read",),
            ),
            ReadFileHandler(),
        )
        registry.register_capability(
            _definition(
                "list_dir",
                "List scratch directory",
                "List a directory in this Run's isolated scratch space.",
                READ_SCHEMA,
                "read",
                ("filesystem.read",),
            ),
            ListDirHandler(),
        )
        registry.register_capability(
            _definition(
                "write_file",
                "Write scratch file",
                "Atomically write a UTF-8 file in this Run's isolated scratch space.",
                WRITE_SCHEMA,
                "write",
                ("filesystem.write",),
            ),
            WriteFileHandler(),
        )
        registry.register_capability(
            _definition(
                "edit_file",
                "Edit scratch file",
                "Replace one unique text fragment in a scratch file.",
                EDIT_SCHEMA,
                "write",
                ("filesystem.read", "filesystem.write"),
            ),
            EditFileHandler(),
        )

    def health_checks(self) -> tuple[Any, ...]:
        return ()


def _definition(
    capability_id: str,
    name: str,
    description: str,
    input_schema: dict[str, Any],
    side_effect: str,
    permissions: tuple[str, ...],
) -> CapabilityDefinition:
    return CapabilityDefinition(
        ref=CapabilityRef(capability_id, "1.0.0", CapabilityKind.CAPABILITY),
        name=name,
        description=description,
        input_schema=input_schema,
        output_schema={"type": "object"},
        adapter="extension",
        tags=("filesystem", "scratch"),
        expected_duration_seconds=1,
        timeout_seconds=10,
        idempotent=True,
        retryable=False,
        side_effect=side_effect,
        permissions=permissions,
        data_classification="confidential",
    )


def create_extension() -> FilesystemCapabilityExtension:
    return FilesystemCapabilityExtension()
