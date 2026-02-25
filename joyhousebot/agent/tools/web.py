"""Web tools: web_search and web_fetch."""

import asyncio
import html
import ipaddress
import json
import os
import re
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from joyhousebot.agent.tools.base import Tool
from joyhousebot.utils.exceptions import (
    ToolError,
    TimeoutError,
    RateLimitError,
    sanitize_error_message,
)


USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
MAX_REDIRECTS = 5
_REQUEST_TIMEOUT = 30.0
_SEARCH_TIMEOUT = 10.0
_MAX_RETRIES = 3


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r'<script[\s\S]*?</script>', '', text, flags=re.I)
    text = re.sub(r'<style[\s\S]*?</style>', '', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    return html.unescape(text).strip()


def _normalize(text: str) -> str:
    """Normalize whitespace."""
    text = re.sub(r'[ \t]+', ' ', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _validate_url(url: str) -> tuple[bool, str]:
    """Validate URL: must be http(s) with valid domain."""
    try:
        p = urlparse(url)
        if p.scheme not in ('http', 'https'):
            return False, f"Only http/https allowed, got '{p.scheme or 'none'}'"
        if not p.netloc:
            return False, "Missing domain"
        host = p.hostname
        if not host:
            return False, "Missing hostname"
        if _is_forbidden_host(host):
            return False, f"Blocked host: {host}"
        return True, ""
    except ValueError as e:
        return False, f"Invalid URL format: {e}"
    except Exception as e:
        return False, sanitize_error_message(str(e))


def _is_forbidden_host(host: str) -> bool:
    """Block localhost/private IPs to reduce SSRF risk."""
    lowered = host.lower()
    if lowered in {"localhost"} or lowered.endswith(".local"):
        return True

    try:
        ip = ipaddress.ip_address(lowered)
        return _is_forbidden_ip(ip)
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True

    for info in infos:
        ip_raw = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_raw)
        except ValueError:
            continue
        if _is_forbidden_ip(ip):
            return True
    return False


def _is_forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Deny local/private/special-purpose address ranges."""
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        return True
    if ip == ipaddress.ip_address("169.254.169.254"):
        return True
    return False


class WebSearchTool(Tool):
    """Search the web using Brave Search API."""

    name = "web_search"
    description = "Search the web. Returns titles, URLs, and snippets."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "count": {"type": "integer", "description": "Results (1-10)", "minimum": 1, "maximum": 10}
        },
        "required": ["query"]
    }

    def __init__(self, api_key: str | None = None, max_results: int = 5):
        self.api_key = api_key or os.environ.get("BRAVE_API_KEY", "")
        self.max_results = max_results

    async def execute(self, query: str, count: int | None = None, **kwargs: Any) -> str:
        if not self.api_key:
            return "Error: BRAVE_API_KEY not configured"

        try:
            n = min(max(count or self.max_results, 1), 10)
            async with httpx.AsyncClient() as client:
                r = None
                last_error: Exception | None = None
                for attempt in range(_MAX_RETRIES):
                    try:
                        r = await client.get(
                            "https://api.search.brave.com/res/v1/web/search",
                            params={"q": query, "count": n},
                            headers={"Accept": "application/json", "X-Subscription-Token": self.api_key},
                            timeout=_SEARCH_TIMEOUT
                        )
                        r.raise_for_status()
                        break
                    except httpx.TimeoutException:
                        last_error = TimeoutError("web_search", _SEARCH_TIMEOUT)
                        if attempt < _MAX_RETRIES - 1:
                            await asyncio.sleep(0.5 * (attempt + 1))
                            continue
                        raise last_error
                    except httpx.HTTPStatusError as e:
                        if e.response.status_code == 429:
                            raise RateLimitError("Brave Search")
                        if e.response.status_code >= 500 and attempt < _MAX_RETRIES - 1:
                            await asyncio.sleep(0.5 * (attempt + 1))
                            continue
                        raise
                    except httpx.RequestError as e:
                        last_error = e
                        if attempt < _MAX_RETRIES - 1:
                            await asyncio.sleep(0.5 * (attempt + 1))
                            continue
                        raise
                assert r is not None

            results = r.json().get("web", {}).get("results", [])
            if not results:
                return f"No results for: {query}"

            lines = [f"Results for: {query}\n"]
            for i, item in enumerate(results[:n], 1):
                lines.append(f"{i}. {item.get('title', '')}\n   {item.get('url', '')}")
                if desc := item.get("description"):
                    lines.append(f"   {desc}")
            return "\n".join(lines)
        except RateLimitError as e:
            return f"Error: {e.message}"
        except TimeoutError as e:
            return f"Error: {e.message}"
        except httpx.HTTPStatusError as e:
            return f"Error: HTTP {e.response.status_code}"
        except httpx.RequestError as e:
            return f"Error: Connection failed - {sanitize_error_message(str(e))}"
        except json.JSONDecodeError:
            return "Error: Invalid response from search API"
        except Exception as e:
            return f"Error: {sanitize_error_message(str(e))}"


class WebFetchTool(Tool):
    """Fetch and extract content from a URL using Readability."""

    name = "web_fetch"
    description = "Fetch URL and extract readable content (HTML → markdown/text)."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "extractMode": {"type": "string", "enum": ["markdown", "text"], "default": "markdown"},
            "maxChars": {"type": "integer", "minimum": 100}
        },
        "required": ["url"]
    }

    def __init__(self, max_chars: int = 50000):
        self.max_chars = max_chars

    async def execute(self, url: str, extractMode: str = "markdown", maxChars: int | None = None, **kwargs: Any) -> str:
        from readability import Document

        max_chars = maxChars or self.max_chars

        is_valid, error_msg = _validate_url(url)
        if not is_valid:
            return json.dumps({"error": f"URL validation failed: {error_msg}", "url": url})

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                max_redirects=MAX_REDIRECTS,
                timeout=_REQUEST_TIMEOUT
            ) as client:
                r = None
                for attempt in range(_MAX_RETRIES):
                    try:
                        r = await client.get(url, headers={"User-Agent": USER_AGENT})
                        r.raise_for_status()
                        break
                    except httpx.TimeoutException:
                        if attempt < _MAX_RETRIES - 1:
                            await asyncio.sleep(0.5 * (attempt + 1))
                            continue
                        return json.dumps({"error": f"Request timed out after {_REQUEST_TIMEOUT}s", "url": url})
                    except httpx.RequestError as e:
                        if attempt < _MAX_RETRIES - 1:
                            await asyncio.sleep(0.5 * (attempt + 1))
                            continue
                        return json.dumps({"error": f"Connection failed: {sanitize_error_message(str(e))}", "url": url})
                assert r is not None

            final_host = urlparse(str(r.url)).hostname
            if final_host and _is_forbidden_host(final_host):
                return json.dumps({"error": f"Blocked final URL host: {final_host}", "url": str(r.url)})

            ctype = r.headers.get("content-type", "")

            if "application/json" in ctype:
                text, extractor = json.dumps(r.json(), indent=2), "json"
            elif "text/html" in ctype or r.text[:256].lower().startswith(("<!doctype", "<html")):
                doc = Document(r.text)
                content = self._to_markdown(doc.summary()) if extractMode == "markdown" else _strip_tags(doc.summary())
                text = f"# {doc.title()}\n\n{content}" if doc.title() else content
                extractor = "readability"
            else:
                text, extractor = r.text, "raw"

            truncated = len(text) > max_chars
            if truncated:
                text = text[:max_chars]

            return json.dumps({"url": url, "finalUrl": str(r.url), "status": r.status_code,
                              "extractor": extractor, "truncated": truncated, "length": len(text), "text": text})
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}", "url": url})
        except json.JSONDecodeError:
            return json.dumps({"error": "Invalid JSON response", "url": url})
        except Exception as e:
            return json.dumps({"error": sanitize_error_message(str(e)), "url": url})

    def _to_markdown(self, html: str) -> str:
        """Convert HTML to markdown."""
        text = re.sub(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
                      lambda m: f'[{_strip_tags(m[2])}]({m[1]})', html, flags=re.I)
        text = re.sub(r'<h([1-6])[^>]*>([\s\S]*?)</h\1>',
                      lambda m: f'\n{"#" * int(m[1])} {_strip_tags(m[2])}\n', text, flags=re.I)
        text = re.sub(r'<li[^>]*>([\s\S]*?)</li>', lambda m: f'\n- {_strip_tags(m[1])}', text, flags=re.I)
        text = re.sub(r'</(p|div|section|article)>', '\n\n', text, flags=re.I)
        text = re.sub(r'<(br|hr)\s*/?>', '\n', text, flags=re.I)
        return _normalize(_strip_tags(text))
