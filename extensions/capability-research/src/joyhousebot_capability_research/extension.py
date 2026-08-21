"""Optional web_search and web_fetch capability extension."""

import asyncio
import html
import json
import os
import re
from typing import Any

import httpx

from joyhousebot.extension_sdk import (
    CapabilityContext,
    CapabilityDefinition,
    CapabilityExtensionManifest,
    CapabilityKind,
    CapabilityRef,
    CapabilityResult,
)
from joyhousebot.extension_sdk.manifest import source_tree_digest
from joyhousebot.extension_sdk.network import (
    DEFAULT_MAX_BYTES,
    RateLimitError,
    ResponseTooLargeError,
    SsrfBlockedError,
    SsrfProtectedTransport,
    TimeoutError,
    TooManyRedirectsError,
    TrackedAsyncClient,
    UnsupportedContentTypeError,
    fetch_url,
    sanitize_error_message,
)
from joyhousebot.extension_sdk.network import (
    validate_url as _validate_url,
)
from joyhousebot.extension_sdk.tools import Tool, ToolInvocationError

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
MAX_REDIRECTS = 5
_REQUEST_TIMEOUT = 30.0
_SEARCH_TIMEOUT = 10.0
_MAX_RETRIES = 3


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _normalize(text: str) -> str:
    """Normalize whitespace."""
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class WebSearchTool(Tool):
    """Search the web using Brave Search API."""

    name = "web_search"
    description = (
        "Search the web for information. PREFERRED for finding news, articles, and current events. "
        "Returns titles, URLs, and snippets. Use this FIRST before web_fetch. "
        "Works reliably for all topics including news, technical info, and general queries."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "count": {
                "type": "integer",
                "description": "Results (1-10)",
                "minimum": 1,
                "maximum": 10,
            },
        },
        "required": ["query"],
    }

    def __init__(self, api_key: str | None = None, max_results: int = 5):
        self.api_key = api_key or os.environ.get("BRAVE_API_KEY", "")
        self.max_results = max_results

    async def execute(self, query: str, count: int | None = None, **kwargs: Any) -> str:
        if not self.api_key:
            raise ToolInvocationError("CAPABILITY_NOT_CONFIGURED", "BRAVE_API_KEY not configured")

        try:
            n = min(max(count or self.max_results, 1), 10)
            async with TrackedAsyncClient() as client:
                r = None
                last_error: Exception | None = None
                for attempt in range(_MAX_RETRIES):
                    try:
                        r = await client.get(
                            "https://api.search.brave.com/res/v1/web/search",
                            params={"q": query, "count": n},
                            headers={
                                "Accept": "application/json",
                                "X-Subscription-Token": self.api_key,
                            },
                            timeout=_SEARCH_TIMEOUT,
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
            raise ToolInvocationError("RATE_LIMITED", e.message, retryable=True) from e
        except TimeoutError as e:
            raise ToolInvocationError("UPSTREAM_TIMEOUT", e.message, retryable=True) from e
        except httpx.HTTPStatusError as e:
            raise ToolInvocationError(
                "UPSTREAM_HTTP_ERROR", f"HTTP {e.response.status_code}", retryable=e.response.status_code >= 500
            ) from e
        except httpx.RequestError as e:
            raise ToolInvocationError(
                "UPSTREAM_CONNECTION_FAILED", sanitize_error_message(str(e)), retryable=True
            ) from e
        except json.JSONDecodeError:
            raise ToolInvocationError("UPSTREAM_INVALID_RESPONSE", "Invalid response from search API")
        except Exception as e:
            raise ToolInvocationError("WEB_SEARCH_FAILED", sanitize_error_message(str(e))) from e


class WebFetchTool(Tool):
    """Fetch and extract content from a URL using Readability."""

    name = "web_fetch"
    description = (
        "Fetch a specific URL and extract readable content. "
        "LIMITATIONS: Cannot render JavaScript-heavy pages (Google News, Twitter/X, SPA sites). "
        "For news/current events, use web_search FIRST instead. "
        "Best for: static articles, blogs, documentation, and server-rendered pages."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "extract_mode": {"type": "string", "enum": ["markdown", "text"], "default": "markdown"},
            "max_chars": {"type": "integer", "minimum": 100, "maximum": 50000},
        },
        "required": ["url"],
    }

    def __init__(self, max_chars: int = 50000, max_bytes: int = DEFAULT_MAX_BYTES):
        self.max_chars = max_chars
        self.max_bytes = max_bytes

    async def execute(
        self, url: str, extract_mode: str = "markdown", max_chars: int | None = None, **kwargs: Any
    ) -> str:
        from readability import Document

        max_chars = min(max_chars, self.max_chars) if max_chars else self.max_chars

        is_valid, error_msg = _validate_url(url)
        if not is_valid:
            raise ToolInvocationError("INVALID_URL", f"URL validation failed: {error_msg}")

        try:
            async with TrackedAsyncClient(
                propagate_headers=False,
                transport=SsrfProtectedTransport(),
                follow_redirects=False,
                timeout=_REQUEST_TIMEOUT,
            ) as client:
                r = None
                text = ""
                for attempt in range(_MAX_RETRIES):
                    try:
                        r, text = await fetch_url(
                            client,
                            url,
                            headers={"User-Agent": USER_AGENT},
                            max_redirects=MAX_REDIRECTS,
                            max_bytes=self.max_bytes,
                        )
                        break
                    except httpx.TimeoutException:
                        if attempt < _MAX_RETRIES - 1:
                            await asyncio.sleep(0.5 * (attempt + 1))
                            continue
                        raise ToolInvocationError(
                            "UPSTREAM_TIMEOUT",
                            f"Request timed out after {_REQUEST_TIMEOUT}s",
                            retryable=True,
                        )
                    except httpx.RequestError as e:
                        if attempt < _MAX_RETRIES - 1:
                            await asyncio.sleep(0.5 * (attempt + 1))
                            continue
                        raise ToolInvocationError(
                            "UPSTREAM_CONNECTION_FAILED",
                            sanitize_error_message(str(e)),
                            retryable=True,
                        ) from e
                assert r is not None

            ctype = r.headers.get("content-type", "")

            if "application/json" in ctype:
                text, extractor = json.dumps(json.loads(text), indent=2), "json"
            elif "text/html" in ctype or text[:256].lower().startswith(("<!doctype", "<html")):
                doc = Document(text)
                content = (
                    self._to_markdown(doc.summary())
                    if extract_mode == "markdown"
                    else _strip_tags(doc.summary())
                )
                text = f"# {doc.title()}\n\n{content}" if doc.title() else content
                extractor = "readability"
            else:
                extractor = "raw"

            truncated = len(text) > max_chars
            if truncated:
                text = text[:max_chars]

            return json.dumps(
                {
                    "url": url,
                    "finalUrl": str(r.url),
                    "status": r.status_code,
                    "extractor": extractor,
                    "truncated": truncated,
                    "length": len(text),
                    "text": text,
                }
            )
        except (
            SsrfBlockedError,
            ResponseTooLargeError,
            UnsupportedContentTypeError,
            TooManyRedirectsError,
        ) as e:
            raise ToolInvocationError("FETCH_BLOCKED", str(e)) from e
        except httpx.HTTPStatusError as e:
            raise ToolInvocationError(
                "UPSTREAM_HTTP_ERROR", f"HTTP {e.response.status_code}", retryable=e.response.status_code >= 500
            ) from e
        except json.JSONDecodeError:
            raise ToolInvocationError("UPSTREAM_INVALID_RESPONSE", "Invalid JSON response")
        except ToolInvocationError:
            raise
        except Exception as e:
            raise ToolInvocationError("WEB_FETCH_FAILED", sanitize_error_message(str(e))) from e

    def _to_markdown(self, html: str) -> str:
        """Convert HTML to markdown."""
        text = re.sub(
            r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
            lambda m: f"[{_strip_tags(m[2])}]({m[1]})",
            html,
            flags=re.I,
        )
        text = re.sub(
            r"<h([1-6])[^>]*>([\s\S]*?)</h\1>",
            lambda m: f"\n{'#' * int(m[1])} {_strip_tags(m[2])}\n",
            text,
            flags=re.I,
        )
        text = re.sub(
            r"<li[^>]*>([\s\S]*?)</li>", lambda m: f"\n- {_strip_tags(m[1])}", text, flags=re.I
        )
        text = re.sub(r"</(p|div|section|article)>", "\n\n", text, flags=re.I)
        text = re.sub(r"<(br|hr)\s*/?>", "\n", text, flags=re.I)
        return _normalize(_strip_tags(text))


