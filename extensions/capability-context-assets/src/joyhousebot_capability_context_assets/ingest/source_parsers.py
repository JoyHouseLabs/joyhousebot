"""Extensible, bounded parsers for immutable Knowledge source snapshots."""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Any, Protocol
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree

from joyhousebot.extension_sdk.network import (
    DEFAULT_MAX_BYTES,
    SsrfProtectedTransport,
    TrackedAsyncClient,
    fetch_url_bytes,
    sanitize_error_message,
)

from .chunking import chunk_text
from .docx_parser import parse_docx
from .models import Chunk
from .url_ingest import MAX_REDIRECTS, USER_AGENT, fetch_and_ingest_url

MAX_UNCOMPRESSED_OFFICE_BYTES = 50 * 1024 * 1024
MAX_PARSED_CHARS = 500_000
MAX_DOCUMENT_PAGES = 2_000


class SourceParseError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        parser_id: str = "unresolved",
        parser_version: str = "1",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.parser_id = parser_id
        self.parser_version = parser_version


@dataclass(frozen=True)
class ParseCandidate:
    reference_kind: str
    uri: str = ""
    display_name: str = ""
    media_type: str = ""
    content: str = ""
    asset_id: str = ""
    binary_body: bytes = b""

    @property
    def extension(self) -> str:
        path = unquote(urlparse(self.uri).path) or self.display_name
        return PurePosixPath(path).suffix.lower()


@dataclass
class ParsedCandidate:
    parser_id: str
    parser_version: str
    chunks: list[Chunk]
    trace: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedSnapshot:
    parser_id: str
    parser_version: str
    chunks: list[dict[str, Any]]
    trace: dict[str, Any]


class CandidateParser(Protocol):
    parser_id: str
    parser_version: str
    priority: int

    def supports(self, candidate: ParseCandidate) -> bool: ...

    async def parse(self, candidate: ParseCandidate) -> ParsedCandidate: ...


