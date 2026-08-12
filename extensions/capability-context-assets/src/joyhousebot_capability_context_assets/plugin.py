"""Optional durable context, memory, and knowledge capabilities."""

from __future__ import annotations

from typing import Any

from joyhousebot.extension_sdk import (
    CapabilityContext,
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
    CapabilityResult,
    PluginManifest,
    WriteReceipt,
)
from joyhousebot.extension_sdk.manifest import source_tree_digest
from joyhousebot.extension_sdk.network import DEFAULT_MAX_BYTES, sanitize_error_message

from .ingest.source_parsers import DEFAULT_SOURCE_PARSERS, SourceParseError
from .ingest.url_ingest import fetch_and_ingest_url


def _relative_memory_path(path: str) -> str:
    clean = str(path or "").strip().replace("\\", "/").lstrip("/")
    if clean.startswith("memory/"):
        clean = clean[7:]
    if not clean or any(part in {"", ".", ".."} for part in clean.split("/")):
        return ""
    return clean


def _services(context: CapabilityContext) -> Any:
    services = context.services
    if services is None:
        raise RuntimeError("capability runtime context services are unavailable")
    return services.context


class RetrieveHandler:
    async def execute(
        self, context: CapabilityContext, input: dict[str, Any]
    ) -> CapabilityResult:
        query = str(input.get("query") or "").strip()
        if not query:
            return _failure("INVALID_PARAMETERS", "query is required")
        try:
            scope = str(input.get("scope") or "knowledge")
            hits = await _services(context).search(
                context,
                query=query,
                top_k=int(input.get("top_k") or 10),
                source_type=str(input["source_type"]) if input.get("source_type") else None,
                collection_ref=(
                    str(input["collection_ref"]) if input.get("collection_ref") else None
                ),
                scope=scope,
            )
        except PermissionError as exc:
            return _failure("MEMORY_ACCESS_DENIED", str(exc))
        except FileNotFoundError:
            return _failure("KNOWLEDGE_NOT_INITIALIZED", "Knowledge base not initialized")
        except Exception as exc:
            return _failure("RETRIEVE_FAILED", sanitize_error_message(str(exc)), retryable=True)
        return CapabilityResult(
            success=True,
            output={"query": query, "scope": scope, "count": len(hits), "hits": hits},
        )


class MemoryGetHandler:
    async def execute(
        self, context: CapabilityContext, input: dict[str, Any]
    ) -> CapabilityResult:
        path = str(input.get("path") or "")
        relative = _relative_memory_path(path)
        if not relative:
            return _failure("INVALID_PARAMETERS", "invalid memory path")
        try:
            text = await _services(context).read_memory(
                context,
                relative_path=relative,
                start_line=_optional_int(input.get("start_line")),
                num_lines=_optional_int(input.get("num_lines")),
            )
        except ValueError as exc:
            return _failure("CONTEXT_REQUIRED", str(exc))
        except PermissionError as exc:
            return _failure("MEMORY_ACCESS_DENIED", str(exc))
        except Exception as exc:
            return _failure("MEMORY_READ_FAILED", sanitize_error_message(str(exc)))
        return CapabilityResult(success=True, output={"text": text, "path": path})


class FetchUrlToKnowledgebaseHandler:
    async def execute(
        self, context: CapabilityContext, input: dict[str, Any]
    ) -> CapabilityResult:
        url = str(input.get("url") or "").strip()
        if not url:
            return _failure("INVALID_PARAMETERS", "url is required")
        if not context.action_id or not context.idempotency_key:
            return _failure(
                "ACTION_IDENTITY_REQUIRED",
                "knowledge writes require a frozen Runtime Action identity",
            )
        try:
            services = _services(context)
        except RuntimeError as exc:
            return _failure("CONTEXT_REQUIRED", str(exc))
        try:
            document = await fetch_and_ingest_url(url)
        except ValueError as exc:
            return _failure("INVALID_URL", sanitize_error_message(str(exc)))
        except Exception as exc:
            return _failure("FETCH_FAILED", sanitize_error_message(str(exc)), retryable=True)
        try:
            doc_id = await services.index_knowledge(
                context,
                source_type=document.source_type,
                source_url=document.source_url,
                title=document.title,
                chunks=[{"text": item.text, "page": item.page} for item in document.chunks],
                metadata={"trace": document.trace},
            )
        except Exception as exc:
            return _failure("KNOWLEDGE_WRITE_FAILED", sanitize_error_message(str(exc)), retryable=True)
        return CapabilityResult(
            success=True,
            output={
                "ok": True,
                "doc_id": doc_id,
                "url": document.source_url,
                "title": document.title,
                "chunk_count": len(document.chunks),
                "message": "Content indexed in the durable knowledge store.",
            },
            write_receipt=WriteReceipt(
                action_id=context.action_id,
                idempotency_key=context.idempotency_key,
                provider_operation_id=doc_id,
            ),
        )


