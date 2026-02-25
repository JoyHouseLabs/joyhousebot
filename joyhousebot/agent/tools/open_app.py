"""Open App tool: Agent-only routing to open a domain app (App-first contract)."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from joyhousebot.agent.tools.base import Tool


class OpenAppTool(Tool):
    """
    Open a domain application by id and optional route/params.
    Agent uses this only for routing; business logic runs inside the App.
    """

    @property
    def name(self) -> str:
        return "open_app"

    @property
    def description(self) -> str:
        return (
            "Open a domain application for the user. Use this when the user wants to use "
            "an installed app. Do not perform domain actions yourself; open the app and let "
            "the user do it there. Parameters: app_id (required, e.g. 'my_app'), route "
            "(optional, app-internal path like '/add'), params (optional object for the app)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "app_id": {
                    "type": "string",
                    "description": "Application ID (e.g. from installed apps). Required.",
                },
                "route": {
                    "type": "string",
                    "description": "Optional in-app route or anchor, e.g. '/add', '#search'.",
                },
                "params": {
                    "type": "object",
                    "description": "Optional key-value context for the app (e.g. intent, prefill).",
                },
            },
            "required": ["app_id"],
        }

    async def execute(
        self,
        app_id: str,
        route: str | None = None,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        app_id = (app_id or "").strip()
        if not app_id:
            return json.dumps({
                "ok": False,
                "error": {"code": "INVALID_REQUEST", "message": "app_id is required"},
            }, ensure_ascii=False)
        route = (route or "").strip() or None
        params = params if isinstance(params, dict) else {}
        navigate_to = f"/app/{app_id}"
        app_link = f"/plugins-apps/{app_id}/index.html"
        if route:
            if route.startswith("#"):
                navigate_to += route
                app_link += route
            else:
                navigate_to += "?route=" + quote(route)
                app_link += "?route=" + quote(route)
        out = {
            "ok": True,
            "app_id": app_id,
            "route": route,
            "params": params,
            "navigate_to": navigate_to,
            "app_link": app_link,
        }
        return json.dumps(out, ensure_ascii=False)
