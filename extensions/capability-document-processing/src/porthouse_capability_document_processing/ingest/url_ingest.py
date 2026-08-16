"""Fetch and normalize a public URL through the Core-enforced network boundary."""

import html
import json
import re

from porthouse.extension_sdk.network import (
    DEFAULT_MAX_BYTES,
    SsrfProtectedTransport,
    TrackedAsyncClient,
    fetch_url,
    sanitize_error_message,
)
from porthouse.extension_sdk.network import validate_url as _validate_url

from .chunking import chunk_text
from .models import IngestDoc

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
MAX_REDIRECTS = 5


def _strip_tags(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


async def fetch_and_ingest_url(
    url: str,
    max_chars: int = 50000,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> IngestDoc:
    ok, error = _validate_url(url)
    if not ok:
        raise ValueError(error)

    from readability import Document

    async with TrackedAsyncClient(
        propagate_headers=False,
        transport=SsrfProtectedTransport(),
        follow_redirects=False,
        timeout=30.0,
    ) as client:
        try:
            response, text = await fetch_url(
                client,
                url,
                headers={"User-Agent": USER_AGENT},
                max_redirects=MAX_REDIRECTS,
                max_bytes=max_bytes,
            )
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(sanitize_error_message(str(exc))) from exc

    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        text = str(json.loads(text))
        title = url
    elif "text/html" in content_type or text[:256].lower().startswith(("<!doctype", "<html")):
        document = Document(text)
        title = document.title() or url
        text = f"# {title}\n\n" + _strip_tags(document.summary())
    else:
        title = url

    text = text[:max_chars]
    return IngestDoc(
        source_type="url",
        source_url=url,
        title=title,
        chunks=chunk_text(text, chunk_size=1200, overlap=200, page=None),
        trace={
            "final_url": str(response.url),
            "status": response.status_code,
            "length": len(text),
        },
    )
