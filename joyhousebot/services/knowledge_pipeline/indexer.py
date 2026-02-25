"""Index processed markdown files into FTS5 retrieval store and optional vector store."""

import json
import urllib.request
from pathlib import Path
from typing import Any

from loguru import logger

from joyhousebot.agent.tools.ingest.chunking import chunk_text
from joyhousebot.services.retrieval.store import RetrievalStore


def index_processed_file(
    workspace: Path,
    processed_md_path: Path,
    *,
    chunk_size: int = 1200,
    chunk_overlap: int = 200,
    config: Any = None,
) -> None:
    """
    Index a single processed .md file (and its .json meta) into RetrievalStore.
    When vector is enabled (config + vector_backend + embedding), also index into Chroma (or configured vector store).
    Replaces any existing chunks for the same doc_id in FTS5; vector store upserts by chunk id.
    """
    processed_md_path = Path(processed_md_path).resolve()
    if not processed_md_path.suffix == ".md" or not processed_md_path.exists():
        return
    meta_path = processed_md_path.with_suffix(".json")
    if not meta_path.exists():
        logger.warning(f"No metadata {meta_path} for {processed_md_path}, skipping index")
        return
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    doc_id = meta.get("doc_id") or processed_md_path.stem
    source_type = meta.get("source_type", "text")
    source_url = meta.get("source_url", "")
    file_path = meta.get("file_path", str(processed_md_path))
    title = meta.get("title", processed_md_path.stem)

    text = processed_md_path.read_text(encoding="utf-8", errors="replace")
    chunks_list = chunk_text(text, chunk_size=chunk_size, overlap=chunk_overlap, page=None)
    if not chunks_list:
        chunks_list = [{"text": text or "(empty)", "page": None}]
    else:
        chunks_list = [{"text": c.text, "page": c.page} for c in chunks_list]

    store = RetrievalStore(Path(workspace))
    store.delete_by_doc_id(doc_id)
    store.index_doc(
        doc_id=doc_id,
        source_type=source_type,
        source_url=source_url,
        title=title,
        file_path=file_path,
        chunks=chunks_list,
    )
    logger.debug(f"Indexed {processed_md_path} -> doc_id={doc_id} ({len(chunks_list)} chunks)")

    # Optional: index into vector store (Chroma etc.) when enabled
    if config is None:
        try:
            from joyhousebot.config.access import get_config
            config = get_config()
        except Exception:
            config = None
    if config is not None:
        try:
            from joyhousebot.services.retrieval.vector_optional import (
                get_embedding_provider,
                get_vector_store,
                should_enable_vector,
            )
            if should_enable_vector(workspace, config) and get_embedding_provider(config) and get_vector_store(workspace, config):
                provider = get_embedding_provider(config)
                vs = get_vector_store(workspace, config)
                if provider and vs:
                    texts = [c.get("text", "") for c in chunks_list]
                    vectors = provider.embed(texts)
                    for i, vec in enumerate(vectors):
                        if i < len(chunks_list):
                            c = chunks_list[i]
                            vs.index(
                                doc_id=doc_id,
                                chunk_index=i,
                                vector=vec,
                                meta={
                                    "doc_id": doc_id,
                                    "chunk_index": i,
                                    "source_type": source_type,
                                    "source_url": source_url or "",
                                    "file_path": file_path or "",
                                    "title": title,
                                    "page": c.get("page"),
                                    "content": c.get("text", ""),
                                },
                            )
                    logger.debug(f"Vector indexed {processed_md_path} -> doc_id={doc_id} ({len(vectors)} chunks)")
        except Exception as e:
            logger.warning(f"Vector index during pipeline index failed: {e}")

    # Optional: sync to QMD index when knowledge_qmd_sync_enabled and URL set
    if config is not None:
        try:
            retrieval = getattr(getattr(config, "tools", None), "retrieval", None)
            if retrieval and getattr(retrieval, "knowledge_qmd_sync_enabled", False):
                url = (getattr(retrieval, "knowledge_qmd_sync_url", "") or "").strip()
                if url:
                    payload = json.dumps({
                        "doc_id": doc_id,
                        "title": title,
                        "source_type": source_type,
                        "source_url": source_url,
                        "file_path": file_path,
                        "chunks": [{"text": c.get("text", ""), "page": c.get("page")} for c in chunks_list],
                    }, ensure_ascii=False).encode("utf-8")
                    req = urllib.request.Request(
                        url,
                        data=payload,
                        method="POST",
                        headers={"Content-Type": "application/json; charset=utf-8"},
                    )
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        if resp.status in (200, 201, 204):
                            logger.debug(f"QMD sync ok for {doc_id} -> {url}")
                        else:
                            logger.warning(f"QMD sync returned {resp.status} for {doc_id}")
                else:
                    logger.debug("knowledge_qmd_sync_enabled but knowledge_qmd_sync_url empty, skip sync")
        except Exception as e:
            logger.warning(f"QMD sync during pipeline index failed: {e}")


def sync_processed_dir_to_store(
    workspace: Path,
    processed_dir: Path,
    *,
    chunk_size: int = 1200,
    chunk_overlap: int = 200,
    config: Any = None,
) -> int:
    """
    Scan processed_dir for all .md files and index each into RetrievalStore (and optional vector store).
    Returns number of files indexed.
    """
    processed_dir = Path(processed_dir).resolve()
    if not processed_dir.exists():
        return 0
    count = 0
    for md_path in sorted(processed_dir.glob("*.md")):
        try:
            index_processed_file(
                workspace,
                md_path,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                config=config,
            )
            count += 1
        except Exception as e:
            logger.warning(f"Failed to index {md_path}: {e}")
    return count
