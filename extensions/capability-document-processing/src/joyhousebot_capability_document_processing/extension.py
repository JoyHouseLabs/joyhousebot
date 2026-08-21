"""Isolated extraction of one immutable, Run-bound private document."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any

from joyhousebot.extension_sdk import (
    Artifact,
    CapabilityContext,
    CapabilityDefinition,
    CapabilityExtensionManifest,
    CapabilityKind,
    CapabilityRef,
    CapabilityResult,
)
from joyhousebot.extension_sdk.manifest import source_tree_digest
from joyhousebot.extension_sdk.network import sanitize_error_message

from .subprocess_runner import run_document_subprocess

MAX_INPUT_BYTES = 25 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
DEFAULT_IMAGE = "joyhousebot-document-processing:0.1.0"
_DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_ALLOWED_MEDIA = {"application/pdf", _DOCX_MEDIA, "application/octet-stream"}
_WORKER_COMMAND = (
    "python -m joyhousebot_capability_document_processing.worker "
    "--input source.bin --request request.json --output result.json"
)


def _failure(code: str, message: str, *, retryable: bool = False) -> CapabilityResult:
    return CapabilityResult(
        success=False,
        error={"code": code, "message": message, "retryable": retryable},
    )


def _supported_document(display_name: str, media_type: str) -> bool:
    extension = PurePosixPath(display_name).suffix.lower()
    normalized = media_type.split(";", 1)[0].strip().lower()
    if normalized not in _ALLOWED_MEDIA:
        return False
    if normalized == "application/octet-stream":
        return extension in {".pdf", ".docx"}
    return normalized == "application/pdf" or normalized == _DOCX_MEDIA


def _sandbox_configuration(context: CapabilityContext) -> dict[str, Any]:
    configured = dict(context.metadata.get("capability_configuration") or {})
    return {
        "timeout": int(configured.get("timeout_seconds") or 120),
        "container_image": str(configured.get("container_image") or DEFAULT_IMAGE),
        "container_user": "65534:65534",
        "container_network": "none",
        "container_memory": str(configured.get("container_memory") or "512m"),
        "container_cpus": str(configured.get("container_cpus") or "1"),
        "container_pids_limit": int(configured.get("container_pids_limit") or 64),
        "shell_mode": False,
    }


def _isolation_backend(context: CapabilityContext) -> str:
    configured = dict(context.metadata.get("capability_configuration") or {})
    return str(configured.get("isolation_backend") or "subprocess").strip().lower()


def _validated_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("parser output must be an object")
    if not value.get("ok"):
        error = value.get("error")
        if not isinstance(error, dict) or not str(error.get("code") or "").strip():
            raise ValueError("parser failure is missing a closed error code")
        return value
    chunks = value.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise ValueError("parser output must contain at least one text chunk")
    total_chars = 0
    for chunk in chunks:
        if not isinstance(chunk, dict) or not isinstance(chunk.get("text"), str):
            raise ValueError("parser chunk is invalid")
        total_chars += len(chunk["text"])
        if total_chars > 500_000:
            raise ValueError("parser output exceeds the character limit")
        page = chunk.get("page")
        if page is not None and (not isinstance(page, int) or page < 1 or page > 200):
            raise ValueError("parser chunk page is outside the allowed range")
    if not str(value.get("parser_id") or "").strip():
        raise ValueError("parser identity is required")
    return value


class DocumentExtractHandler:
    def __init__(self, *, subprocess_runner: Any = run_document_subprocess) -> None:
        self._subprocess_runner = subprocess_runner

    async def _execute_parser(
        self,
        context: CapabilityContext,
        services: Any,
        *,
        source: dict[str, Any],
        request: dict[str, Any],
    ) -> dict[str, Any]:
        configured = dict(context.metadata.get("capability_configuration") or {})
        backend = _isolation_backend(context)
        if backend == "subprocess":
            return await self._subprocess_runner(
                body=bytes(source["body"]),
                request=request,
                timeout_seconds=int(configured.get("timeout_seconds") or 120),
                memory_mb=int(configured.get("memory_limit_mb") or 512),
            )
        if backend != "container":
            raise ValueError("isolation_backend must be subprocess or container")
        if not hasattr(services, "sandbox"):
            return {
                "success": False,
                "code": "SANDBOX_UNAVAILABLE",
                "message": "container document isolation is unavailable",
                "retryable": True,
            }
        return await services.sandbox.execute_job(
            context,
            command=_WORKER_COMMAND,
            input_files={
                "source.bin": bytes(source["body"]),
                "request.json": json.dumps(request, ensure_ascii=False).encode("utf-8"),
            },
            output_files=("result.json",),
            configuration=_sandbox_configuration(context),
            max_input_bytes=MAX_INPUT_BYTES + 16_384,
            max_output_bytes=MAX_OUTPUT_BYTES,
        )

    async def execute(self, context: CapabilityContext, input: dict[str, Any]) -> CapabilityResult:
        asset_id = str(input.get("asset_id") or "").strip()
        if not asset_id:
            return _failure("INVALID_PARAMETERS", "asset_id is required")
        services = context.services
        if services is None or not hasattr(services, "context"):
            return _failure("CONTEXT_REQUIRED", "document extraction services are unavailable")
        try:
            source = await services.context.read_input_asset(
                context,
                asset_id=asset_id,
                max_bytes=MAX_INPUT_BYTES,
            )
        except PermissionError as exc:
            return _failure("REFERENCE_ACCESS_DENIED", str(exc))
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            return _failure(
                "REFERENCE_READ_FAILED",
                sanitize_error_message(str(exc)),
                retryable=isinstance(exc, (FileNotFoundError, OSError, RuntimeError)),
            )

        display_name = str(source.get("display_name") or "document")[:500]
        media_type = str(source.get("media_type") or "application/octet-stream")
        if not _supported_document(display_name, media_type):
            return _failure("UNSUPPORTED_MEDIA_TYPE", "only PDF and DOCX are supported")
        request = {
            "asset_id": asset_id,
            "display_name": display_name,
            "media_type": media_type,
            "max_pages": max(1, min(int(input.get("max_pages") or 200), 200)),
            "max_chars": max(1_000, min(int(input.get("max_chars") or 500_000), 500_000)),
        }
        try:
            job = await self._execute_parser(
                context,
                services,
                source=source,
                request=request,
            )
        except (PermissionError, ValueError) as exc:
            return _failure("INVALID_PARAMETERS", sanitize_error_message(str(exc)))
        except Exception as exc:
            return _failure("ISOLATION_EXECUTION_FAILED", sanitize_error_message(str(exc)))
        if not job.get("success"):
            return _failure(
                str(job.get("code") or "SANDBOX_EXECUTION_FAILED"),
                sanitize_error_message(str(job.get("message") or "sandbox execution failed")),
                retryable=bool(job.get("retryable", False)),
            )
        try:
            parsed = _validated_result(
                json.loads(bytes(job["files"]["result.json"]).decode("utf-8"))
            )
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return _failure("PARSER_OUTPUT_INVALID", sanitize_error_message(str(exc)))
        if not parsed.get("ok"):
            error = parsed["error"]
            return _failure(
                str(error["code"]),
                sanitize_error_message(str(error.get("message") or "document parse failed")),
                retryable=bool(error.get("retryable", False)),
            )

        artifact_data = {
            "schema_version": 1,
            "source": {
                "asset_id": asset_id,
                "content_sha256": str(source.get("content_sha256") or ""),
                "byte_size": int(source.get("byte_size") or len(source["body"])),
                "media_type": media_type,
            },
            "parser_id": parsed["parser_id"],
            "parser_version": str(parsed.get("parser_version") or "1"),
            "chunks": parsed["chunks"],
            "trace": dict(parsed.get("trace") or {}),
        }
        encoded = json.dumps(
            artifact_data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        identity = hashlib.sha256(
            f"{context.run_id}\0{asset_id}\0{artifact_data['parser_id']}\0"
            f"{artifact_data['parser_version']}".encode()
        ).hexdigest()[:32]
        artifact_id = f"artifact_document_extract_{identity}"
        artifact = Artifact(
            artifact_id=artifact_id,
            artifact_type="document.extracted_text",
            media_type="application/vnd.joyhousebot.document-extract+json",
            data=artifact_data,
            content_sha256=hashlib.sha256(encoded).hexdigest(),
            provenance={
                "source_kind": "runtime_input_asset",
                "source_asset_id": asset_id,
                "source_content_sha256": str(source.get("content_sha256") or ""),
            },
            evidence={
                "chunk_count": len(parsed["chunks"]),
                "parser_id": parsed["parser_id"],
                "parser_version": str(parsed.get("parser_version") or "1"),
            },
            metadata={"name": f"Extracted text: {display_name}"},
        )
        return CapabilityResult(
            success=True,
            output={
                "artifact_id": artifact_id,
                "parser_id": parsed["parser_id"],
                "parser_version": str(parsed.get("parser_version") or "1"),
                "chunk_count": len(parsed["chunks"]),
                "content_sha256": artifact.content_sha256,
            },
            artifacts=[artifact],
        )


INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["asset_id"],
    "properties": {
        "asset_id": {"type": "string", "minLength": 1},
        "max_pages": {"type": "integer", "minimum": 1, "maximum": 200},
        "max_chars": {"type": "integer", "minimum": 1000, "maximum": 500000},
    },
}

CONFIGURATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "isolation_backend": {
            "type": "string",
            "enum": ["subprocess", "container"],
            "default": "subprocess",
        },
        "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600},
        "memory_limit_mb": {"type": "integer", "minimum": 128, "maximum": 2048},
        "container_image": {"type": "string", "minLength": 1},
        "container_memory": {"type": "string", "minLength": 1},
        "container_cpus": {"type": "string", "minLength": 1},
        "container_pids_limit": {"type": "integer", "minimum": 16, "maximum": 256},
    },
}


class DocumentProcessingExtension:
    extension_id = "capability-document-processing"
    version = "1.1.0"

    def manifest(self) -> CapabilityExtensionManifest:
        return CapabilityExtensionManifest(
            extension_id=self.extension_id,
            version=self.version,
            name="Private Document Processing",
            description="Extract bounded text and evidence from private Run Input Assets.",
            distribution_name="joyhousebot-capability-document-processing",
            build_digest=source_tree_digest(__file__),
            runtime_contract_version=2,
            execution_isolation="subprocess",
            required_permissions=("context.read", "document.extract"),
            dependencies=(
                {"id": "runtime-context-services", "kind": "service", "required": True},
            ),
        )

    def register(self, registry: Any) -> None:
        registry.register_capability(
            CapabilityDefinition(
                ref=CapabilityRef("document.extract", self.version, CapabilityKind.CAPABILITY),
                name="Extract private document",
                description=(
                    "Extract bounded PDF or DOCX text from one immutable Input Asset "
                    "already bound to the current Run."
                ),
                input_schema=INPUT_SCHEMA,
                output_schema={"type": "object"},
                adapter="extension",
                tags=("document", "extract", "private", "artifact"),
                expected_duration_seconds=15,
                timeout_seconds=660,
                idempotent=True,
                retryable=True,
                side_effect="read",
                permissions=("context.read", "document.extract"),
                data_classification="restricted",
                invocation_concurrency="sequential",
                max_concurrent_invocations=1,
                configuration_schema=CONFIGURATION_SCHEMA,
            ),
            DocumentExtractHandler(),
        )

    def health_checks(self) -> tuple[Any, ...]:
        return ()


def create_extension() -> DocumentProcessingExtension:
    return DocumentProcessingExtension()
