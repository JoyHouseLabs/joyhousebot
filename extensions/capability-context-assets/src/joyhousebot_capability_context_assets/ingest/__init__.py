"""URL ingestion primitives owned by the context-assets extension."""

from .chunking import chunk_text, normalize_whitespace
from .models import Chunk, IngestDoc
from .url_ingest import fetch_and_ingest_url

__all__ = [
    "Chunk",
    "IngestDoc",
    "chunk_text",
    "fetch_and_ingest_url",
    "normalize_whitespace",
]
