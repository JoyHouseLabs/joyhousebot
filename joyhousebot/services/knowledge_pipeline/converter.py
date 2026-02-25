"""Convert a single file from knowledge source dir to markdown + metadata in processed dir."""

import hashlib
import json
from pathlib import Path
from typing import Any

from loguru import logger

from joyhousebot.agent.tools.ingest.chunking import chunk_text
from joyhousebot.agent.tools.ingest.image_ocr import extract_image_text
from joyhousebot.agent.tools.ingest.models import Chunk, IngestDoc
from joyhousebot.agent.tools.ingest.pdf_parser import extract_pdf
from joyhousebot.utils.helpers import ensure_dir


# Supported extensions for conversion (local only in pipeline; URL content should be dumped to file first)
SUPPORTED_EXTENSIONS = {".pdf", ".md", ".txt", ".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _doc_id_for_path(relative_path: str) -> str:
    """Stable doc_id from relative path (same file -> same id for re-index)."""
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:12]


def _file_to_ingest_doc(
    path: Path,
    *,
    pdf_processing: str = "local",
    image_processing: str = "auto",
    cloud_ocr_provider: str = "",
    cloud_ocr_api_key: str = "",
) -> IngestDoc:
    """Dispatch by extension to existing ingest parsers. Returns IngestDoc."""
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(str(path))
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(path, processing=pdf_processing)
    if suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        return extract_image_text(
            path,
            processing=image_processing,
            cloud_ocr_provider=cloud_ocr_provider,
            cloud_ocr_api_key=cloud_ocr_api_key,
        )
    if suffix in (".md", ".txt"):
        text = path.read_text(encoding="utf-8", errors="replace")
        chunks_list = chunk_text(text, chunk_size=1200, overlap=200, page=None) if text.strip() else []
        if not chunks_list:
            chunks_list = [Chunk(text=text or "(empty)", start_offset=0, end_offset=len(text), page=None, meta={})]
        return IngestDoc(
            source_type="text",
            file_path=str(path),
            title=path.stem,
            chunks=chunks_list,
            trace={"format": suffix},
        )
    raise ValueError(f"Unsupported extension for conversion: {suffix}")


def convert_file_to_processed(
    source_path: Path,
    processed_dir: Path,
    relative_to: Path,
    *,
    ingest_config: Any = None,
) -> tuple[str, Path]:
    """
    Convert a single file into markdown + metadata under processed_dir.
    Returns (doc_id, path_to_md).
    Uses stable doc_id from relative path so re-run overwrites same doc.
    """
    processed_dir = Path(processed_dir).resolve()
    source_path = Path(source_path).resolve()
    try:
        rel = source_path.relative_to(relative_to)
    except ValueError:
        rel = source_path.name
    rel_str = str(rel).replace("\\", "/")
    doc_id = _doc_id_for_path(rel_str)

    pdf_mode = "local"
    image_mode = "auto"
    cloud_ocr = ""
    cloud_key = ""
    if ingest_config:
        pdf_mode = getattr(ingest_config, "pdf_processing", "local") or "local"
        image_mode = getattr(ingest_config, "image_processing", "auto") or "auto"
        cloud_ocr = getattr(ingest_config, "cloud_ocr_provider", "") or ""
        cloud_key = getattr(ingest_config, "cloud_ocr_api_key", "") or ""

    doc = _file_to_ingest_doc(
        source_path,
        pdf_processing=pdf_mode,
        image_processing=image_mode,
        cloud_ocr_provider=cloud_ocr,
        cloud_ocr_api_key=cloud_key,
    )

    ensure_dir(processed_dir)
    meta_path = processed_dir / f"{doc_id}.json"
    md_path = processed_dir / f"{doc_id}.md"

    meta = {
        "doc_id": doc_id,
        "source_type": doc.source_type,
        "source_url": doc.source_url,
        "file_path": doc.file_path,
        "title": doc.title,
        "author": getattr(doc, "author", "") or "",
        "chunk_count": len(doc.chunks),
        "source_relative": rel_str,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    body_lines = [f"# {doc.title}", ""]
    if doc.author:
        body_lines.append(f"**Author:** {doc.author}")
    if doc.source_url:
        body_lines.append(f"**Source:** {doc.source_url}")
    if doc.file_path:
        body_lines.append(f"**File:** {doc.file_path}")
    body_lines.append("")
    for i, c in enumerate(doc.chunks, 1):
        body_lines.append(f"## Chunk {i}" + (f" (page {c.page})" if c.page else ""))
        body_lines.append("")
        body_lines.append(c.text)
        body_lines.append("")
    md_path.write_text("\n".join(body_lines), encoding="utf-8")
    logger.debug(f"Converted {source_path} -> {md_path} (doc_id={doc_id})")
    return doc_id, md_path
