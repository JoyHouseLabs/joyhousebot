"""Unified ingest domain model for information decision center."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Chunk:
    """A single chunk of content with trace to source."""

    text: str
    start_offset: int
    end_offset: int
    page: int | None = None  # PDF page 1-based; None for non-PDF
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestDoc:
    """Normalized document after ingest: source metadata + chunks."""

    source_type: str  # pdf, url, image
    source_url: str = ""
    file_path: str = ""
    title: str = ""
    author: str = ""
    date: str = ""
    language: str = ""
    chunks: list[Chunk] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_url": self.source_url,
            "file_path": self.file_path,
            "title": self.title,
            "author": self.author,
            "date": self.date,
            "language": self.language,
            "chunk_count": len(self.chunks),
            "chunks": [
                {
                    "text": c.text,
                    "start_offset": c.start_offset,
                    "end_offset": c.end_offset,
                    "page": c.page,
                    "meta": c.meta,
                }
                for c in self.chunks
            ],
            "trace": self.trace,
        }
