"""Parser registry and bounded Office/PDF ingestion contracts."""

from __future__ import annotations

import io
import zipfile

import pytest
from joyhousebot_capability_context_assets.ingest import source_parsers


def _archive(files: dict[str, str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return output.getvalue()


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
    objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
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


@pytest.mark.asyncio
async def test_registry_combines_inline_text_and_docx_attachment(monkeypatch) -> None:
    body = _archive(
        {
            "[Content_Types].xml": "<Types/>",
            "word/document.xml": (
                '<w:document xmlns:w="urn:word"><w:body>'
                "<w:p><w:r><w:t>Document evidence</w:t></w:r></w:p>"
                "</w:body></w:document>"
            ),
        }
    )

    async def fake_fetch(url, allowed_content_types):  # noqa: ANN001
        assert url == "https://files.example/evidence.docx"
        assert "application/zip" in allowed_content_types
        return url, body, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    monkeypatch.setattr(source_parsers, "_fetch_binary", fake_fetch)
    parsed = await source_parsers.default_source_parser_registry().parse_snapshot(
        {
            "source_type": "file",
            "title": "Combined source",
            "content": "Inline observation",
            "attachments": [
                {
                    "reference_kind": "url",
                    "uri": "https://files.example/evidence.docx",
                    "display_name": "evidence.docx",
                    "media_type": (
                        "application/vnd.openxmlformats-officedocument."
                        "wordprocessingml.document"
                    ),
                }
            ],
        }
    )
    assert parsed.parser_id == "composite:plain-text+office-openxml"
    assert [item["text"] for item in parsed.chunks] == [
        "Inline observation",
        "Document evidence",
    ]
    assert parsed.chunks[1]["section_path"] == ["evidence.docx"]


@pytest.mark.asyncio
async def test_pdf_parser_extracts_page_text(monkeypatch) -> None:
    body = _pdf_with_text("PDF evidence")

    async def fake_fetch(url, allowed):  # noqa: ANN001
        assert allowed[0] == "application/pdf"
        return url, body, "application/pdf"

    monkeypatch.setattr(source_parsers, "_fetch_binary", fake_fetch)
    parsed = await source_parsers.default_source_parser_registry().parse_snapshot(
        {
            "source_type": "file",
            "attachments": [
                {
                    "reference_kind": "url",
                    "uri": "https://files.example/evidence.pdf",
                    "display_name": "evidence.pdf",
                    "media_type": "application/pdf",
                }
            ],
        }
    )
    assert parsed.parser_id == "pdf-pypdf"
    assert parsed.chunks[0]["text"] == "PDF evidence"
    assert parsed.chunks[0]["page"] == 1


@pytest.mark.asyncio
async def test_parser_normalizes_pdf_radical_glyphs_for_cjk_search() -> None:
    parsed = await source_parsers.default_source_parser_registry().parse_snapshot(
        {
            "source_type": "note",
            "content": "⼀个⼈的⼈⽣与成⻓，⻩⾦机会需要对⻬。",
        }
    )

    assert parsed.chunks[0]["text"] == "一个人的人生与成长,黄金机会需要对齐。"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("uri", "media_type", "files", "expected"),
    [
        (
            "https://files.example/deck.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            {
                "[Content_Types].xml": "<Types/>",
                "ppt/slides/slide1.xml": (
                    '<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><p:cSld>'
                    "<a:t>First slide</a:t></p:cSld></p:sld>"
                ),
            },
            "First slide",
        ),
        (
            "https://files.example/table.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            {
                "[Content_Types].xml": "<Types/>",
                "xl/sharedStrings.xml": (
                    '<sst xmlns="urn:x"><si><t>Customer</t></si><si><t>Follow up</t></si></sst>'
                ),
                "xl/worksheets/sheet1.xml": (
                    '<worksheet xmlns="urn:x"><sheetData><row>'
                    '<c t="s"><v>0</v></c><c t="s"><v>1</v></c>'
                    "</row></sheetData></worksheet>"
                ),
            },
            "Customer Follow up",
        ),
    ],
)
async def test_office_parser_extracts_presentation_and_sheet(
    monkeypatch, uri, media_type, files, expected  # noqa: ANN001
) -> None:
    body = _archive(files)

    async def fake_fetch(_url, _allowed):  # noqa: ANN001
        return uri, body, media_type

    monkeypatch.setattr(source_parsers, "_fetch_binary", fake_fetch)
    parsed = await source_parsers.default_source_parser_registry().parse_snapshot(
        {
            "source_type": "file",
            "title": "Office",
            "attachments": [
                {
                    "reference_kind": "url",
                    "uri": uri,
                    "display_name": uri.rsplit("/", 1)[-1],
                    "media_type": media_type,
                }
            ],
        }
    )
    assert parsed.parser_id == "office-openxml"
    assert parsed.chunks[0]["text"] == expected
    assert parsed.chunks[0]["page"] == 1