class SourceParserRegistry:
    """Resolve one parser per source part; new extensions can register more parsers."""

    def __init__(self) -> None:
        self._parsers: list[CandidateParser] = []

    def register(self, parser: CandidateParser) -> None:
        if any(item.parser_id == parser.parser_id for item in self._parsers):
            raise ValueError(f"duplicate source parser: {parser.parser_id}")
        self._parsers.append(parser)
        self._parsers.sort(key=lambda item: item.priority, reverse=True)

    def resolve(self, candidate: ParseCandidate) -> CandidateParser:
        for parser in self._parsers:
            if parser.supports(candidate):
                return parser
        label = candidate.media_type or candidate.extension or candidate.reference_kind
        raise SourceParseError("PARSER_UNAVAILABLE", f"no installed parser supports {label}")

    async def parse_snapshot(
        self,
        value: dict[str, Any],
        *,
        input_asset_loader: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    ) -> ParsedSnapshot:
        candidates = _snapshot_candidates(value)
        if not candidates:
            source_type = str(value.get("source_type") or "unknown")
            raise SourceParseError(
                "PARSER_UNAVAILABLE", f"no installed parser can index source_type={source_type}"
            )
        chunks: list[dict[str, Any]] = []
        traces: list[dict[str, Any]] = []
        parser_versions: list[str] = []
        parsed_chars = 0
        for candidate in candidates:
            if candidate.reference_kind == "runtime_input":
                if input_asset_loader is None:
                    raise SourceParseError(
                        "REFERENCE_RESOLVER_UNAVAILABLE",
                        "runtime_input references require the Runtime input asset port",
                    )
                try:
                    resolved = await input_asset_loader(candidate.asset_id)
                except PermissionError as exc:
                    raise SourceParseError("REFERENCE_ACCESS_DENIED", str(exc)) from exc
                except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
                    raise SourceParseError(
                        "REFERENCE_READ_FAILED",
                        sanitize_error_message(str(exc)),
                        retryable=isinstance(exc, (FileNotFoundError, OSError, RuntimeError)),
                    ) from exc
                candidate = replace(
                    candidate,
                    binary_body=bytes(resolved["body"]),
                    display_name=(
                        candidate.display_name or str(resolved.get("display_name") or "")
                    ),
                    media_type=(candidate.media_type or str(resolved.get("media_type") or "")),
                )
            elif candidate.reference_kind not in {"inline", "url"}:
                raise SourceParseError(
                    "REFERENCE_RESOLVER_UNAVAILABLE",
                    f"{candidate.reference_kind} references require an installed vault resolver",
                )
            parser = self.resolve(candidate)
            try:
                parsed = await parser.parse(candidate)
            except SourceParseError as exc:
                if exc.parser_id == "unresolved":
                    exc.parser_id = parser.parser_id
                    exc.parser_version = parser.parser_version
                raise
            except Exception as exc:
                raise SourceParseError("PARSER_FAILED", sanitize_error_message(str(exc))) from exc
            if not parsed.chunks:
                raise SourceParseError(
                    "EMPTY_DOCUMENT", f"{parser.parser_id} produced no searchable text"
                )
            part_chars = sum(len(chunk.text) for chunk in parsed.chunks)
            parsed_chars += part_chars
            if parsed_chars > MAX_PARSED_CHARS:
                raise SourceParseError(
                    "PARSED_CONTENT_LIMIT",
                    f"combined searchable text exceeds {MAX_PARSED_CHARS} characters",
                    parser_id=parsed.parser_id,
                    parser_version=parsed.parser_version,
                )
            parser_versions.append(f"{parsed.parser_id}@{parsed.parser_version}")
            prefix = candidate.display_name.strip()
            for chunk in parsed.chunks:
                section_path = list(chunk.meta.get("section_path") or [])
                if prefix:
                    section_path.insert(0, prefix)
                chunks.append(
                    {
                        "text": chunk.text,
                        "page": chunk.page,
                        "char_start": chunk.start_offset,
                        "char_end": chunk.end_offset,
                        "section_path": section_path,
                        "block_type": str(chunk.meta.get("block_type") or "text"),
                    }
                )
            traces.append(
                {
                    "reference_kind": candidate.reference_kind,
                    "uri": candidate.uri if candidate.reference_kind == "url" else "",
                    "asset_id": candidate.asset_id,
                    "display_name": candidate.display_name,
                    "parser_id": parsed.parser_id,
                    **parsed.trace,
                }
            )
        identities = list(dict.fromkeys(parser_versions))
        if len(identities) == 1:
            parser_id, parser_version = identities[0].split("@", 1)
        else:
            parser_id = "composite:" + "+".join(item.split("@", 1)[0] for item in identities)
            parser_version = "+".join(identities)
        return ParsedSnapshot(
            parser_id=parser_id,
            parser_version=parser_version,
            chunks=chunks,
            trace={"parts": traces, "chunk_count": len(chunks)},
        )


class InlineTextParser:
    parser_id = "plain-text"
    parser_version = "1"
    priority = 100

    def supports(self, candidate: ParseCandidate) -> bool:
        return candidate.reference_kind == "inline" and bool(candidate.content.strip())

    async def parse(self, candidate: ParseCandidate) -> ParsedCandidate:
        content = candidate.content[:MAX_PARSED_CHARS]
        return ParsedCandidate(
            self.parser_id,
            self.parser_version,
            chunk_text(content, chunk_size=1200, overlap=200),
            {"content_length": len(content)},
        )


