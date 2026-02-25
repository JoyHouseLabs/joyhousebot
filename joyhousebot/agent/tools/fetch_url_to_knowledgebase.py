"""Tool: fetch a URL and save its content into workspace/knowledgebase for pipeline processing."""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from joyhousebot.agent.tools.base import Tool
from joyhousebot.agent.tools.ingest.url_ingest import fetch_and_ingest_url
from joyhousebot.utils.helpers import ensure_dir


def _safe_filename(url: str, title: str) -> str:
    """Produce a safe .md filename from URL and title."""
    slug = re.sub(r"[^\w\s-]", "", (title or url)[:60]).strip()
    slug = re.sub(r"[-\s]+", "_", slug).strip("_") or "page"
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    return f"url_{h}_{slug}.md"[:120]


class FetchUrlToKnowledgebaseTool(Tool):
    """Fetch a URL and save its extracted content into workspace/knowledgebase. The knowledge pipeline will then convert and index it. Use this to add web pages to the knowledge base."""

    def __init__(self, workspace: Path, config: Any = None):
        self.workspace = Path(workspace)
        self.config = config

    def _knowledge_source_dir(self) -> Path:
        if self.config and getattr(getattr(self.config, "tools", None), "knowledge_pipeline", None):
            d = getattr(self.config.tools.knowledge_pipeline, "knowledge_source_dir", "knowledgebase")
            return self.workspace / d
        return self.workspace / "knowledgebase"

    @property
    def name(self) -> str:
        return "fetch_url_to_knowledgebase"

    @property
    def description(self) -> str:
        return (
            "Fetch a URL and save its readable content into the knowledge source dir (knowledgebase). "
            "The pipeline will then convert and index it so you can search with retrieve(scope='knowledge'). "
            "Use for adding web pages or articles to the knowledge base."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "HTTP(S) URL to fetch"},
            },
            "required": ["url"],
        }

    async def execute(self, url: str, **kwargs: Any) -> str:
        url = (url or "").strip()
        if not url:
            return json.dumps({"error": "url is required"})
        try:
            doc = await fetch_and_ingest_url(url)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        except Exception as e:
            return json.dumps({"error": f"Fetch failed: {e}"})
        target_dir = self._knowledge_source_dir()
        ensure_dir(target_dir)
        filename = _safe_filename(doc.source_url, doc.title)
        md_path = target_dir / filename
        lines = [f"# {doc.title}", "", f"**Source:** {doc.source_url}", ""]
        for c in doc.chunks:
            lines.append(c.text)
            lines.append("")
        md_path.write_text("\n".join(lines), encoding="utf-8")
        return json.dumps({
            "ok": True,
            "url": doc.source_url,
            "title": doc.title,
            "path": str(md_path),
            "message": "Content saved to knowledgebase; pipeline will index it shortly.",
        }, ensure_ascii=False, indent=2)
