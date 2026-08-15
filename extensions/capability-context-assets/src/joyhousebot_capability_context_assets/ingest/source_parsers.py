"""Compatibility imports for the shared parser registry."""

from joyhousebot_capability_document_processing.ingest.source_parsers import (
    DEFAULT_SOURCE_PARSERS,
    ParseCandidate,
    ParsedSnapshot,
    SourceParseError,
    SourceParserRegistry,
    default_source_parser_registry,
)

__all__ = [
    "DEFAULT_SOURCE_PARSERS",
    "ParseCandidate",
    "ParsedSnapshot",
    "SourceParseError",
    "SourceParserRegistry",
    "default_source_parser_registry",
]
