"""Compatibility imports for shared URL ingestion."""

from porthouse_capability_document_processing.ingest.url_ingest import (
    MAX_REDIRECTS,
    USER_AGENT,
    fetch_and_ingest_url,
)

__all__ = ["MAX_REDIRECTS", "USER_AGENT", "fetch_and_ingest_url"]
