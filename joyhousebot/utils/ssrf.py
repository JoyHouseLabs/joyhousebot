"""Shared SSRF protection for outbound HTTP egress.

Provides:
- IP/host validation against private/loopback/reserved ranges
- Async DNS resolution with a timeout (event-loop friendly)
- SsrfProtectedTransport: an httpx transport that pins each connection to the
  exact validated DNS answer, defeating DNS rebinding / TOCTOU
- fetch_url: a GET helper with per-hop redirect validation, a download size
  cap and a text content-type allowlist
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

from joyhousebot.utils.exceptions import sanitize_error_message

DNS_RESOLVE_TIMEOUT = 5.0
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_TEXT_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/javascript",
    "application/xhtml+xml",
    "application/rss+xml",
    "application/atom+xml",
)


class SsrfBlockedError(ValueError):
    """Raised when a URL or its DNS resolution points at a forbidden address."""


class ResponseTooLargeError(ValueError):
    """Raised when a response exceeds the configured byte cap."""


class UnsupportedContentTypeError(ValueError):
    """Raised when a response content type is not processable text."""


class TooManyRedirectsError(ValueError):
    """Raised when the redirect hop limit is exceeded."""


def _parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.strip("[]").lower())
    except ValueError:
        return None


def is_forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Deny local/private/special-purpose address ranges.

    Covers loopback (127.0.0.0/8, ::1), link-local (169.254.0.0/16, fe80::/10),
    private/ULA (10/8, 172.16/12, 192.168/16, fc00::/7), multicast, reserved
    and unspecified addresses.
    """
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True
    return ip == ipaddress.ip_address("169.254.169.254")


def is_forbidden_host(host: str) -> bool:
    """Block localhost-style names and forbidden literal IPs (no DNS lookup)."""
    lowered = host.lower()
    if lowered == "localhost" or lowered.endswith(".local"):
        return True
    ip = _parse_ip(host)
    return ip is not None and is_forbidden_ip(ip)


def validate_url(url: str) -> tuple[bool, str]:
    """Validate URL structure: http(s) scheme and a non-forbidden host (no DNS)."""
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False, f"Only http/https allowed, got '{p.scheme or 'none'}'"
        if not p.netloc:
            return False, "Missing domain"
        host = p.hostname
        if not host:
            return False, "Missing hostname"
        if is_forbidden_host(host):
            return False, f"Blocked host: {host}"
        return True, ""
    except ValueError as e:
        return False, f"Invalid URL format: {e}"
    except Exception as e:
        return False, sanitize_error_message(str(e))


async def resolve_host(host: str, timeout: float = DNS_RESOLVE_TIMEOUT) -> list[str]:
    """Resolve a hostname once, off the event loop, with a timeout.

    Every resolved address is validated; a single private/reserved answer
    blocks the host. Returns the validated IP addresses.
    """
    lowered = host.lower()
    if lowered == "localhost" or lowered.endswith(".local"):
        raise SsrfBlockedError(f"Blocked host: {host}")
    ip = _parse_ip(host)
    if ip is not None:
        if is_forbidden_ip(ip):
            raise SsrfBlockedError(f"Blocked host: {host}")
        return [str(ip)]
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, type=socket.SOCK_STREAM), timeout
        )
    except asyncio.TimeoutError:
        raise SsrfBlockedError(f"DNS resolution timed out for host: {host}") from None
    except socket.gaierror:
        raise SsrfBlockedError(f"DNS resolution failed for host: {host}") from None
    ips: list[str] = []
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if is_forbidden_ip(addr):
            raise SsrfBlockedError(f"Host {host} resolves to forbidden address {addr}")
        ips.append(str(addr))
    if not ips:
        raise SsrfBlockedError(f"DNS resolution returned no addresses for host: {host}")
    return ips


async def validate_url_with_dns(url: str, timeout: float = DNS_RESOLVE_TIMEOUT) -> tuple[bool, str]:
    """Full validation: URL structure plus a DNS resolution check."""
    ok, err = validate_url(url)
    if not ok:
        return ok, err
    host = urlparse(url).hostname or ""
    try:
        await resolve_host(host, timeout)
    except SsrfBlockedError as e:
        return False, str(e)
    return True, ""


def _pin_request(request: httpx.Request, ip: str) -> httpx.Request:
    """Rewrite a request to dial `ip` while preserving Host header and TLS SNI."""
    url = request.url
    host = url.host
    default_port = 443 if url.scheme == "https" else 80
    host_header = host if url.port in (None, default_port) else f"{host}:{url.port}"
    headers = httpx.Headers(request.headers)
    headers["Host"] = host_header
    extensions = dict(request.extensions)
    if url.scheme == "https":
        extensions["sni_hostname"] = host
    return httpx.Request(
        request.method,
        url.copy_with(host=ip),
        headers=headers,
        stream=request.stream,
        extensions=extensions,
    )


class SsrfProtectedTransport(httpx.AsyncHTTPTransport):
    """httpx transport that resolves DNS itself and dials the validated IP.

    Validation and connection use the *same* resolution result, so an attacker
    controlling authoritative DNS cannot return a public IP at validation time
    and a private IP at connect time (DNS rebinding / TOCTOU). The Host header
    and TLS SNI still use the original hostname.
    """

    def __init__(self, *args, resolve_timeout: float = DNS_RESOLVE_TIMEOUT, **kwargs):
        super().__init__(*args, **kwargs)
        self._resolve_timeout = resolve_timeout

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host:
            ips = await resolve_host(host, self._resolve_timeout)
            request = _pin_request(request, ips[0])
        return await super().handle_async_request(request)


def is_text_content_type(content_type: str) -> bool:
    """Allow only text-class content types (HTML, plain text, JSON, XML, ...)."""
    mime = content_type.split(";", 1)[0].strip().lower()
    if not mime:
        return True
    return mime.startswith(_TEXT_CONTENT_TYPES) or mime.endswith(("+json", "+xml"))


async def fetch_url(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> tuple[httpx.Response, str]:
    """GET a URL with per-hop redirect validation, a byte cap and a type allowlist.

    Redirects are followed manually: every hop's Location is re-validated and
    re-pinned through the client's transport before being followed. Raises
    SsrfBlockedError, ResponseTooLargeError, UnsupportedContentTypeError,
    TooManyRedirectsError, or the underlying httpx errors.
    """
    current = url
    for _ in range(max_redirects + 1):
        ok, err = validate_url(current)
        if not ok:
            raise SsrfBlockedError(f"URL validation failed: {err}")
        async with client.stream("GET", current, headers=headers) as response:
            if response.status_code in _REDIRECT_STATUSES and response.headers.get("location"):
                current = urljoin(current, response.headers["location"])
                continue
            response.raise_for_status()
            ctype = response.headers.get("content-type", "")
            if ctype and not is_text_content_type(ctype):
                raise UnsupportedContentTypeError(
                    f"Unsupported content type: {ctype.split(';')[0].strip()}"
                )
            content_length = response.headers.get("content-length", "")
            if content_length.isdigit() and int(content_length) > max_bytes:
                raise ResponseTooLargeError(
                    f"Response too large: {content_length} bytes (limit {max_bytes})"
                )
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ResponseTooLargeError(f"Response exceeded the {max_bytes} bytes limit")
                chunks.append(chunk)
            body = b"".join(chunks)
        encoding = response.charset_encoding or "utf-8"
        return response, body.decode(encoding, errors="replace")
    raise TooManyRedirectsError(f"Exceeded {max_redirects} redirects")
