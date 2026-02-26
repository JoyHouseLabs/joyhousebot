"""Standalone entry point for knowledge pipeline worker subprocess."""

import os
import sys
from pathlib import Path
from typing import Any

from loguru import logger


def main() -> None:
    """Main entry point for the knowledge pipeline worker."""
    workspace = Path(os.environ.get("JOYHOUSEBOT_KNOWLEDGE_WORKSPACE", "."))
    source_dir = Path(os.environ.get("JOYHOUSEBOT_KNOWLEDGE_SOURCE_DIR", "knowledge"))
    processed_dir = Path(os.environ.get("JOYHOUSEBOT_KNOWLEDGE_PROCESSED_DIR", "knowledge/processed"))

    logger.info(f"Starting knowledge pipeline worker: source={source_dir}, processed={processed_dir}")

    from joyhousebot.services.knowledge_pipeline.converter import SUPPORTED_EXTENSIONS
    from joyhousebot.services.knowledge_pipeline.converter import convert_file_to_processed
    from joyhousebot.services.knowledge_pipeline.indexer import index_processed_file
    import queue
    import threading

    class PipelineWorker:
        """Simple worker that processes files from source_dir."""

        def __init__(self):
            self._q = queue.Queue()
            self._stop = threading.Event()
            self._thread = None

        def put(self, path: Path) -> None:
            p = Path(path).resolve()
            if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
                return
            try:
                p.relative_to(source_dir)
            except ValueError:
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
                    convert_file_to_processed(item, processed_dir, source_dir)
                    md_path = processed_dir / f"{item.stem}.md"
                    if md_path.exists():
                        index_processed_file(workspace, md_path)
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

        def stop(self) -> None:
            self._stop.set()
            self._q.put(None)
            if self._thread is not None:
                self._thread.join(timeout=5.0)

    worker = PipelineWorker()
    worker.start()

    def _start_file_watcher() -> threading.Thread | None:
        """Start file watcher thread if watchfiles is available."""
        try:
            from watchfiles import watch
        except ImportError:
            logger.warning("watchfiles not installed; file watch disabled")
            return None

        def _watch_loop() -> None:
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
                        worker.put(path)
                    except OSError:
                        pass

        thread = threading.Thread(target=_watch_loop, daemon=True)
        thread.start()
        logger.info(f"File watcher started for {source_dir}")
        return thread

    watcher_thread = _start_file_watcher()

    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down...")
    finally:
        worker.stop()
        logger.info("Knowledge pipeline worker stopped")


if __name__ == "__main__":
    main()
