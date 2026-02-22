"""Local browser control service (OpenClaw-compatible HTTP API) using Playwright."""

from joyhousebot.browser.server import create_browser_app, get_browser_state

__all__ = ["create_browser_app", "get_browser_state"]
