"""Knowledge pipeline: knowledgebase dir -> convert to markdown -> processed -> FTS5 index."""

from joyhousebot.services.knowledge_pipeline.converter import convert_file_to_processed
from joyhousebot.services.knowledge_pipeline.indexer import index_processed_file, sync_processed_dir_to_store
from joyhousebot.services.knowledge_pipeline.queue import KnowledgePipelineQueue
from joyhousebot.services.knowledge_pipeline.watcher import start_watcher

__all__ = [
    "convert_file_to_processed",
    "index_processed_file",
    "sync_processed_dir_to_store",
    "KnowledgePipelineQueue",
    "start_watcher",
]
