"""Normalize and chunk text with overlap and source offsets."""

import re

from .models import Chunk


def normalize_whitespace(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def chunk_text(
    text: str,
    chunk_size: int = 1200,
    overlap: int = 200,
    page: int | None = None,
) -> list[Chunk]:
    text = normalize_whitespace(text)
    if not text:
        return []
    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            for separator in ("\n\n", "\n", ". ", " "):
                last = text.rfind(separator, start, end + 1)
                if last >= start:
                    end = last + len(separator)
                    break
        value = text[start:end].strip()
        if value:
            chunks.append(
                Chunk(
                    text=value,
                    start_offset=start,
                    end_offset=end,
                    page=page,
                )
            )
        if end >= len(text):
            break
        start = max(start + 1, end - max(0, overlap))
    return chunks
