"""Canvas tool (OpenClaw-compatible): present, hide, navigate, eval, snapshot, a2ui_push, a2ui_reset via node.invoke."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable

from joyhousebot.agent.tools.base import Tool
from joyhousebot.node import NodeInvokeResult


class CanvasTool(Tool):
    """
    Control node canvases via gateway node.invoke (present/hide/navigate/eval/snapshot/A2UI).
    Use snapshot to capture the rendered UI. Requires at least one connected node.
    """

    def __init__(
        self,
        node_invoke_runner: Callable[..., Awaitable[NodeInvokeResult]] | None = None,
    ):
        self._runner = node_invoke_runner

    @property
    def name(self) -> str:
        return "canvas"

    @property
    def description(self) -> str:
        return (
            "Control node canvases: present, hide, navigate, eval, snapshot, a2ui_push, a2ui_reset. "
            "Use snapshot to capture the rendered UI. Pass node (id or display name) to target a specific node; "
            "omit to use the first connected node."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Action: present, hide, navigate, eval, snapshot, a2ui_push, a2ui_reset",
                    "enum": [
                        "present", "hide", "navigate", "eval", "snapshot",
                        "a2ui_push", "a2ui_reset",
                    ],
                },
                "node": {"type": "string", "description": "Node ID or display name (optional; default: first connected)"},
                "timeoutMs": {"type": "integer", "description": "Request timeout in ms"},
                "target": {"type": "string", "description": "For present: URL or path"},
                "x": {"type": "number", "description": "For present: placement x"},
                "y": {"type": "number", "description": "For present: placement y"},
                "width": {"type": "number", "description": "For present: placement width"},
                "height": {"type": "number", "description": "For present: placement height"},
                "url": {"type": "string", "description": "For navigate: URL to load"},
                "javaScript": {"type": "string", "description": "For eval: JavaScript to run"},
                "outputFormat": {"type": "string", "description": "For snapshot: png or jpeg", "enum": ["png", "jpg", "jpeg"]},
                "maxWidth": {"type": "integer", "description": "For snapshot: max width"},
                "quality": {"type": "number", "description": "For snapshot: JPEG quality"},
                "delayMs": {"type": "integer", "description": "For snapshot: delay before capture"},
                "jsonl": {"type": "string", "description": "For a2ui_push: JSONL content"},
                "jsonlPath": {"type": "string", "description": "For a2ui_push: path to JSONL file"},
            },
            "required": ["action"],
        }

    async def execute(self, **kwargs: Any) -> str:
        if not self._runner:
            return "Error: Canvas control is not available (no node invoke runner configured)."
        action = (kwargs.get("action") or "").strip().lower()
        if not action:
            return "Error: action is required."
        timeout_ms = int(kwargs.get("timeoutMs") or 30000)
        node = (kwargs.get("node") or "").strip() or None

        async def invoke(command: str, params: dict[str, Any] | None = None) -> NodeInvokeResult:
            return await self._runner(
                node_id_or_name=node,
                command=command,
                params=params,
                timeout_ms=timeout_ms,
            )

        if action == "present":  # noqa: SIM102
            params: dict[str, Any] = {}
            if kwargs.get("target"):
                params["url"] = (kwargs.get("target") or "").strip()
            placement: dict[str, Any] = {}
            if "x" in kwargs and kwargs["x"] is not None:
                placement["x"] = kwargs["x"]
            if "y" in kwargs and kwargs["y"] is not None:
                placement["y"] = kwargs["y"]
            if "width" in kwargs and kwargs["width"] is not None:
                placement["width"] = kwargs["width"]
            if "height" in kwargs and kwargs["height"] is not None:
                placement["height"] = kwargs["height"]
            if placement:
                params["placement"] = placement
            result = await invoke("canvas.present", params if params else None)
        elif action == "hide":
            result = await invoke("canvas.hide", None)
        elif action == "navigate":
            url = (kwargs.get("url") or "").strip()
            if not url:
                return "Error: url is required for navigate."
            result = await invoke("canvas.navigate", {"url": url})
        elif action == "eval":
            js = (kwargs.get("javaScript") or "").strip()
            if not js:
                return "Error: javaScript is required for eval."
            result = await invoke("canvas.eval", {"javaScript": js})
            if result.ok and result.payload and isinstance(result.payload, dict):
                res = result.payload.get("result")
                if res is not None:
                    return str(res)
            if not result.ok and result.error:
                return f"Error: {result.error.get('message', 'eval failed')}"
            return json.dumps(result.payload) if result.payload is not None else "{}"
        elif action == "snapshot":
            fmt = (kwargs.get("outputFormat") or "png").strip().lower()
            if fmt in ("jpg", "jpeg"):
                fmt = "jpeg"
            else:
                fmt = "png"
            params = {"format": fmt}
            if kwargs.get("maxWidth") is not None:
                params["maxWidth"] = int(kwargs["maxWidth"])
            if kwargs.get("quality") is not None:
                params["quality"] = float(kwargs["quality"])
            if kwargs.get("delayMs") is not None:
                params["delayMs"] = int(kwargs["delayMs"])
            result = await invoke("canvas.snapshot", params)
            if not result.ok:
                return f"Error: {result.error.get('message', 'snapshot failed') if result.error else 'snapshot failed'}"
            if not result.payload or not isinstance(result.payload, dict):
                return "Error: snapshot returned no payload."
            base64_data = result.payload.get("base64")
            ext = "jpg" if fmt == "jpeg" else "png"
            if base64_data:
                try:
                    import base64 as b64
                    data = b64.b64decode(base64_data)
                    fd, path = tempfile.mkstemp(suffix=f".{ext}", prefix="joyhousebot-canvas-snapshot-")
                    try:
                        Path(path).write_bytes(data)
                        return json.dumps({"ok": True, "path": path, "format": fmt})
                    finally:
                        os.close(fd)
                except Exception as e:
                    return f"Error: failed to save snapshot: {e}"
            return json.dumps({"ok": True, "payload": result.payload})
        elif action == "a2ui_push":
            jsonl = (kwargs.get("jsonl") or "").strip()
            jsonl_path = (kwargs.get("jsonlPath") or "").strip()
            if jsonl_path and not jsonl:
                try:
                    jsonl = Path(jsonl_path).read_text(encoding="utf-8")
                except OSError as e:
                    return f"Error: cannot read jsonlPath: {e}"
            if not jsonl:
                return "Error: jsonl or jsonlPath is required for a2ui_push."
            result = await invoke("canvas.a2ui.pushJSONL", {"jsonl": jsonl})
        elif action == "a2ui_reset":
            result = await invoke("canvas.a2ui.reset", None)
        else:
            return f"Error: unknown action {action!r}."

        if not result.ok:
            msg = result.error.get("message", "command failed") if result.error else "command failed"
            return f"Error: {msg}"
        return json.dumps({"ok": True})
