"""Normalized document model for optional knowledge ingestion."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Chunk:
    """A single content chunk with source offsets."""

    text: str
    start_offset: int
    end_offset: int
    page: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestDoc:
    """Normalized source metadata and chunks."""

    source_type: str
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
                    "text": item.text,
                    "start_offset": item.start_offset,
                    "end_offset": item.end_offset,
                    "page": item.page,
                    "meta": item.meta,
                }
                for item in self.chunks
            ],
            "trace": self.trace,
        }