class PdfParser:
    parser_id = "pdf-pypdf"
    parser_version = "1"
    priority = 90

    def supports(self, candidate: ParseCandidate) -> bool:
        return candidate.reference_kind in {"url", "runtime_input"} and (
            candidate.extension == ".pdf" or candidate.media_type.lower() == "application/pdf"
        )

    async def parse(self, candidate: ParseCandidate) -> ParsedCandidate:
        body, content_type = await _candidate_binary(
            candidate,
            ("application/pdf", "application/octet-stream"),
        )
        if not body.startswith(b"%PDF-"):
            raise SourceParseError("INVALID_DOCUMENT", "PDF signature is missing")
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise SourceParseError(
                "PARSER_DEPENDENCY_MISSING", "PDF parser dependency pypdf is not installed"
            ) from exc
        try:
            reader = PdfReader(io.BytesIO(body), strict=False)
            if len(reader.pages) > MAX_DOCUMENT_PAGES:
                raise SourceParseError(
                    "DOCUMENT_PAGE_LIMIT",
                    f"PDF contains more than {MAX_DOCUMENT_PAGES} pages",
                )
            chunks: list[Chunk] = []
            parsed_chars = 0
            for page_number, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                parsed_chars += len(text)
                if parsed_chars > MAX_PARSED_CHARS:
                    raise SourceParseError(
                        "PARSED_CONTENT_LIMIT",
                        f"PDF searchable text exceeds {MAX_PARSED_CHARS} characters",
                    )
                chunks.extend(chunk_text(text, chunk_size=1200, overlap=200, page=page_number))
        except SourceParseError:
            raise
        except Exception as exc:
            raise SourceParseError(
                "PARSER_FAILED", f"PDF parse failed: {sanitize_error_message(str(exc))}"
            ) from exc
        return ParsedCandidate(
            self.parser_id,
            self.parser_version,
            chunks,
            {"content_type": content_type, "byte_size": len(body), "page_count": len(reader.pages)},
        )


