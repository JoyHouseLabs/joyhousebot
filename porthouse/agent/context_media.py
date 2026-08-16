"""Media admission descriptors for model context."""

from __future__ import annotations

import mimetypes
from hashlib import sha256
from pathlib import Path
from typing import Any

from porthouse.agent.context_manifest import source_entry


def media_sources(media: list[str] | None) -> list[dict[str, Any]]:
    """Describe admitted images without persisting paths or bytes."""
    sources: list[dict[str, Any]] = []
    for index, path in enumerate(media or []):
        item = Path(path)
        mime, _ = mimetypes.guess_type(path)
        included = bool(item.is_file() and mime and mime.startswith("image/"))
        fingerprint = (
            {
                "sha256": sha256(item.read_bytes()).hexdigest(),
                "size": item.stat().st_size,
                "mime": mime,
            }
            if included
            else {"mime": mime or "unknown", "accepted": False}
        )
        sources.append(
            source_entry(
                source_kind="media_attachment",
                source_id=f"media:{index}",
                content=fingerprint,
                classification="confidential",
                authority="user",
                freshness="request",
                priority=95,
                included=included,
                included_reason="supported_image_attachment" if included else None,
                excluded_reason="missing_or_unsupported_media" if not included else None,
                metadata={"mime": mime or "unknown"},
            )
        )
    return sources
