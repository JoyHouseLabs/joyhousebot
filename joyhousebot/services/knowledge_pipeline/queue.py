"""Task queue for knowledge pipeline: consume paths, convert + index."""

import queue
import threading
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from joyhousebot.services.knowledge_pipeline.converter import (
    SUPPORTED_EXTENSIONS,
    convert_file_to_processed,
)
from joyhousebot.services.knowledge_pipeline.indexer import index_processed_file


class KnowledgePipelineQueue:
    """
    In-memory queue: put(source_file_path), worker thread converts to processed and indexes.
    """

    def __init__(
        self,
        workspace: Path,
        source_dir: Path,
        processed_dir: Path,
        *,
        ingest_config: Any = None,
        pipeline_config: Any = None,
        config: Any = None,
    ):
        self.workspace = Path(workspace)
        self.source_dir = Path(source_dir).resolve()
        self.processed_dir = Path(processed_dir).resolve()
        self._ingest_config = ingest_config
        self._pipeline_config = pipeline_config
        self._config = config
        self._q: queue.Queue[Path | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def _chunk_size(self) -> int:
        if self._pipeline_config:
            return getattr(self._pipeline_config, "convert_chunk_size", 1200)
        return 1200

    def _chunk_overlap(self) -> int:
        if self._pipeline_config:
            return getattr(self._pipeline_config, "convert_chunk_overlap", 200)
        return 200

    def put(self, source_path: Path) -> None:
        """Enqueue a source file for conversion and indexing."""
        p = Path(source_path).resolve()
        if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
            logger.debug(f"Pipeline skip unsupported extension: {p}")
            return
        try:
            p.relative_to(self.source_dir)
        except ValueError:
            logger.debug(f"Pipeline skip path outside source dir: {p}")
            return
        self._q.put(p)

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            try:
                doc_id, md_path = convert_file_to_processed(
                    item,
                    self.processed_dir,
                    self.source_dir,
                    ingest_config=self._ingest_config,
                )
                index_processed_file(
                    self.workspace,
                    md_path,
                    chunk_size=self._chunk_size(),
                    chunk_overlap=self._chunk_overlap(),
                    config=self._config,
                )
            except Exception as e:
                logger.warning(f"Pipeline task failed for {item}: {e}")
            finally:
                self._q.task_done()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        logger.debug("Knowledge pipeline queue worker started")

    def stop(self) -> None:
        self._stop.set()
        self._q.put(None)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
