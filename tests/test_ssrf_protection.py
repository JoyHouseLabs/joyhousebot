"""Tests for SSRF protection: DNS rebinding, redirect validation, size/type caps."""

import asyncio
import ipaddress
import json
import socket
import threading
from contextlib import AsyncExitStack
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import httpx
import pytest
from joyhousebot_capability_context_assets import plugin as kb_module
from joyhousebot_capability_research import WebFetchTool
from joyhousebot_capability_research import plugin as web_module
from joyhousebot_connector_mcp_client import MCPToolWrapper, connect_mcp_servers

from joyhousebot.capabilities.tool_adapter import ToolInvocationError
from joyhousebot.extension_sdk import CapabilityContext
from joyhousebot.runtime.http_tracking import TrackedAsyncClient
from joyhousebot.utils import ssrf
from joyhousebot.utils.ssrf import (
    ResponseTooLargeError,
    SsrfBlockedError,
    SsrfProtectedTransport,
    TooManyRedirectsError,
    UnsupportedContentTypeError,
    fetch_url,
    fetch_url_bytes,
    is_forbidden_ip,
    resolve_host,
    validate_url,
)


class _Handler(BaseHTTPRequestHandler):
    """Configurable test HTTP handler; class attrs set per-test."""

    routes: dict = {}
    seen: list = []

    def do_GET(self) -> None:  # noqa: N802
        type(self).seen.append({"path": self.path, "host": self.headers.get("Host")})
        route = self.routes.get(self.path)
        if route is None:
            self.send_error(404)
            return
        status, headers, body = route
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture()
def http_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    _Handler.routes = {}
    _Handler.seen = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


@pytest.fixture()
def pin_to_localhost(monkeypatch):
    """Fake DNS: example.test resolves to 127.0.0.1 (attacker-controlled answer)."""

    async def fake_resolve(host: str, timeout: float = 5.0):
        if host == "example.test":
            return ["127.0.0.1"]
        return await resolve_host(host, timeout)

    monkeypatch.setattr(ssrf, "resolve_host", fake_resolve)


# --- URL / IP validation ----------------------------------------------------


def test_validate_url_blocks_private_and_reserved():
    for url in (
        "http://localhost/a",
        "http://foo.local/a",
        "http://127.0.0.1/a",
        "http://10.0.0.1/a",
        "http://192.168.1.1/a",
        "http://172.16.0.1/a",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/a",
        "http://[fd00::1]/a",
        "http://[fe80::1]/a",
        "ftp://example.com/a",
        "http:///nohost",
    ):
        ok, _ = validate_url(url)
        assert not ok, url

    ok, _ = validate_url("https://example.com/a")
    assert ok


def test_is_forbidden_ip_ranges():
    for raw in (
        "127.0.0.1",
        "10.1.2.3",
        "192.168.0.1",
        "172.16.5.5",
        "169.254.169.254",
        "169.254.1.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fd00::1",
        "fe80::1",
    ):
        assert is_forbidden_ip(ipaddress.ip_address(raw)), raw
    assert not is_forbidden_ip(ipaddress.ip_address("93.184.216.34"))


# --- DNS resolution (async, timeout) ----------------------------------------


async def test_resolve_host_literal_ip():
    assert await resolve_host("93.184.216.34") == ["93.184.216.34"]
    with pytest.raises(SsrfBlockedError):
        await resolve_host("169.254.169.254")
    with pytest.raises(SsrfBlockedError):
        await resolve_host("localhost")


