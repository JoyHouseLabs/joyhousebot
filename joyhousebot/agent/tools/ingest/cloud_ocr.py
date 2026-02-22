"""Cloud OCR: optional vision API for image text extraction."""

import base64
from pathlib import Path
from typing import Any

from joyhousebot.agent.tools.ingest.chunking import chunk_text
from joyhousebot.agent.tools.ingest.models import Chunk, IngestDoc


def extract_image_text_cloud(
    path: str | Path,
    provider: str,
    api_key: str = "",
    **kwargs: Any,
) -> IngestDoc:
    """
    Extract text from image via cloud vision/OCR API.
    provider: openai_vision | (others reserved).
    """
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(str(path))

    text = ""
    trace: dict = {"ocr": "cloud", "provider": provider}

    if provider == "openai_vision":
        text, trace = _openai_vision_ocr(path, api_key, trace)
    else:
        text = f"[Cloud OCR provider '{provider}' not implemented or configured.]"
        trace["error"] = "unknown_provider"

    text = (text or "").strip()
    chunks_list = chunk_text(text, chunk_size=1200, overlap=200, page=None) if text else []
    if not chunks_list:
        chunks_list = [Chunk(text=text or "(no text extracted)", start_offset=0, end_offset=len(text), page=None, meta={})]

    return IngestDoc(
        source_type="image",
        file_path=str(path),
        title=path.stem,
        chunks=chunks_list,
        trace=trace,
    )


def _openai_vision_ocr(path: Path, api_key: str, trace: dict) -> tuple[str, dict]:
    """Use OpenAI Vision (e.g. gpt-4o) to extract text from image."""
    if not api_key:
        return "[Cloud OCR: openai_vision requires api_key.]", {**trace, "error": "no_api_key"}

    try:
        import httpx
        with open(path, "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode("ascii")
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract all text from this image. Preserve structure (paragraphs, list items). Output plain text only."},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ],
                }
            ],
            "max_tokens": 4096,
        }
        r = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60.0,
        )
        r.raise_for_status()
        data = r.json()
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        trace["model"] = "gpt-4o-mini"
        return text, trace
    except Exception as e:
        return f"[Cloud OCR error: {e}]", {**trace, "error": str(e)}
