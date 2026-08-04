"""URL ingest: fetch page, extract readable content, chunk and return IngestDoc."""

import html
import json
import re

from joyhousebot.agent.tools.ingest.chunking import chunk_text
from joyhousebot.agent.tools.ingest.models import IngestDoc
from joyhousebot.runtime.http_tracking import TrackedAsyncClient
from joyhousebot.utils.exceptions import sanitize_error_message
from joyhousebot.utils.ssrf import (
    DEFAULT_MAX_BYTES,
    SsrfProtectedTransport,
    fetch_url,
)
from joyhousebot.utils.ssrf import (
    validate_url as _validate_url,
)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
MAX_REDIRECTS = 5


def _strip_tags(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


async def fetch_and_ingest_url(
    url: str, max_chars: int = 50000, max_bytes: int = DEFAULT_MAX_BYTES
) -> IngestDoc:
    """Fetch URL, extract readable content, chunk and return IngestDoc."""
    ok, err = _validate_url(url)
    if not ok:
        raise ValueError(err)

    from readability import Document

    async with TrackedAsyncClient(
        propagate_headers=False,
        transport=SsrfProtectedTransport(),
        follow_redirects=False,
        timeout=30.0,
    ) as client:
        try:
            r, text = await fetch_url(
                client,
                url,
                headers={"User-Agent": USER_AGENT},
                max_redirects=MAX_REDIRECTS,
                max_bytes=max_bytes,
            )
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(sanitize_error_message(str(e))) from e

    ctype = r.headers.get("content-type", "")
    if "application/json" in ctype:
        text = str(json.loads(text))
        title = url
    elif "text/html" in ctype or (text[:256].lower().startswith(("<!doctype", "<html"))):
        doc = Document(text)
        title = doc.title() or url
        text = f"# {title}\n\n" + _strip_tags(doc.summary())
    else:
        title = url

    if len(text) > max_chars:
        text = text[:max_chars]
    chunks = chunk_text(text, chunk_size=1200, overlap=200, page=None)
    return IngestDoc(
        source_type="url",
        source_url=url,
        title=title,
        chunks=chunks,
        trace={"final_url": str(r.url), "status": r.status_code, "length": len(text)},
    )