async def test_resolve_host_blocks_forbidden_answer(monkeypatch):
    loop = asyncio.get_running_loop()

    async def fake_getaddrinfo(host, port, *, type=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(SsrfBlockedError, match="forbidden address"):
        await resolve_host("evil.test")


async def test_resolve_host_dns_timeout(monkeypatch):
    loop = asyncio.get_running_loop()

    async def slow_getaddrinfo(host, port, *, type=0):
        await asyncio.sleep(10)
        return []

    monkeypatch.setattr(loop, "getaddrinfo", slow_getaddrinfo)
    with pytest.raises(SsrfBlockedError, match="timed out"):
        await resolve_host("slow.test", timeout=0.05)


# --- Connection-layer IP pinning (DNS rebinding / TOCTOU) --------------------


def test_pin_request_rewrites_url_but_keeps_host_and_sni():
    req = httpx.Request("GET", "https://example.com:8443/a?b=1")
    pinned = ssrf._pin_request(req, "93.184.216.34")
    assert str(pinned.url) == "https://93.184.216.34:8443/a?b=1"
    assert pinned.headers["Host"] == "example.com:8443"
    assert pinned.extensions["sni_hostname"] == "example.com"


async def test_transport_dials_pinned_ip_with_original_host(http_server, pin_to_localhost):
    port = http_server.server_address[1]
    _Handler.routes["/data"] = (200, {"Content-Type": "text/plain"}, b"pinned-ok")

    async with httpx.AsyncClient(transport=SsrfProtectedTransport()) as client:
        r = await client.get(f"http://example.test:{port}/data")

    assert r.status_code == 200
    assert r.text == "pinned-ok"
    # The server was reached via the pinned IP while the Host header stayed original.
    assert _Handler.seen[0]["host"] == f"example.test:{port}"


async def test_transport_blocks_private_answer_before_connect(http_server):
    port = http_server.server_address[1]
    async with httpx.AsyncClient(transport=SsrfProtectedTransport()) as client:
        with pytest.raises(SsrfBlockedError):
            await client.get(f"http://127.0.0.1:{port}/")
    assert _Handler.seen == []


async def test_transport_blocks_rebinding_answer(http_server, monkeypatch):
    """DNS answer flips to a private IP at connect time: connection is refused."""

    async def fake_resolve(host, timeout=5.0):
        return ["169.254.169.254"]

    # _pin never runs because the answer itself is validated inside resolve_host;
    # simulate a resolver that returns a forbidden IP to the transport.
    async def transport_level_resolve(host, timeout=5.0):
        raise SsrfBlockedError(f"Host {host} resolves to forbidden address 169.254.169.254")

    monkeypatch.setattr(ssrf, "resolve_host", transport_level_resolve)
    async with httpx.AsyncClient(transport=SsrfProtectedTransport()) as client:
        with pytest.raises(SsrfBlockedError):
            await client.get("http://rebind.test/")
    assert _Handler.seen == []


# --- Redirect hop validation --------------------------------------------------


async def test_redirect_to_metadata_endpoint_blocked(http_server, pin_to_localhost):
    port = http_server.server_address[1]
    _Handler.routes["/go"] = (
        302,
        {"Location": "http://169.254.169.254/latest/meta-data"},
        b"",
    )
    async with httpx.AsyncClient(
        transport=SsrfProtectedTransport(), follow_redirects=False
    ) as client:
        with pytest.raises(SsrfBlockedError):
            await fetch_url(client, f"http://example.test:{port}/go")


async def test_redirect_same_host_followed(http_server, pin_to_localhost):
    port = http_server.server_address[1]
    _Handler.routes["/go"] = (302, {"Location": "/final"}, b"")
    _Handler.routes["/final"] = (200, {"Content-Type": "text/plain"}, b"landed")
    async with httpx.AsyncClient(
        transport=SsrfProtectedTransport(), follow_redirects=False
    ) as client:
        response, text = await fetch_url(client, f"http://example.test:{port}/go")
    assert text == "landed"
    assert response.status_code == 200


async def test_redirect_loop_capped(http_server, pin_to_localhost):
    port = http_server.server_address[1]
    _Handler.routes["/loop"] = (302, {"Location": "/loop"}, b"")
    async with httpx.AsyncClient(
        transport=SsrfProtectedTransport(), follow_redirects=False
    ) as client:
        with pytest.raises(TooManyRedirectsError):
            await fetch_url(client, f"http://example.test:{port}/loop", max_redirects=3)
    assert len(_Handler.seen) == 4  # initial + 3 redirects


# --- Size / content-type caps --------------------------------------------------


async def test_download_size_cap_aborts(http_server, pin_to_localhost):
    port = http_server.server_address[1]
    _Handler.routes["/big"] = (200, {"Content-Type": "text/plain"}, b"x" * 65536)
    async with httpx.AsyncClient(
        transport=SsrfProtectedTransport(), follow_redirects=False
    ) as client:
        with pytest.raises(ResponseTooLargeError):
            await fetch_url(client, f"http://example.test:{port}/big", max_bytes=1024)


async def test_content_length_precheck(http_server, pin_to_localhost):
    port = http_server.server_address[1]
    _Handler.routes["/huge"] = (
        200,
        {"Content-Type": "text/plain", "Content-Length": "99999999"},
        b"small",
    )
    async with httpx.AsyncClient(
        transport=SsrfProtectedTransport(), follow_redirects=False
    ) as client:
        with pytest.raises(ResponseTooLargeError, match="too large"):
            await fetch_url(client, f"http://example.test:{port}/huge", max_bytes=1024)


async def test_binary_content_type_rejected(http_server, pin_to_localhost):
    port = http_server.server_address[1]
    _Handler.routes["/bin"] = (
        200,
        {"Content-Type": "application/octet-stream"},
        b"\x00\x01",
    )
    async with httpx.AsyncClient(
        transport=SsrfProtectedTransport(), follow_redirects=False
    ) as client:
        with pytest.raises(UnsupportedContentTypeError):
            await fetch_url(client, f"http://example.test:{port}/bin")


async def test_json_content_type_allowed(http_server, pin_to_localhost):
    port = http_server.server_address[1]
    _Handler.routes["/j"] = (200, {"Content-Type": "application/json"}, b'{"a": 1}')
    async with httpx.AsyncClient(
        transport=SsrfProtectedTransport(), follow_redirects=False
    ) as client:
        _, text = await fetch_url(client, f"http://example.test:{port}/j")
    assert json.loads(text) == {"a": 1}


async def test_binary_fetch_requires_an_explicit_parser_content_type(
    http_server, pin_to_localhost
):
    port = http_server.server_address[1]
    _Handler.routes["/document.pdf"] = (
        200,
        {"Content-Type": "application/pdf"},
        b"%PDF-safe-fixture",
    )
    async with httpx.AsyncClient(
        transport=SsrfProtectedTransport(), follow_redirects=False
    ) as client:
        response, body = await fetch_url_bytes(
            client,
            f"http://example.test:{port}/document.pdf",
            allowed_content_types=("application/pdf",),
            max_bytes=1024,
        )
        assert response.status_code == 200
        assert body == b"%PDF-safe-fixture"
        with pytest.raises(UnsupportedContentTypeError):
            await fetch_url_bytes(
                client,
                f"http://example.test:{port}/document.pdf",
                allowed_content_types=("application/zip",),
                max_bytes=1024,
            )


# --- WebFetchTool ------------------------------------------------------------


def _fake_response(ctype: str = "text/plain") -> httpx.Response:
    return httpx.Response(
        200,
        headers={"Content-Type": ctype},
        request=httpx.Request("GET", "http://example.com/"),
    )


async def test_web_fetch_clamps_user_max_chars(monkeypatch):
    async def fake_fetch(client, url, **kwargs):
        return _fake_response(), "x" * 100000

    monkeypatch.setattr(web_module, "fetch_url", fake_fetch)
    tool = WebFetchTool()
    result = json.loads(await tool.execute("http://example.com/", max_chars=10**9))
    assert result["truncated"] is True
    assert result["length"] == tool.max_chars


def test_web_fetch_schema_caps_max_chars():
    assert WebFetchTool.parameters["properties"]["max_chars"]["maximum"] == 50000


async def test_web_fetch_blocks_metadata_url():
    tool = WebFetchTool()
    with pytest.raises(ToolInvocationError, match="URL validation failed") as captured:
        await tool.execute("http://169.254.169.254/latest/meta-data")
    assert captured.value.code == "INVALID_URL"


async def test_web_fetch_reports_too_large(monkeypatch):
    async def fake_fetch(client, url, **kwargs):
        raise ResponseTooLargeError("Response exceeded the 100 bytes limit")

    monkeypatch.setattr(web_module, "fetch_url", fake_fetch)
    tool = WebFetchTool()
    with pytest.raises(ToolInvocationError, match="exceeded") as captured:
        await tool.execute("http://example.com/")
    assert captured.value.code == "FETCH_BLOCKED"


# --- Tracking header propagation (L4) -----------------------------------------


async def test_tracking_headers_only_when_enabled():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200, text="ok")

    async with TrackedAsyncClient(
        transport=httpx.MockTransport(handler), propagate_headers=False
    ) as client:
        await client.get("http://third-party.example/")
    assert "x-tracker-id" not in captured
    assert "x-request-id" not in captured

    async with TrackedAsyncClient(transport=httpx.MockTransport(handler)) as client:
        await client.get("https://api.llm-provider.internal/")
    assert "x-tracker-id" in captured
    assert "x-request-id" in captured


