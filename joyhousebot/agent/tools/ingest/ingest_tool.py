"""Unified ingest tool: PDF, URL, image -> workspace/knowledge with traceable summary."""

import json
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from joyhousebot.agent.tools.base import Tool

from joyhousebot.agent.tools.ingest.image_ocr import extract_image_text
from joyhousebot.agent.tools.ingest.models import IngestDoc
from joyhousebot.agent.tools.ingest.pdf_parser import extract_pdf
from joyhousebot.agent.tools.ingest.url_ingest import fetch_and_ingest_url
from joyhousebot.agent.tools.ingest.youtube_ingest import fetch_youtube


class IngestTool(Tool):
    """Import a document (PDF, URL, image, or YouTube) into the knowledge workspace. Returns summary with evidence and source trace. Processing can be local or cloud per source type (see config tools.ingest)."""

    def __init__(self, workspace: Path, transcribe_provider: Any = None, config: Any = None):
        self.workspace = Path(workspace)
        self.transcribe_provider = transcribe_provider
        self.config = config
        self.knowledge_dir = self.workspace / "knowledge"
        self.sources_dir = self.knowledge_dir / "sources"
        self.documents_dir = self.knowledge_dir / "documents"

    def _ingest_config(self) -> Any:
        if self.config and getattr(self.config, "tools", None):
            return getattr(self.config.tools, "ingest", None)
        return None

    @property
    def name(self) -> str:
        return "ingest"

    @property
    def description(self) -> str:
        return (
            "Import a document into the knowledge base for later search and decision support. "
            "Supports: pdf (local path), url (http(s) link), image (local path, OCR if available), youtube (video URL; captions first, then audio transcription if configured). "
            "Stores normalized chunks and metadata under workspace/knowledge/. "
            "Returns summary, key points, evidence snippets, and source trace."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "source_type": {
                    "type": "string",
                    "enum": ["pdf", "url", "image", "youtube"],
                    "description": "Type of source: pdf, url, image, or youtube",
                },
                "source_input": {
                    "type": "string",
                    "description": "PDF or image file path (relative to workspace or absolute), or URL for url type",
                },
                "title_override": {
                    "type": "string",
                    "description": "Optional title to use instead of auto-detected",
                },
            },
            "required": ["source_type", "source_input"],
        }

    async def execute(
        self,
        source_type: str,
        source_input: str,
        title_override: str | None = None,
        **kwargs: Any,
    ) -> str:
        source_input = (source_input or "").strip()
        if not source_input:
            return json.dumps({"error": "source_input is required"})

        cfg = self._ingest_config()
        pdf_mode = getattr(cfg, "pdf_processing", "local") if cfg else "local"
        image_mode = getattr(cfg, "image_processing", "auto") if cfg else "auto"
        youtube_mode = getattr(cfg, "youtube_processing", "auto") if cfg else "auto"
        cloud_ocr_provider = getattr(cfg, "cloud_ocr_provider", "") or ""
        cloud_ocr_api_key = getattr(cfg, "cloud_ocr_api_key", "") or ""
        if not cloud_ocr_api_key and self.config and getattr(self.config, "providers", None) and cloud_ocr_provider == "openai_vision":
            openai_cfg = getattr(self.config.providers, "openai", None)
            if openai_cfg:
                cloud_ocr_api_key = getattr(openai_cfg, "api_key", "") or ""

        try:
            if source_type == "pdf":
                path = self.workspace / source_input if not Path(source_input).is_absolute() else Path(source_input)
                doc = extract_pdf(path, processing=pdf_mode)
            elif source_type == "url":
                doc = await fetch_and_ingest_url(source_input)
            elif source_type == "image":
                path = self.workspace / source_input if not Path(source_input).is_absolute() else Path(source_input)
                doc = extract_image_text(path, processing=image_mode, cloud_ocr_provider=cloud_ocr_provider, cloud_ocr_api_key=cloud_ocr_api_key)
            elif source_type == "youtube":
                doc = await fetch_youtube(source_input, transcribe_provider=self.transcribe_provider, youtube_processing=youtube_mode)
            else:
                return json.dumps({"error": f"Unsupported source_type: {source_type}"})
        except FileNotFoundError as e:
            return json.dumps({"error": f"File not found: {e}"})
        except ValueError as e:
            return json.dumps({"error": str(e)})
        except Exception as e:
            return json.dumps({"error": f"Ingest failed: {e}"})

        if title_override:
            doc.title = title_override

        doc_id = str(uuid.uuid4())[:8]
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        self.sources_dir.mkdir(parents=True, exist_ok=True)
        self.documents_dir.mkdir(parents=True, exist_ok=True)

        meta_path = self.sources_dir / f"{doc_id}.json"
        meta_path.write_text(json.dumps(doc.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

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

        doc_path = self.documents_dir / f"{doc_id}.md"
        doc_path.write_text("\n".join(body_lines), encoding="utf-8")

        chunks_payload = [{"text": c.text, "page": c.page} for c in doc.chunks]
        try:
            from joyhousebot.services.retrieval import RetrievalStore
            store = RetrievalStore(self.workspace)
            store.index_doc(
                doc_id=doc_id,
                source_type=doc.source_type,
                source_url=doc.source_url,
                title=doc.title,
                file_path=doc.file_path,
                chunks=chunks_payload,
            )
        except Exception:  # retrieval optional at ingest time
            pass

        # Optional: index to vector store when vector layer is enabled
        if self.config:
            try:
                from joyhousebot.services.retrieval.vector_optional import (
                    get_embedding_provider,
                    get_vector_store,
                    should_enable_vector,
                )
                if should_enable_vector(self.workspace, self.config) and get_embedding_provider(self.config) and get_vector_store(self.workspace, self.config):
                    provider = get_embedding_provider(self.config)
                    vs = get_vector_store(self.workspace, self.config)
                    texts = [c.get("text", "") for c in chunks_payload]
                    vectors = await provider.aembed(texts)
                    for i, vec in enumerate(vectors):
                        if i < len(chunks_payload):
                            c = chunks_payload[i]
                            vs.index(
                                doc_id=doc_id,
                                chunk_index=i,
                                vector=vec,
                                meta={
                                    "doc_id": doc_id,
                                    "chunk_index": i,
                                    "source_type": doc.source_type,
                                    "source_url": doc.source_url or "",
                                    "file_path": doc.file_path or "",
                                    "title": doc.title,
                                    "page": c.get("page"),
                                    "content": c.get("text", ""),
                                },
                            )
            except Exception as e:  # do not block ingest on vector index failure
                logger.warning(f"Vector index during ingest failed: {e}")

        summary = _build_traceable_summary(doc, doc_id, str(meta_path), str(doc_path))
        return json.dumps(summary, ensure_ascii=False, indent=2)

    def to_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _build_traceable_summary(doc: IngestDoc, doc_id: str, meta_path: str, doc_path: str) -> dict[str, Any]:
    """Build summary with evidence snippets and source trace."""
    evidence = [c.text[:500] + ("..." if len(c.text) > 500 else "") for c in doc.chunks[:5]]
    return {
        "ok": True,
        "doc_id": doc_id,
        "title": doc.title,
        "source_type": doc.source_type,
        "source_url": doc.source_url or doc.file_path,
        "chunk_count": len(doc.chunks),
        "summary": f"Imported {doc.title} ({len(doc.chunks)} chunks).",
        "evidence_snippets": evidence,
        "trace": {
            **doc.trace,
            "meta_path": meta_path,
            "doc_path": doc_path,
        },
    }
