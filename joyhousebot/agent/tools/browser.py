"""Browser control tool (OpenClaw-compatible: status, snapshot, act, navigate, etc.)."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from joyhousebot.agent.tools.base import Tool


class BrowserTool(Tool):
    """
    Control the browser via gateway browser.request (local service or node proxy).
    Use snapshot to get page structure with refs, then act with kind/ref to click, type, etc.
    """

    def __init__(
        self,
        browser_request_runner: Callable[..., Awaitable[Any]] | None = None,
    ):
        self._runner = browser_request_runner

    @property
    def name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Control the browser: status, start, stop, profiles, tabs, open, focus, close, "
            "snapshot (get page structure and refs), screenshot, navigate, console, pdf, act (click, type, hover, etc.). "
            "Prefer snapshot over screenshot: use snapshot first to get page structure and numeric refs, then act with kind and ref to interact. "
            "Use screenshot only when you need visual layout. Refs are numeric (e.g. 1, 2)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Action: status, start, stop, profiles, tabs, open, focus, close, snapshot, screenshot, navigate, console, pdf, act",
                    "enum": [
                        "status", "start", "stop", "profiles", "tabs", "open", "focus", "close",
                        "snapshot", "screenshot", "navigate", "console", "pdf", "act",
                    ],
                },
                "profile": {"type": "string", "description": "Browser profile name"},
                "targetId": {"type": "string", "description": "Tab target id"},
                "targetUrl": {"type": "string", "description": "URL for open/navigate"},
                "snapshotFormat": {"type": "string", "description": "ai or aria"},
                "maxChars": {"type": "integer", "description": "Max chars for snapshot"},
                "fullPage": {"type": "boolean", "description": "Full page screenshot"},
                "request": {
                    "type": "object",
                    "description": "For action=act: { kind, ref, text, ... }",
                    "properties": {
                        "kind": {"type": "string", "enum": ["click", "type", "press", "hover", "close"]},
                        "ref": {"type": "string"},
                        "text": {"type": "string"},
                        "key": {"type": "string"},
                        "doubleClick": {"type": "boolean"},
                        "submit": {"type": "boolean"},
                    },
                },
                "timeoutMs": {"type": "integer", "description": "Request timeout ms"},
            },
            "required": ["action"],
        }

    async def execute(self, **kwargs: Any) -> str:
        if not self._runner:
            return "Error: Browser control is not available (no runner configured)."
        action = (kwargs.get("action") or "").strip().lower()
        if not action:
            return "Error: action is required."
        timeout_ms = int(kwargs.get("timeoutMs") or 30000)
        profile = (kwargs.get("profile") or "").strip()
        target_id = (kwargs.get("targetId") or "").strip()
        target_url = (kwargs.get("targetUrl") or "").strip()

        method = "GET"
        path = "/"
        query: dict[str, Any] = {}
        body: Any = None

        if action == "status":
            method, path = "GET", "/"
        elif action == "start":
            method, path = "POST", "/start"
        elif action == "stop":
            method, path = "POST", "/stop"
        elif action == "profiles":
            method, path = "GET", "/profiles"
        elif action == "tabs":
            method, path = "GET", "/tabs"
            if profile:
                query["profile"] = profile
        elif action == "open":
            if not target_url:
                return "Error: targetUrl is required for open."
            method, path = "POST", "/tabs/open"
            body = {"url": target_url}
            if profile:
                query["profile"] = profile
        elif action == "focus":
            if not target_id:
                return "Error: targetId is required for focus."
            method, path = "POST", "/tabs/focus"
            body = {"targetId": target_id}
        elif action == "close":
            if target_id:
                method, path = "DELETE", f"/tabs/{target_id}"
            else:
                method, path = "POST", "/act"
                body = {"kind": "close"}
        elif action == "snapshot":
            method, path = "GET", "/snapshot"
            if profile:
                query["profile"] = profile
            if target_id:
                query["targetId"] = target_id
            if kwargs.get("maxChars"):
                query["maxChars"] = int(kwargs["maxChars"])
            if kwargs.get("snapshotFormat"):
                query["format"] = str(kwargs["snapshotFormat"]).strip()
        elif action == "screenshot":
            method, path = "POST", "/screenshot"
            body = {"targetId": target_id or None, "fullPage": bool(kwargs.get("fullPage"))}
            if profile:
                query["profile"] = profile
        elif action == "navigate":
            if not target_url:
                return "Error: targetUrl is required for navigate."
            method, path = "POST", "/navigate"
            body = {"url": target_url, "targetId": target_id or None}
        elif action == "console":
            method, path = "GET", "/console"
            if target_id:
                query["targetId"] = target_id
        elif action == "pdf":
            method, path = "POST", "/pdf"
            body = {"targetId": target_id or None}
        elif action == "act":
            req = kwargs.get("request") or {}
            if not isinstance(req, dict):
                return "Error: request must be an object for action=act."
            kind = (req.get("kind") or "").strip().lower()
            if not kind:
                return "Error: request.kind is required for action=act."
            method, path = "POST", "/act"
            body = {"kind": kind, "targetId": target_id or None}
            if "ref" in req:
                body["ref"] = str(req["ref"]).strip()
            if "text" in req:
                body["text"] = str(req["text"])
            if "key" in req:
                body["key"] = str(req["key"]).strip()
            if "doubleClick" in req:
                body["doubleClick"] = bool(req["doubleClick"])
            if "submit" in req:
                body["submit"] = bool(req["submit"])
        else:
            return f"Error: unknown action {action!r}."

        if profile and "profile" not in query:
            query["profile"] = profile

        try:
            result = await self._runner(
                method=method,
                path=path,
                query=query or None,
                body=body,
                timeout_ms=timeout_ms,
            )
        except Exception as e:
            return f"Error: {e!s}"

        if isinstance(result, dict) and "error" in result:
            return f"Error: {result.get('error', result)}"
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, indent=2)
