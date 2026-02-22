"""Normalize and chunk text with overlap and metadata."""

import re

from joyhousebot.agent.tools.ingest.models import Chunk


def normalize_whitespace(text: str) -> str:
    """Normalize spaces and newlines."""
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def chunk_text(
    text: str,
    chunk_size: int = 1200,
    overlap: int = 200,
    page: int | None = None,
) -> list[Chunk]:
    """Split text into chunks with overlap. Offsets are character-based."""
    text = normalize_whitespace(text)
    if not text:
        return []
    step = max(1, chunk_size - overlap)
    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        # Prefer breaking at paragraph or sentence
        if end < len(text):
            for sep in ("\n\n", "\n", ". ", " "):
                last = text.rfind(sep, start, end + 1)
                if last >= start:
                    end = last + len(sep)
                    break
        chunk_text_slice = text[start:end].strip()
        if chunk_text_slice:
            chunks.append(
                Chunk(
                    text=chunk_text_slice,
                    start_offset=start,
                    end_offset=end,
                    page=page,
                    meta={},
                )
            )
        start = end if end > start else start + chunk_size
    return chunks
