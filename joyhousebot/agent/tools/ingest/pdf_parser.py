"""PDF text extraction with page mapping. Local only; cloud reserved for future."""

from pathlib import Path
from typing import Any

from joyhousebot.agent.tools.ingest.chunking import chunk_text
from joyhousebot.agent.tools.ingest.models import Chunk, IngestDoc


def extract_pdf(path: str | Path, processing: str = "local", **kwargs: Any) -> IngestDoc:
    """Extract text from PDF with per-page mapping. processing: local | cloud | auto (cloud not implemented yet)."""
    path = Path(path).resolve()
    if processing == "cloud":
        return IngestDoc(
            source_type="pdf",
            file_path=str(path),
            title=path.stem,
            chunks=[Chunk(text="[Cloud PDF processing not implemented; use processing: local or auto.]", start_offset=0, end_offset=0, page=None, meta={})],
            trace={"error": "cloud_not_implemented"},
        )
    if not path.exists():
        raise FileNotFoundError(str(path))

    from pypdf import PdfReader

    reader = PdfReader(str(path))
    meta = reader.metadata
    title = (meta.title or path.stem) if meta else path.stem
    author = (meta.get("/Author") or "") if meta else ""
    all_chunks: list[Chunk] = []
    trace: list[dict] = []

    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        text = text.strip()
        if not text:
            continue
        trace.append({"page": i, "char_count": len(text)})
        for c in chunk_text(text, chunk_size=1200, overlap=200, page=i):
            all_chunks.append(c)

    return IngestDoc(
        source_type="pdf",
        file_path=str(path),
        title=title,
        author=author,
        chunks=all_chunks,
        trace={"pages": len(reader.pages), "page_stats": trace},
    )
