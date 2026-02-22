"""Shared browser process state (single profile, multiple pages)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

# Playwright types (lazy import to avoid requiring playwright at import time)
PLAYWRIGHT_BROWSER: Any = None
PLAYWRIGHT_CONTEXT: Any = None


@dataclass
class TabInfo:
    """One tab (page) with stable id."""
    target_id: str
    page: Any  # playwright.async_api.Page
    url: str = ""


@dataclass
class BrowserState:
    """Global state for the local browser control service."""
    browser: Any = None  # playwright.async_api.Browser
    context: Any = None  # playwright.async_api.BrowserContext
    _playwright: Any = None  # playwright.async_api.Playwright
    tabs: list[TabInfo] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    profile_name: str = "default"
    running: bool = False

    async def ensure_browser(self, *, executable_path: str = "", headless: bool = False) -> None:
        """Launch browser if not running."""
        if self.browser and self.running:
            return
        async with self._lock:
            if self.browser and self.running:
                return
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            launch_opts: dict[str, Any] = {"headless": headless}
            if executable_path and executable_path.strip():
                launch_opts["executable_path"] = executable_path.strip()
            self.browser = await self._playwright.chromium.launch(**launch_opts)
            self.context = await self.browser.new_context()
            self.tabs = []
            self.running = True

    async def stop(self) -> None:
        """Close browser and clear state."""
        async with self._lock:
            self.running = False
            if self.context:
                await self.context.close()
                self.context = None
            if self.browser:
                await self.browser.close()
                self.browser = None
            self.tabs = []
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None

    def get_page(self, target_id: str | None = None) -> Any | None:
        """Return page for target_id, or first/last page if None."""
        if not self.tabs:
            return None
        if target_id:
            for t in self.tabs:
                if t.target_id == target_id:
                    return t.page
            return None
        return self.tabs[-1].page

    def get_tab_info(self, target_id: str | None = None) -> TabInfo | None:
        if not self.tabs:
            return None
        if target_id:
            for t in self.tabs:
                if t.target_id == target_id:
                    return t
            return None
        return self.tabs[-1]

    async def add_tab(self, url: str = "about:blank") -> TabInfo:
        """Open a new tab and return its info."""
        async with self._lock:
            if not self.context:
                raise RuntimeError("browser not started")
            page = await self.context.new_page()
            target_id = f"page-{id(page)}"
            tab = TabInfo(target_id=target_id, page=page, url=url)
            self.tabs.append(tab)
            if url and url != "about:blank":
                await page.goto(url, wait_until="domcontentloaded")
                tab.url = page.url
            return tab

    async def focus_tab(self, target_id: str) -> None:
        """Bring tab to front (activate its page)."""
        tab = self.get_tab_info(target_id)
        if not tab:
            raise ValueError(f"tab not found: {target_id}")
        await tab.page.bring_to_front()

    async def close_tab(self, target_id: str) -> None:
        """Close one tab."""
        async with self._lock:
            for i, t in enumerate(self.tabs):
                if t.target_id == target_id:
                    await t.page.close()
                    self.tabs.pop(i)
                    return
        raise ValueError(f"tab not found: {target_id}")


_global_state: BrowserState | None = None


def get_browser_state() -> BrowserState:
    """Return the global browser state (create if needed)."""
    global _global_state
    if _global_state is None:
        _global_state = BrowserState()
    return _global_state