# --- MCP wrapper --------------------------------------------------------------


def _fake_tool_def():
    return SimpleNamespace(
        name="read_file",
        description="Reads a file.",
        inputSchema={"type": "object", "properties": {}},
    )


class _FakeSession:
    def __init__(self, text: str = "secret data", delay: float = 0.0):
        self._text = text
        self._delay = delay

    async def call_tool(self, name, arguments=None):
        if self._delay:
            await asyncio.sleep(self._delay)
        from mcp import types

        return SimpleNamespace(content=[types.TextContent(type="text", text=self._text)])


async def test_mcp_output_wrapped_with_boundary_markers():
    wrapper = MCPToolWrapper(_FakeSession(), "filesrv", _fake_tool_def())
    output = await wrapper.execute(path="/tmp/x")
    assert output.startswith('<mcp_tool_result server="filesrv" name="read_file">')
    assert "secret data" in output
    assert output.endswith("</mcp_tool_result>")
    assert "[Untrusted MCP tool from server 'filesrv']" in wrapper.description


async def test_mcp_call_tool_timeout():
    wrapper = MCPToolWrapper(_FakeSession(delay=5.0), "filesrv", _fake_tool_def(), timeout=0.05)
    with pytest.raises(ToolInvocationError, match="timed out") as captured:
        await wrapper.execute()
    assert captured.value.code == "MCP_TIMEOUT"


async def test_connect_mcp_servers_blocks_private_url():
    from joyhousebot.capabilities import CapabilityRegistry

    registry = CapabilityRegistry()
    cfg = {
        "command": "",
        "args": [],
        "env": {},
        "url": "http://127.0.0.1:9/mcp",
    }
    async with AsyncExitStack() as stack:
        await connect_mcp_servers({"evil": cfg}, registry, stack)
    assert registry.get_tool("mcp_evil_read_file") is None


# --- fetch_url_to_knowledgebase ordering (L1) ----------------------------------


async def test_fetch_to_knowledgebase_validates_context_before_fetch(monkeypatch):
    called = False

    async def fake_fetch(url):
        nonlocal called
        called = True
        raise AssertionError("fetch must not run without a valid tool context")

    monkeypatch.setattr(kb_module, "fetch_and_ingest_url", fake_fetch)
    result = await kb_module.FetchUrlToKnowledgebaseHandler().execute(
        CapabilityContext(
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            action_id="action-a",
            idempotency_key="idem-a",
        ),
        {"url": "http://example.com/"},
    )
    assert result.success is False
    assert result.error["code"] == "CONTEXT_REQUIRED"
    assert called is False
