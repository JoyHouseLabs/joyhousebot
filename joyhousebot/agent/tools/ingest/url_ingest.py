"""URL ingest: fetch page, extract readable content, chunk and return IngestDoc."""

import html
import ipaddress
import re
import socket
from urllib.parse import urlparse

import httpx

from joyhousebot.agent.tools.ingest.chunking import chunk_text
from joyhousebot.agent.tools.ingest.models import IngestDoc

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
MAX_REDIRECTS = 5


def _strip_tags(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _validate_url(url: str) -> tuple[bool, str]:
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False, f"Only http/https allowed, got '{p.scheme or 'none'}'"
        if not p.netloc:
            return False, "Missing domain"
        host = p.hostname or ""
        if _is_forbidden_host(host):
            return False, f"Blocked host: {host}"
        return True, ""
    except Exception as e:
        return False, str(e)


def _is_forbidden_host(host: str) -> bool:
    if host.lower() in {"localhost"} or host.lower().endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return bool(
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved
            or ip == ipaddress.ip_address("169.254.169.254")
        )
    except ValueError:
        pass
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                return True
    except socket.gaierror:
        return True
    return False


async def fetch_and_ingest_url(url: str, max_chars: int = 50000) -> IngestDoc:
    """Fetch URL, extract readable content, chunk and return IngestDoc."""
    ok, err = _validate_url(url)
    if not ok:
        raise ValueError(err)

    from readability import Document

    async with httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        timeout=30.0,
    ) as client:
        r = await client.get(url, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()

    final_host = urlparse(str(r.url)).hostname
    if final_host and _is_forbidden_host(final_host):
        raise ValueError(f"Blocked final URL host: {final_host}")

    ctype = r.headers.get("content-type", "")
    if "application/json" in ctype:
        text = str(r.json())
        title = url
    elif "text/html" in ctype or (r.text[:256].lower().startswith(("<!doctype", "<html"))):
        doc = Document(r.text)
        title = doc.title() or url
        text = f"# {title}\n\n" + _strip_tags(doc.summary())
    else:
        text = r.text
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