class OfficeOpenXmlParser:
    parser_id = "office-openxml"
    parser_version = "2"
    priority = 80
    _extensions = {".docx", ".pptx", ".xlsx"}
    _media_types = {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }

    def supports(self, candidate: ParseCandidate) -> bool:
        return candidate.reference_kind in {"url", "runtime_input"} and (
            candidate.extension in self._extensions
            or candidate.media_type.lower() in self._media_types
        )

    async def parse(self, candidate: ParseCandidate) -> ParsedCandidate:
        body, content_type = await _candidate_binary(
            candidate,
            tuple(sorted(self._media_types | {"application/zip", "application/octet-stream"})),
        )
        if not zipfile.is_zipfile(io.BytesIO(body)):
            raise SourceParseError("INVALID_DOCUMENT", "Office Open XML ZIP signature is missing")
        extension = candidate.extension or _extension_from_media_type(candidate.media_type)
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                _validate_office_archive(archive)
                if extension == ".docx":
                    chunks = _parse_docx(archive)
                elif extension == ".pptx":
                    chunks = _parse_pptx(archive)
                elif extension == ".xlsx":
                    chunks = _parse_xlsx(archive)
                else:
                    raise SourceParseError(
                        "PARSER_UNAVAILABLE", "cannot infer Office Open XML document type"
                    )
        except SourceParseError:
            raise
        except (KeyError, OSError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            raise SourceParseError(
                "PARSER_FAILED", f"Office parse failed: {sanitize_error_message(str(exc))}"
            ) from exc
        return ParsedCandidate(
            self.parser_id,
            self.parser_version,
            chunks,
            {"content_type": content_type, "byte_size": len(body), "format": extension},
        )


class PublicTextFileParser:
    parser_id = "public-text-file"
    parser_version = "1"
    priority = 70
    _extensions = {".txt", ".md", ".markdown", ".csv", ".json", ".xml"}

    def supports(self, candidate: ParseCandidate) -> bool:
        media = candidate.media_type.lower()
        return candidate.reference_kind in {"url", "runtime_input"} and (
            candidate.extension in self._extensions
            or media.startswith("text/")
            or media in {"application/json", "application/xml"}
        )

    async def parse(self, candidate: ParseCandidate) -> ParsedCandidate:
        body, content_type = await _candidate_binary(
            candidate,
            ("text/", "application/json", "application/xml", "application/octet-stream"),
        )
        text = body.decode("utf-8", errors="replace")[:MAX_PARSED_CHARS]
        return ParsedCandidate(
            self.parser_id,
            self.parser_version,
            chunk_text(text, chunk_size=1200, overlap=200),
            {"content_type": content_type, "byte_size": len(body)},
        )


class PublicTextUrlParser:
    parser_id = "web-readability"
    parser_version = "1"
    priority = 10

    def supports(self, candidate: ParseCandidate) -> bool:
        media = candidate.media_type.lower()
        binary_extensions = {
            ".pdf",
            ".docx",
            ".doc",
            ".pptx",
            ".ppt",
            ".xlsx",
            ".xls",
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".webp",
            ".mp3",
            ".wav",
            ".mp4",
            ".mov",
        }
        return (
            candidate.reference_kind == "url"
            and candidate.extension not in binary_extensions
            and not media.startswith(("image/", "video/", "audio/"))
        )

    async def parse(self, candidate: ParseCandidate) -> ParsedCandidate:
        try:
            document = await fetch_and_ingest_url(candidate.uri, max_chars=MAX_PARSED_CHARS)
        except ValueError as exc:
            raise SourceParseError(
                "FETCH_FAILED", sanitize_error_message(str(exc)), retryable=True
            ) from exc
        return ParsedCandidate(
            self.parser_id,
            self.parser_version,
            document.chunks,
            document.trace,
        )


def default_source_parser_registry() -> SourceParserRegistry:
    registry = SourceParserRegistry()
    registry.register(InlineTextParser())
    registry.register(PdfParser())
    registry.register(OfficeOpenXmlParser())
    registry.register(PublicTextFileParser())
    registry.register(PublicTextUrlParser())
    return registry


def _snapshot_candidates(value: dict[str, Any]) -> list[ParseCandidate]:
    candidates: list[ParseCandidate] = []
    content = str(value.get("content") or "")
    if content.strip():
        candidates.append(ParseCandidate("inline", display_name="正文", content=content))
    elif str(value.get("source_url") or "").strip():
        candidates.append(
            ParseCandidate(
                "url",
                uri=str(value["source_url"]).strip(),
                display_name=str(value.get("title") or ""),
            )
        )
    seen = {candidate.uri for candidate in candidates if candidate.uri}
    for item in list(value.get("attachments") or []):
        uri = str(item.get("uri") or "").strip()
        asset_id = str(item.get("asset_id") or "").strip()
        identity = uri or asset_id
        if not identity or identity in seen:
            continue
        seen.add(identity)
        candidates.append(
            ParseCandidate(
                str(item.get("reference_kind") or "url"),
                uri=uri,
                display_name=str(item.get("display_name") or ""),
                media_type=str(item.get("media_type") or ""),
                asset_id=asset_id,
            )
        )
    return candidates


async def _fetch_binary(url: str, allowed_content_types: tuple[str, ...]) -> tuple[str, bytes, str]:
    async with TrackedAsyncClient(
        propagate_headers=False,
        transport=SsrfProtectedTransport(),
        follow_redirects=False,
        timeout=30.0,
    ) as client:
        try:
            response, body = await fetch_url_bytes(
                client,
                url,
                headers={"User-Agent": USER_AGENT},
                max_redirects=MAX_REDIRECTS,
                max_bytes=DEFAULT_MAX_BYTES,
                allowed_content_types=allowed_content_types,
            )
        except ValueError as exc:
            raise SourceParseError(
                "FETCH_FAILED", sanitize_error_message(str(exc)), retryable=True
            ) from exc
        except Exception as exc:
            raise SourceParseError(
                "FETCH_FAILED", sanitize_error_message(str(exc)), retryable=True
            ) from exc
    return str(response.url), body, response.headers.get("content-type", "")


async def _candidate_binary(
    candidate: ParseCandidate, allowed_content_types: tuple[str, ...]
) -> tuple[bytes, str]:
    if candidate.reference_kind == "runtime_input":
        if not candidate.binary_body:
            raise SourceParseError("EMPTY_DOCUMENT", "input asset is empty")
        return candidate.binary_body, candidate.media_type
    _, body, content_type = await _fetch_binary(candidate.uri, allowed_content_types)
    return body, content_type


def _validate_office_archive(archive: zipfile.ZipFile) -> None:
    total_size = sum(item.file_size for item in archive.infolist())
    if total_size > MAX_UNCOMPRESSED_OFFICE_BYTES:
        raise SourceParseError(
            "DOCUMENT_EXPANSION_LIMIT",
            f"Office document expands beyond {MAX_UNCOMPRESSED_OFFICE_BYTES} bytes",
        )
    if any(
        item.filename.startswith(("/", "../")) or "/../" in item.filename
        for item in archive.infolist()
    ):
        raise SourceParseError("INVALID_DOCUMENT", "Office archive contains unsafe paths")


def _xml_root(data: bytes) -> ElementTree.Element:
    upper = data[:4096].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise SourceParseError("INVALID_DOCUMENT", "Office XML declarations are not allowed")
    return ElementTree.fromstring(data)


def _xml_texts(data: bytes, tags: set[str]) -> list[str]:
    root = _xml_root(data)
    values: list[str] = []
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1]
        if local_name in tags and element.text:
            text = element.text.strip()
            if text:
                values.append(text)
    return values