@pytest.mark.asyncio
async def test_office_parser_rejects_xml_entities(monkeypatch) -> None:
    body = _archive(
        {
            "[Content_Types].xml": "<Types/>",
            "word/document.xml": '<!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><x/>',
        }
    )

    async def fake_fetch(url, allowed):  # noqa: ANN001
        return url, body, allowed[0]

    monkeypatch.setattr(source_parsers, "_fetch_binary", fake_fetch)
    with pytest.raises(source_parsers.SourceParseError) as raised:
        await source_parsers.default_source_parser_registry().parse_snapshot(
            {
                "source_type": "file",
                "attachments": [
                    {
                        "reference_kind": "url",
                        "uri": "https://files.example/unsafe.docx",
                        "media_type": (
                            "application/vnd.openxmlformats-officedocument."
                            "wordprocessingml.document"
                        ),
                    }
                ],
            }
        )
    assert raised.value.code == "INVALID_DOCUMENT"


@pytest.mark.asyncio
async def test_registry_fails_closed_for_vault_and_media_without_parser() -> None:
    registry = source_parsers.default_source_parser_registry()
    with pytest.raises(source_parsers.SourceParseError) as vault_error:
        await registry.parse_snapshot(
            {
                "source_type": "file",
                "attachments": [
                    {
                        "reference_kind": "cloud_vault",
                        "uri": "joyhouse-cloud://vault/private.pdf",
                    }
                ],
            }
        )
    assert vault_error.value.code == "REFERENCE_RESOLVER_UNAVAILABLE"

    with pytest.raises(source_parsers.SourceParseError) as media_error:
        await registry.parse_snapshot(
            {
                "source_type": "image",
                "attachments": [
                    {
                        "reference_kind": "url",
                        "uri": "https://files.example/photo.png",
                        "media_type": "image/png",
                    }
                ],
            }
        )
    assert media_error.value.code == "PARSER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_registry_reads_runtime_input_through_scoped_loader() -> None:
    calls: list[str] = []

    async def load_input_asset(asset_id: str) -> dict[str, object]:
        calls.append(asset_id)
        return {
            "body": b"Durable private evidence",
            "display_name": "evidence.txt",
            "media_type": "text/plain",
        }

    parsed = await source_parsers.default_source_parser_registry().parse_snapshot(
        {
            "source_type": "file",
            "attachments": [
                {
                    "reference_kind": "runtime_input",
                    "asset_id": "input_" + "a" * 32,
                    "display_name": "evidence.txt",
                    "media_type": "text/plain",
                }
            ],
        },
        input_asset_loader=load_input_asset,
    )

    assert calls == ["input_" + "a" * 32]
    assert parsed.parser_id == "public-text-file"
    assert parsed.chunks[0]["text"] == "Durable private evidence"
    assert parsed.trace["parts"][0]["uri"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "media_type", "body", "expected"),
    [
        ("evidence.pdf", "application/pdf", _pdf_with_text("Private PDF"), "Private PDF"),
        (
            "evidence.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            _archive(
                {
                    "[Content_Types].xml": "<Types/>",
                    "word/document.xml": (
                        '<w:document xmlns:w="urn:word"><w:body>'
                        "<w:p><w:r><w:t>Private Word</w:t></w:r></w:p>"
                        "</w:body></w:document>"
                    ),
                }
            ),
            "Private Word",
        ),
        (
            "deck.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            _archive(
                {
                    "[Content_Types].xml": "<Types/>",
                    "ppt/slides/slide1.xml": (
                        '<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><p:cSld>'
                        "<a:t>Private Slide</a:t></p:cSld></p:sld>"
                    ),
                }
            ),
            "Private Slide",
        ),
        (
            "table.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            _archive(
                {
                    "[Content_Types].xml": "<Types/>",
                    "xl/sharedStrings.xml": '<sst xmlns="urn:x"><si><t>Private Cell</t></si></sst>',
                    "xl/worksheets/sheet1.xml": (
                        '<worksheet xmlns="urn:x"><sheetData><row>'
                        '<c t="s"><v>0</v></c></row></sheetData></worksheet>'
                    ),
                }
            ),
            "Private Cell",
        ),
    ],
)
async def test_runtime_input_parses_private_pdf_and_office(
    name: str, media_type: str, body: bytes, expected: str
) -> None:
    async def load_input_asset(_asset_id: str) -> dict[str, object]:
        return {"body": body, "display_name": name, "media_type": media_type}

    parsed = await source_parsers.default_source_parser_registry().parse_snapshot(
        {
            "source_type": "file",
            "attachments": [
                {
                    "reference_kind": "runtime_input",
                    "asset_id": "input_" + "b" * 32,
                    "display_name": name,
                    "media_type": media_type,
                }
            ],
        },
        input_asset_loader=load_input_asset,
    )
    assert parsed.chunks[0]["text"] == expected
    assert parsed.trace["parts"][0]["uri"] == ""
