"""Helpers for audio transcription HTTP endpoint."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


async def transcribe_upload_file(
    *,
    file: Any,
    transcription_provider: Any,
    timestamp: int,
    temp_dir: str = "/tmp",
) -> str:
    """Persist upload to temp file, run transcription, and cleanup."""
    temp_path = Path(temp_dir) / f"joyhousebot_audio_{timestamp}_{file.filename}"
    try:
        with open(temp_path, "wb") as f:
            content = await file.read()
            f.write(content)
        return await transcription_provider.transcribe(temp_path)
    finally:
        if temp_path.exists():
            os.unlink(temp_path)

