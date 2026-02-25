"""Ingest tools for information decision center: PDF, URL, image -> knowledge base.

This module is primarily used by the internal knowledge pipeline (convert + index).
The canonical entry for user-facing knowledge is the knowledgebase directory plus
the processed output; the pipeline consumes source files and writes to processed/index.
"""

from joyhousebot.agent.tools.ingest.ingest_tool import IngestTool

__all__ = ["IngestTool"]