class _ToolHandler:
    def __init__(self, tool: Tool) -> None:
        self.tool = tool

    async def execute(
        self, context: CapabilityContext, input: dict[str, Any]
    ) -> CapabilityResult:
        try:
            value = await self.tool.execute(**input)
            return CapabilityResult(success=True, output=value)
        except ToolInvocationError as exc:
            return CapabilityResult(
                success=False,
                error={
                    "code": exc.code,
                    "message": str(exc),
                    "retryable": exc.retryable,
                },
            )


class ResearchCapabilityExtension:
    extension_id = "capability-research"
    version = "1.0.0"

    def manifest(self) -> CapabilityExtensionManifest:
        return CapabilityExtensionManifest(
            extension_id=self.extension_id,
            version=self.version,
            name="joyhousebot Research Capabilities",
            description="SSRF-safe web search and readable page fetching.",
            distribution_name="joyhousebot-capability-research",
            build_digest=source_tree_digest(__file__),
            required_permissions=("network.search", "network.http.read"),
            dependencies=(
                {"id": "public-web", "kind": "http", "required": True},
                {"id": "brave-search-key", "kind": "credential", "required": False},
            ),
        )

    def register(self, registry: Any) -> None:
        for tool, permission in (
            (WebSearchTool(), "network.search"),
            (WebFetchTool(), "network.http.read"),
        ):
            registry.register_capability(
                CapabilityDefinition(
                    ref=CapabilityRef(tool.name, self.version, CapabilityKind.CAPABILITY),
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.parameters,
                    output_schema={"type": "string"},
                    adapter="extension",
                    tags=("research", "web"),
                    expected_duration_seconds=10,
                    timeout_seconds=45,
                    idempotent=True,
                    retryable=True,
                    side_effect="read",
                    permissions=(permission,),
                    data_classification="internal",
                ),
                _ToolHandler(tool),
            )

    def health_checks(self) -> tuple[Any, ...]:
        return ()


def create_extension() -> ResearchCapabilityExtension:
    return ResearchCapabilityExtension()
