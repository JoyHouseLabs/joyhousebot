"""Compatibility imports for the shared document-processing primitives."""

from porthouse_capability_document_processing.ingest.chunking import (
    chunk_text,
    normalize_whitespace,
)

__all__ = ["chunk_text", "normalize_whitespace"]
