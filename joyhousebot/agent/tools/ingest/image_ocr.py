"""Image OCR: extract text from image file. Local (pytesseract) or cloud (vision API) by config."""

from pathlib import Path
from typing import Any

from joyhousebot.agent.tools.ingest.chunking import chunk_text
from joyhousebot.agent.tools.ingest.models import Chunk, IngestDoc


def extract_image_text_local(path: str | Path) -> IngestDoc:
    """Extract text from image via local OCR (pytesseract). Returns IngestDoc."""
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(str(path))

    text = ""
    trace: dict = {"ocr": "none"}

    try:
        import pytesseract
        from PIL import Image

        img = Image.open(path)
        text = pytesseract.image_to_string(img)
        trace["ocr"] = "pytesseract"
    except ImportError:
        text = "[OCR unavailable: install pytesseract and Pillow, and system tesseract-ocr.]"
    except Exception as e:
        text = f"[OCR error: {e}]"
        trace["error"] = str(e)

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


def extract_image_text(
    path: str | Path,
    processing: str = "auto",
    cloud_ocr_provider: str = "",
    cloud_ocr_api_key: str = "",
    **kwargs: Any,
) -> IngestDoc:
    """
    Extract text from image. processing: local | cloud | auto.
    auto = try local first; if unavailable or empty, try cloud when configured.
    """
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(str(path))

    use_cloud = processing == "cloud" or (processing == "auto" and cloud_ocr_provider and cloud_ocr_api_key)
    use_local = processing == "local" or processing == "auto"

    if use_local and not use_cloud:
        return extract_image_text_local(path)

    if use_cloud and cloud_ocr_provider and cloud_ocr_api_key:
        from joyhousebot.agent.tools.ingest.cloud_ocr import extract_image_text_cloud
        return extract_image_text_cloud(path, provider=cloud_ocr_provider, api_key=cloud_ocr_api_key)

    # auto: try local first
    doc = extract_image_text_local(path)
    local_ok = doc.trace.get("ocr") == "pytesseract" and any(c.text and len(c.text.strip()) > 10 for c in doc.chunks)
    if local_ok:
        return doc
    if processing == "auto" and cloud_ocr_provider and cloud_ocr_api_key:
        from joyhousebot.agent.tools.ingest.cloud_ocr import extract_image_text_cloud
        return extract_image_text_cloud(path, provider=cloud_ocr_provider, api_key=cloud_ocr_api_key)
    return doc