class IndexKnowledgeHandler:
    async def execute(
        self, context: CapabilityContext, input: dict[str, Any]
    ) -> CapabilityResult:
        if not context.action_id or not context.idempotency_key:
            return _failure(
                "ACTION_IDENTITY_REQUIRED",
                "knowledge writes require a frozen Runtime Action identity",
            )
        source_type = str(input.get("source_type") or "note")
        source_url = str(input.get("source_url") or "")
        try:
            services = _services(context)
        except RuntimeError as exc:
            return _failure("CONTEXT_REQUIRED", str(exc))
        try:
            async def load_input_asset(asset_id: str) -> dict[str, Any]:
                return await services.read_input_asset(
                    context,
                    asset_id=asset_id,
                    max_bytes=DEFAULT_MAX_BYTES,
                )

            parsed = await DEFAULT_SOURCE_PARSERS.parse_snapshot(
                input,
                input_asset_loader=load_input_asset,
            )
        except SourceParseError as exc:
            try:
                await services.fail_knowledge_index(
                    context,
                    source_type=source_type,
                    source_url=source_url,
                    title=str(input.get("title") or "Untitled knowledge source"),
                    error_code=exc.code,
                    error_message=str(exc),
                    metadata=_index_metadata(input, {"parse_failure": exc.code}),
                    source_system=str(input["source_system"]),
                    source_id=str(input["source_id"]),
                    source_version=str(input["source_version"]),
                    source_generation=int(input["source_generation"]),
                    source_status=str(input.get("source_status") or "active"),
                    index_profile_id=str(input.get("index_profile_id") or "lexical-v1"),
                    parser_id=exc.parser_id,
                    parser_version=exc.parser_version,
                    chunker_id="semantic-text-v1",
                    chunker_version="2",
                )
            except Exception as failure_exc:
                return _failure(
                    "KNOWLEDGE_FAILURE_RECORD_FAILED",
                    sanitize_error_message(str(failure_exc)),
                    retryable=True,
                )
            return _failure(exc.code, str(exc), retryable=exc.retryable)
        try:
            doc_id = await services.index_knowledge(
                context,
                source_type=source_type,
                source_url=source_url,
                title=str(input.get("title") or "Untitled knowledge source"),
                chunks=parsed.chunks,
                metadata=_index_metadata(input, parsed.trace),
                source_system=str(input["source_system"]),
                source_id=str(input["source_id"]),
                source_version=str(input["source_version"]),
                source_generation=int(input["source_generation"]),
                source_status=str(input.get("source_status") or "active"),
                index_profile_id=str(input.get("index_profile_id") or "lexical-v1"),
                parser_id=parsed.parser_id,
                parser_version=parsed.parser_version,
                chunker_id="semantic-text-v1",
                chunker_version="2",
            )
        except ValueError as exc:
            return _failure("INVALID_SOURCE", sanitize_error_message(str(exc)))
        except Exception as exc:
            return _failure(
                "KNOWLEDGE_INDEX_FAILED",
                sanitize_error_message(str(exc)),
                retryable=True,
            )
        return CapabilityResult(
            success=True,
            output={
                "doc_id": doc_id,
                "chunk_count": len(parsed.chunks),
                "source_version": input["source_version"],
                "parser_id": parsed.parser_id,
            },
            write_receipt=WriteReceipt(
                action_id=context.action_id,
                idempotency_key=context.idempotency_key,
                provider_operation_id=doc_id,
            ),
        )


def _index_metadata(input: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    return {
        "trace": trace,
        "tags": list(input.get("tags") or []),
        "collection_refs": list(input.get("collection_refs") or []),
        "snapshot_content_sha256": input.get("content_sha256"),
    }


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


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


RETRIEVE_SCHEMA = {
    "type": "object",
    "required": ["query"],
    "properties": {
        "query": {"type": "string", "minLength": 1},
        "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
        "source_type": {
            "type": "string",
            "enum": [
                "url",
                "note",
                "web",
                "file",
                "image",
                "video",
                "email",
                "capture",
                "paper",
                "report",
            ],
        },
        "scope": {"type": "string", "enum": ["knowledge", "memory"]},
        "collection_ref": {"type": "string", "minLength": 1},
    },
}
MEMORY_GET_SCHEMA = {
    "type": "object",
    "required": ["path"],
    "properties": {
        "path": {"type": "string", "minLength": 1},
        "start_line": {"type": "integer", "minimum": 1},
        "num_lines": {"type": "integer", "minimum": 1},
    },
}
FETCH_TO_KNOWLEDGE_SCHEMA = {
    "type": "object",
    "required": ["url"],
    "properties": {"url": {"type": "string", "minLength": 1}},
}
INDEX_KNOWLEDGE_SCHEMA = {
    "type": "object",
    "required": [
        "source_system",
        "source_id",
        "source_version",
        "source_generation",
        "source_type",
        "title",
        "content_sha256",
    ],
    "properties": {
        "source_system": {"type": "string", "minLength": 1},
        "source_id": {"type": "string", "minLength": 1},
        "source_version": {"type": "string", "minLength": 1},
        "source_generation": {"type": "integer", "minimum": 1},
        "source_status": {"type": "string", "enum": ["inbox", "active", "archived"]},
        "source_type": {"type": "string"},
        "title": {"type": "string", "minLength": 1},
        "content": {"type": "string"},
        "source_url": {"type": "string"},
        "attachments": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "required": ["reference_kind"],
                "properties": {
                    "reference_kind": {
                        "type": "string",
                        "enum": ["url", "local_vault", "cloud_vault", "runtime_input"],
                    },
                    "uri": {"type": "string"},
                    "asset_id": {"type": "string", "minLength": 1},
                    "display_name": {"type": "string"},
                    "media_type": {"type": "string"},
                    "byte_size": {"type": ["integer", "null"], "minimum": 0},
                    "sha256": {"type": "string"},
                    "content_sha256": {"type": "string"},
                    "metadata": {"type": "object"},
                },
                "additionalProperties": False,
            },
        },
        "tags": {"type": "array", "items": {"type": "string"}},
        "collection_refs": {"type": "array", "items": {"type": "string"}},
        "content_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "index_profile_id": {"type": "string"},
    },
}


