"""Contracts for sandboxed private document extraction."""

from __future__ import annotations

import json

import pytest
from porthouse_capability_document_processing import plugin as documents

from porthouse.capabilities import CapabilityPluginRegistry
from porthouse.capabilities.services.sandbox import SandboxPort
from porthouse.capabilities.services.scratch import ScratchPort
from porthouse.extension_sdk import CapabilityContext
from porthouse.extension_sdk.sandbox import is_sandbox_available


class _FakeContextPort:
    def __init__(self, source: dict | None = None, error: Exception | None = None) -> None:
        self.source = source or {
            "body": b"%PDF-private",
            "asset_id": "input-a",
            "display_name": "resume.pdf",
            "media_type": "application/pdf",
            "content_sha256": "a" * 64,
            "byte_size": 12,
        }
        self.error = error
        self.calls: list[dict] = []

    async def read_input_asset(self, context, **kwargs):  # noqa: ANN001
        self.calls.append({"user_id": context.user_id, "run_id": context.run_id, **kwargs})
        if self.error:
            raise self.error
        return self.source


class _FakeSandboxPort:
    def __init__(self, result: dict | None = None) -> None:
        parsed = {
            "ok": True,
            "parser_id": "pdf-pypdf",
            "parser_version": "1",
            "chunks": [
                {
                    "text": "Private candidate evidence",
                    "page": 1,
                    "char_start": 0,
                    "char_end": 26,
                    "section_path": ["resume.pdf"],
                    "block_type": "text",
                }
            ],
            "trace": {"chunk_count": 1},
        }
        self.result = result or {
            "success": True,
            "output": "(no output)",
            "exit_code": 0,
            "files": {"result.json": json.dumps(parsed).encode()},
        }
        self.calls: list[dict] = []

    async def execute_job(self, context, **kwargs):  # noqa: ANN001
        self.calls.append({"user_id": context.user_id, **kwargs})
        return self.result


class _FakeServices:
    def __init__(self, context=None, sandbox=None) -> None:  # noqa: ANN001
        self.context = context or _FakeContextPort()
        self.sandbox = sandbox or _FakeSandboxPort()


def _context(
    services: object,
    *,
    configuration: dict | None = None,
) -> CapabilityContext:
    return CapabilityContext(
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        root_run_id="root-a",
        agent_id="agent-a",
        services=services,
        metadata={
            "permissions": ["context.read", "document.extract"],
            "capability_configuration": configuration
            or {"isolation_backend": "container", "timeout_seconds": 45},
        },
    )


def _pdf_with_text(text: str) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects.append(
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
    )
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, value in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode() + value + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(output)


def test_document_processing_registers_one_restricted_subprocess_capability() -> None:
    registry = CapabilityPluginRegistry()
    registry.register_plugin(documents.DocumentProcessingPlugin())
    definition, _handler = registry.get("document.extract", "1.1.0")

    assert definition.side_effect == "read"
    assert definition.idempotent is True
    assert definition.data_classification == "restricted"
    assert definition.permissions == ("context.read", "document.extract")
    assert definition.ref.plugin_id == "capability-document-processing"
    assert registry.manifests()[0].execution_isolation == "subprocess"
    assert registry.manifests()[0].to_extension_manifest().execution_isolation == "subprocess"
    assert documents.CONFIGURATION_SCHEMA["properties"]["isolation_backend"]["default"] == (
        "subprocess"
    )


@pytest.mark.asyncio
async def test_extract_reads_only_run_bound_asset_and_returns_private_artifact() -> None:
    services = _FakeServices()
    result = await documents.DocumentExtractHandler().execute(
        _context(services),
        {"asset_id": "input-a", "max_pages": 100},
    )

    assert result.success is True
    assert result.output == {
        "artifact_id": result.artifacts[0].artifact_id,
        "parser_id": "pdf-pypdf",
        "parser_version": "1",
        "chunk_count": 1,
        "content_sha256": result.artifacts[0].content_sha256,
    }
    assert "Private candidate evidence" not in json.dumps(result.output)
    assert result.artifacts[0].artifact_type == "document.extracted_text"
    assert result.artifacts[0].data["chunks"][0]["page"] == 1
    assert services.context.calls[0]["asset_id"] == "input-a"
    call = services.sandbox.calls[0]
    assert call["configuration"]["container_network"] == "none"
    assert call["configuration"]["timeout"] == 45
    assert call["input_files"]["source.bin"] == b"%PDF-private"
    assert call["output_files"] == ("result.json",)


@pytest.mark.asyncio
async def test_extract_fails_before_sandbox_for_unbound_asset() -> None:
    services = _FakeServices(context=_FakeContextPort(error=PermissionError("not bound")))
    result = await documents.DocumentExtractHandler().execute(
        _context(services), {"asset_id": "input-foreign"}
    )

    assert result.success is False
    assert result.error["code"] == "REFERENCE_ACCESS_DENIED"
    assert services.sandbox.calls == []


