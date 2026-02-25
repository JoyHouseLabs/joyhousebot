"""Watch knowledge source dir and enqueue new/updated files."""

import threading
from pathlib import Path
from typing import Any

from loguru import logger

from joyhousebot.services.knowledge_pipeline.converter import SUPPORTED_EXTENSIONS
from joyhousebot.services.knowledge_pipeline.queue import KnowledgePipelineQueue


def start_watcher(
    workspace: Path,
    source_dir: Path,
    processed_dir: Path,
    pipeline_queue: KnowledgePipelineQueue,
    *,
    config: Any = None,
) -> threading.Thread | None:
    """
    Start a background thread that watches source_dir and puts new/changed files into pipeline_queue.
    Returns the watcher thread, or None if watch is disabled or source_dir does not exist.
    """
    watch_enabled = True
    if config and getattr(getattr(config, "tools", None), "knowledge_pipeline", None):
        watch_enabled = getattr(config.tools.knowledge_pipeline, "watch_enabled", True)
    if not watch_enabled:
        return None
    source_dir = Path(source_dir).resolve()
    if not source_dir.exists():
        source_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created knowledge source dir: {source_dir}")

    def _watch_loop() -> None:
        try:
            from watchfiles import watch
        except ImportError:
            logger.warning("watchfiles not installed; knowledge dir watch disabled")
            return
        seen: set[tuple[str, float]] = set()
        for changes in watch(source_dir):
            for change, path_str in changes:
                path = Path(path_str)
                if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                try:
                    key = (str(path), path.stat().st_mtime)
                    if key in seen:
                        continue
                    seen.add(key)
                    pipeline_queue.put(path)
                except OSError:
                    pass

    thread = threading.Thread(target=_watch_loop, daemon=True)
    thread.start()
    logger.debug(f"Knowledge source watcher started for {source_dir}")
    return thread