class ContextAssetsPlugin:
    plugin_id = "capability-context-assets"
    version = "1.4.2"

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id=self.plugin_id,
            version=self.version,
            name="Context Assets",
            description="Scoped knowledge retrieval, durable memory reading, and URL ingestion.",
            distribution_name="joyhousebot-capability-context-assets",
            build_digest=source_tree_digest(__file__),
            runtime_contract_version=2,
            required_permissions=(
                "context.read",
                "memory.read",
                "network.http.read",
                "knowledge.write",
            ),
            dependencies=(
                {"id": "runtime-context-services", "kind": "service", "required": True},
                {"id": "postgresql", "kind": "database", "required": True},
            ),
        )

    def register(self, registry: Any) -> None:
        registry.register_capability(_retrieve_definition(self.version), RetrieveHandler())
        registry.register_capability(_memory_get_definition(self.version), MemoryGetHandler())
        registry.register_capability(
            _fetch_to_knowledge_definition(self.version),
            FetchUrlToKnowledgebaseHandler(),
        )
        registry.register_capability(
            _index_knowledge_definition(self.version), IndexKnowledgeHandler()
        )

    def health_checks(self) -> tuple[Any, ...]:
        return ()


def _retrieve_definition(version: str) -> CapabilityDefinition:
    return CapabilityDefinition(
        ref=CapabilityRef("retrieve", version, CapabilityKind.TOOL),
        name="Retrieve context",
        description="Search durable user-scoped knowledge or Agent memory.",
        input_schema=RETRIEVE_SCHEMA,
        output_schema={"type": "object"},
        adapter="plugin",
        tags=("context", "knowledge", "memory"),
        expected_duration_seconds=2,
        timeout_seconds=30,
        idempotent=True,
        retryable=True,
        side_effect="read",
        permissions=("context.read",),
        data_classification="confidential",
    )


def _index_knowledge_definition(version: str) -> CapabilityDefinition:
    return CapabilityDefinition(
        ref=CapabilityRef("knowledge.index", version, CapabilityKind.TOOL),
        name="Index knowledge source",
        description="Parse one immutable source snapshot and activate a versioned Knowledge index.",
        input_schema=INDEX_KNOWLEDGE_SCHEMA,
        output_schema={"type": "object"},
        adapter="plugin",
        tags=("knowledge", "ingestion", "index"),
        expected_duration_seconds=10,
        timeout_seconds=600,
        idempotent=True,
        retryable=True,
        side_effect="internal",
        permissions=("knowledge.write",),
        data_classification="confidential",
        invocation_concurrency="sequential",
        max_concurrent_invocations=1,
    )


def _memory_get_definition(version: str) -> CapabilityDefinition:
    return CapabilityDefinition(
        ref=CapabilityRef("memory_get", version, CapabilityKind.TOOL),
        name="Read memory",
        description="Read a document from the current Run's durable memory scope.",
        input_schema=MEMORY_GET_SCHEMA,
        output_schema={"type": "object"},
        adapter="plugin",
        tags=("context", "memory"),
        expected_duration_seconds=1,
        timeout_seconds=10,
        idempotent=True,
        retryable=False,
        side_effect="read",
        permissions=("memory.read",),
        data_classification="confidential",
    )


def _fetch_to_knowledge_definition(version: str) -> CapabilityDefinition:
    return CapabilityDefinition(
        ref=CapabilityRef("fetch_url_to_knowledgebase", version, CapabilityKind.TOOL),
        name="Index URL in knowledge",
        description="Fetch a public URL and index readable content in user-scoped knowledge.",
        input_schema=FETCH_TO_KNOWLEDGE_SCHEMA,
        output_schema={"type": "object"},
        adapter="plugin",
        tags=("context", "knowledge", "ingestion"),
        expected_duration_seconds=10,
        timeout_seconds=60,
        idempotent=True,
        retryable=True,
        side_effect="write",
        permissions=("network.http.read", "knowledge.write"),
        data_classification="confidential",
    )


def create_plugin() -> ContextAssetsPlugin:
    return ContextAssetsPlugin()