@pytest.mark.asyncio
async def test_extract_rejects_unsupported_media_before_sandbox() -> None:
    services = _FakeServices(
        context=_FakeContextPort(
            source={
                "body": b"image",
                "asset_id": "input-image",
                "display_name": "resume.png",
                "media_type": "image/png",
                "content_sha256": "b" * 64,
                "byte_size": 5,
            }
        )
    )
    result = await documents.DocumentExtractHandler().execute(
        _context(services), {"asset_id": "input-image"}
    )

    assert result.success is False
    assert result.error["code"] == "UNSUPPORTED_MEDIA_TYPE"
    assert services.sandbox.calls == []


@pytest.mark.asyncio
async def test_extract_fails_closed_when_sandbox_is_unavailable() -> None:
    sandbox = _FakeSandboxPort(
        {
            "success": False,
            "code": "SANDBOX_UNAVAILABLE",
            "message": "execution sandbox is unavailable",
            "retryable": True,
        }
    )
    result = await documents.DocumentExtractHandler().execute(
        _context(_FakeServices(sandbox=sandbox)), {"asset_id": "input-a"}
    )

    assert result.success is False
    assert result.error == {
        "code": "SANDBOX_UNAVAILABLE",
        "message": "execution sandbox is unavailable",
        "retryable": True,
    }


@pytest.mark.asyncio
async def test_extract_propagates_closed_parser_failure_without_artifact() -> None:
    parser_failure = {
        "ok": False,
        "error": {
            "code": "ENCRYPTED_DOCUMENT",
            "message": "encrypted PDFs require manual processing",
            "retryable": False,
        },
    }
    sandbox = _FakeSandboxPort(
        {
            "success": True,
            "output": "(no output)",
            "exit_code": 0,
            "files": {"result.json": json.dumps(parser_failure).encode()},
        }
    )
    result = await documents.DocumentExtractHandler().execute(
        _context(_FakeServices(sandbox=sandbox)), {"asset_id": "input-a"}
    )

    assert result.success is False
    assert result.error["code"] == "ENCRYPTED_DOCUMENT"
    assert result.artifacts == []


@pytest.mark.asyncio
async def test_extract_defaults_to_injected_subprocess_without_sandbox() -> None:
    calls: list[dict] = []

    async def fake_runner(**kwargs):  # noqa: ANN003, ANN202
        calls.append(kwargs)
        parsed = {
            "ok": True,
            "parser_id": "pdf-pypdf",
            "parser_version": "1",
            "chunks": [{"text": "subprocess evidence", "page": 1}],
            "trace": {},
        }
        return {"success": True, "files": {"result.json": json.dumps(parsed).encode()}}

    class Services:
        context = _FakeContextPort()

    result = await documents.DocumentExtractHandler(subprocess_runner=fake_runner).execute(
        _context(Services(), configuration={"timeout_seconds": 30, "memory_limit_mb": 384}),
        {"asset_id": "input-a"},
    )

    assert result.success is True
    assert calls[0]["body"] == b"%PDF-private"
    assert calls[0]["timeout_seconds"] == 30
    assert calls[0]["memory_mb"] == 384


@pytest.mark.asyncio
async def test_extract_smoke_runs_real_parser_in_bounded_subprocess() -> None:
    body = _pdf_with_text("Subprocess evidence")
    services = _FakeServices(
        context=_FakeContextPort(
            source={
                "body": body,
                "asset_id": "input-subprocess",
                "display_name": "resume.pdf",
                "media_type": "application/pdf",
                "content_sha256": "d" * 64,
                "byte_size": len(body),
            }
        )
    )
    result = await documents.DocumentExtractHandler().execute(
        _context(services, configuration={"timeout_seconds": 30, "memory_limit_mb": 512}),
        {"asset_id": "input-subprocess"},
    )

    assert result.success is True
    assert result.output["parser_id"] == "pdf-pypdf"
    assert result.artifacts[0].data["chunks"][0]["text"] == "Subprocess evidence"


@pytest.mark.asyncio
async def test_extract_smoke_runs_real_parser_in_network_disabled_container(tmp_path) -> None:
    if not await is_sandbox_available():
        pytest.skip("Docker sandbox is unavailable")
    body = _pdf_with_text("Container evidence")
    context_port = _FakeContextPort(
        source={
            "body": body,
            "asset_id": "input-container",
            "display_name": "resume.pdf",
            "media_type": "application/pdf",
            "content_sha256": "c" * 64,
            "byte_size": len(body),
        }
    )
    services = _FakeServices(
        context=context_port,
        sandbox=SandboxPort(ScratchPort(tmp_path)),
    )

    result = await documents.DocumentExtractHandler().execute(
        _context(services), {"asset_id": "input-container"}
    )

    assert result.success is True
    assert result.output["parser_id"] == "pdf-pypdf"
    assert result.artifacts[0].data["chunks"][0]["text"] == "Container evidence"
