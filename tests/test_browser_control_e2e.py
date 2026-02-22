"""
E2E tests for local browser control service (real Chromium via Playwright).

Requires: playwright install chromium
Run: pytest tests/test_browser_control_e2e.py -v
Skip: if playwright/chromium unavailable, tests are skipped.
"""

from __future__ import annotations

import pytest

# Skip entire module if playwright not installed
pytest.importorskip("playwright")


@pytest.fixture(scope="module")
def browser_app():
    """Create browser FastAPI app (headless for CI)."""
    from joyhousebot.browser import create_browser_app
    return create_browser_app(headless=True, default_profile="default")


@pytest.fixture
async def client(browser_app):
    """ASGI test client (async)."""
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=browser_app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_browser_status(client):
    """GET / returns status (running false before start)."""
    r = await client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert "enabled" in data
    assert "running" in data
    assert data["profile"] == "default"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_browser_start_tabs_snapshot_stop(client):
    """Start browser -> open tab -> snapshot -> stop (real Chromium)."""
    # Start
    r = await client.post("/start")
    assert r.status_code == 200, r.text

    # Tabs empty until we open
    r = await client.get("/tabs")
    assert r.status_code == 200
    assert r.json().get("running") is True
    assert isinstance(r.json().get("tabs"), list)

    # Open a tab
    r = await client.post("/tabs/open", json={"url": "https://example.com"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert "targetId" in data

    # Snapshot (page tree with refs)
    r = await client.get("/snapshot?maxChars=5000")
    assert r.status_code == 200, r.text
    snap = r.json()
    assert "snapshot" in snap or "format" in snap
    assert snap.get("format") == "ai"
    # Should have some refs if page loaded
    refs = snap.get("refs") or {}
    assert isinstance(refs, dict)

    # Stop (cleanup)
    r = await client.post("/stop")
    assert r.status_code == 200


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_browser_navigate_screenshot(client):
    """Start -> open tab -> navigate -> screenshot -> stop."""
    r = await client.post("/start")
    assert r.status_code == 200
    r = await client.post("/tabs/open", json={"url": "about:blank"})
    assert r.status_code == 200
    tab = r.json()
    target_id = tab.get("targetId")

    r = await client.post("/navigate", json={"url": "https://example.com", "targetId": target_id})
    assert r.status_code == 200

    r = await client.post("/screenshot", json={"targetId": target_id})
    assert r.status_code == 200, r.text
    data = r.json()
    assert "path" in data
    assert data.get("path", "").endswith((".png", ".jpg"))

    r = await client.post("/stop")
    assert r.status_code == 200
