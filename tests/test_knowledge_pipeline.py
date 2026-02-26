"""Tests for knowledge pipeline: converter, queue, indexer. Uses tmp_path; no real embedding."""

import json
import time
from pathlib import Path

import pytest

from joyhousebot.services.knowledge_pipeline.converter import (
    SUPPORTED_EXTENSIONS,
    convert_file_to_processed,
)
from joyhousebot.services.knowledge_pipeline.indexer import (
    index_processed_file,
    sync_processed_dir_to_store,
)
from joyhousebot.services.knowledge_pipeline.pipeline_queue import KnowledgePipelineQueue
from joyhousebot.services.knowledge_pipeline.watcher import start_watcher
from joyhousebot.services.retrieval.store import RetrievalStore


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "knowledge").mkdir(exist_ok=True)
    return tmp_path


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    d = tmp_path / "source"
    d.mkdir()
    return d


@pytest.fixture
def processed_dir(tmp_path: Path) -> Path:
    d = tmp_path / "processed"
    d.mkdir()
    return d


def test_convert_file_to_processed_md(workspace: Path, source_dir: Path, processed_dir: Path) -> None:
    """Converter: single .md file produces doc_id, .md and .json in processed_dir."""
    md_file = source_dir / "note.md"
    md_file.write_text("# Note\n\nHello world.", encoding="utf-8")
    doc_id, md_path = convert_file_to_processed(md_file, processed_dir, source_dir)
    assert isinstance(doc_id, str)
    assert len(doc_id) == 12
    assert md_path.suffix == ".md"
    assert md_path.exists()
    assert "Hello world" in md_path.read_text()
    meta_path = md_path.with_suffix(".json")
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["doc_id"] == doc_id
    assert meta["title"] == "note"
    assert meta["source_type"] == "text"


def test_convert_file_to_processed_txt(workspace: Path, source_dir: Path, processed_dir: Path) -> None:
    """Converter: single .txt file produces valid processed output."""
    txt_file = source_dir / "readme.txt"
    txt_file.write_text("Plain text content.", encoding="utf-8")
    doc_id, md_path = convert_file_to_processed(txt_file, processed_dir, source_dir)
    assert md_path.exists()
    assert "Plain text content" in md_path.read_text()
    meta_path = md_path.with_suffix(".json")
    assert meta_path.exists()
    assert json.loads(meta_path.read_text(encoding="utf-8"))["doc_id"] == doc_id


def test_convert_file_to_processed_unsupported_raises(source_dir: Path, processed_dir: Path) -> None:
    """Converter: unsupported extension raises ValueError."""
    bad_file = source_dir / "file.xyz"
    bad_file.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported extension"):
        convert_file_to_processed(bad_file, processed_dir, source_dir)


def test_supported_extensions() -> None:
    """Supported extensions include expected types."""
    assert ".md" in SUPPORTED_EXTENSIONS
    assert ".txt" in SUPPORTED_EXTENSIONS
    assert ".pdf" in SUPPORTED_EXTENSIONS


def test_queue_put_enqueues_and_worker_processes(
    workspace: Path, source_dir: Path, processed_dir: Path
) -> None:
    """Queue: put() enqueues path; worker converts and indexes (processed dir + store updated)."""
    md_file = source_dir / "queued.md"
    md_file.write_text("# Queued\n\nContent for queue test.", encoding="utf-8")
    q = KnowledgePipelineQueue(workspace, source_dir, processed_dir)
    q.start()
    try:
        q.put(md_file)
        # Allow worker to process (convert + index)
        for _ in range(20):
            time.sleep(0.25)
            if list(processed_dir.glob("*.md")):
                break
        processed_mds = list(processed_dir.glob("*.md"))
        assert len(processed_mds) >= 1
        assert "Content for queue test" in processed_mds[0].read_text()
    finally:
        q.stop()


def test_index_processed_file_writes_to_store(workspace: Path, processed_dir: Path) -> None:
    """Indexer: index_processed_file indexes .md+.json into RetrievalStore and is searchable."""
    doc_id = "abc123"
    meta_path = processed_dir / f"{doc_id}.json"
    meta_path.write_text(
        json.dumps(
            {
                "doc_id": doc_id,
                "source_type": "text",
                "source_url": "",
                "file_path": "",
                "title": "Test Doc",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    md_path = processed_dir / f"{doc_id}.md"
    md_path.write_text("# Test Doc\n\nUnique phrase pineapple search.", encoding="utf-8")
    index_processed_file(workspace, md_path, chunk_size=500, chunk_overlap=50)
    store = RetrievalStore(workspace)
    hits = store.search(query="pineapple", top_k=5)
    assert len(hits) >= 1
    assert any("pineapple" in (h.get("content") or "") for h in hits)


def test_sync_processed_dir_to_store_count(workspace: Path, processed_dir: Path) -> None:
    """Indexer: sync_processed_dir_to_store returns count of indexed files."""
    for i in range(2):
        doc_id = f"doc{i}"
        (processed_dir / f"{doc_id}.json").write_text(
            json.dumps({"doc_id": doc_id, "source_type": "text", "source_url": "", "file_path": "", "title": f"Doc {i}"}),
            encoding="utf-8",
        )
        (processed_dir / f"{doc_id}.md").write_text(f"# Doc {i}\n\nBody.", encoding="utf-8")
    n = sync_processed_dir_to_store(workspace, processed_dir, chunk_size=500, chunk_overlap=50)
    assert n == 2


def test_start_watcher_returns_thread_when_enabled(
    workspace: Path, source_dir: Path, processed_dir: Path
) -> None:
    """Watcher: start_watcher returns a thread when watch is enabled and queue is provided."""
    q = KnowledgePipelineQueue(workspace, source_dir, processed_dir)
    thread = start_watcher(workspace, source_dir, processed_dir, q, config=None)
    assert thread is not None
    assert thread.is_alive()


def test_start_watcher_returns_none_when_disabled(
    workspace: Path, source_dir: Path, processed_dir: Path
) -> None:
    """Watcher: start_watcher returns None when watch_enabled is False."""
    class Config:
        class tools:
            knowledge_pipeline = type("kp", (), {"watch_enabled": False})()
    q = KnowledgePipelineQueue(workspace, source_dir, processed_dir)
    thread = start_watcher(workspace, source_dir, processed_dir, q, config=Config())
    assert thread is None