def _parse_docx(archive: zipfile.ZipFile) -> list[Chunk]:
    document_root = _xml_root(archive.read("word/document.xml"))
    styles_root = (
        _xml_root(archive.read("word/styles.xml"))
        if "word/styles.xml" in archive.namelist()
        else None
    )
    return parse_docx(document_root, styles_root)


def _numbered_name(value: str) -> tuple[int, str]:
    match = re.search(r"(\d+)(?=\.xml$)", value)
    return (int(match.group(1)) if match else 0, value)


def _parse_pptx(archive: zipfile.ZipFile) -> list[Chunk]:
    chunks: list[Chunk] = []
    names = sorted(
        (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
        key=_numbered_name,
    )
    for page_number, name in enumerate(names, start=1):
        text = "\n".join(_xml_texts(archive.read(name), {"t"}))
        chunks.extend(chunk_text(text, chunk_size=1200, overlap=200, page=page_number))
    return chunks


def _parse_xlsx(archive: zipfile.ZipFile) -> list[Chunk]:
    shared: list[str] = []
    if "xl/sharedStrings.xml" in archive.namelist():
        root = _xml_root(archive.read("xl/sharedStrings.xml"))
        for item in root:
            values = [
                element.text or ""
                for element in item.iter()
                if element.tag.rsplit("}", 1)[-1] == "t"
            ]
            shared.append("".join(values))
    chunks: list[Chunk] = []
    names = sorted(
        (name for name in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)),
        key=_numbered_name,
    )
    for page_number, name in enumerate(names, start=1):
        root = _xml_root(archive.read(name))
        rows: list[str] = []
        for row in (item for item in root.iter() if item.tag.rsplit("}", 1)[-1] == "row"):
            values: list[str] = []
            for cell in (item for item in row if item.tag.rsplit("}", 1)[-1] == "c"):
                cell_type = cell.attrib.get("t", "")
                raw = next(
                    (
                        item.text or ""
                        for item in cell.iter()
                        if item.tag.rsplit("}", 1)[-1] in {"v", "t"}
                    ),
                    "",
                )
                if cell_type == "s" and raw.isdigit() and int(raw) < len(shared):
                    raw = shared[int(raw)]
                values.append(raw)
            if any(values):
                rows.append("\t".join(values))
        text = "\n".join(rows)[:MAX_PARSED_CHARS]
        chunks.extend(chunk_text(text, chunk_size=1200, overlap=200, page=page_number))
    return chunks


def _extension_from_media_type(media_type: str) -> str:
    lowered = media_type.lower()
    if "wordprocessingml" in lowered:
        return ".docx"
    if "presentationml" in lowered:
        return ".pptx"
    if "spreadsheetml" in lowered:
        return ".xlsx"
    return ""


DEFAULT_SOURCE_PARSERS = default_source_parser_registry()


__all__ = [
    "DEFAULT_SOURCE_PARSERS",
    "ParseCandidate",
    "ParsedSnapshot",
    "SourceParseError",
    "SourceParserRegistry",
    "default_source_parser_registry",
]
