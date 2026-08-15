"""Shared, bounded document parsing primitives."""

from .chunking import chunk_text, normalize_whitespace
from .models import Chunk, IngestDoc
from .parser_contracts import ParseCandidate
from .source_parsers import (
    DEFAULT_SOURCE_PARSERS,
    ParsedSnapshot,
    SourceParseError,
    SourceParserRegistry,
    default_source_parser_registry,
)
from .url_ingest import fetch_and_ingest_url

__all__ = [
    "Chunk",
    "DEFAULT_SOURCE_PARSERS",
    "IngestDoc",
    "ParseCandidate",
    "ParsedSnapshot",
    "SourceParseError",
    "SourceParserRegistry",
    "chunk_text",
    "default_source_parser_registry",
    "fetch_and_ingest_url",
    "normalize_whitespace",
]
