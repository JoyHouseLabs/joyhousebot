"""Process tool: list processes and terminate by PID (OpenClaw-aligned process management)."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from typing import Any

from joyhousebot.agent.tools.base import Tool


class ProcessTool(Tool):
    """
    List running processes or terminate a process by PID.
    Subject to optional_allowlist; enable only when needed.
    """

    @property
    def name(self) -> str:
        return "process"

    @property
    def description(self) -> str:
        return (
            "Process management: list running processes or terminate one by PID. "
            "Use action=list to see processes (optional query to filter by name); "
            "use action=kill with pid to send SIGTERM. Only list/kill when explicitly needed."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "kill"],
                    "description": "list: show processes; kill: terminate by pid",
                },
                "query": {
                    "type": "string",
                    "description": "Optional filter (substring match on command line) when action=list",
                },
                "pid": {
                    "type": "integer",
                    "description": "Process ID to terminate (required when action=kill)",
                    "minimum": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max lines when action=list (default 50)",
                    "minimum": 1,
                    "maximum": 200,
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        query: str | None = None,
        pid: int | None = None,
        limit: int = 50,
        **kwargs: Any,
    ) -> str:
        action = (action or "").strip().lower()
        if action not in ("list", "kill"):
            return "Error: action must be 'list' or 'kill'"

        if action == "kill":
            if pid is None:
                return "Error: pid is required when action=kill"
            if pid < 1:
                return "Error: pid must be a positive integer"
            if sys.platform == "win32":
                proc = await asyncio.create_subprocess_shell(
                    f"taskkill /PID {pid} /T",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    err = (stderr or b"").decode("utf-8", errors="replace").strip()
                    return f"Error: taskkill failed (exit {proc.returncode}). {err}"
                return f"Terminated process {pid}."
            try:
                os.kill(pid, signal.SIGTERM)
                return f"Sent SIGTERM to process {pid}."
            except ProcessLookupError:
                return f"Error: process {pid} not found."
            except PermissionError:
                return f"Error: no permission to terminate process {pid}."
            except Exception as e:
                return f"Error: {e}"

        # action == "list"
        limit = max(1, min(200, int(limit)))
        if sys.platform == "win32":
            proc = await asyncio.create_subprocess_shell(
                f'tasklist /FO CSV /NH',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            out = (stdout or b"").decode("utf-8", errors="replace")
            if stderr:
                out += "\n" + stderr.decode("utf-8", errors="replace")
        else:
            proc = await asyncio.create_subprocess_exec(
                "ps", "aux",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            out = (stdout or b"").decode("utf-8", errors="replace")
            if stderr:
                out += "\n" + stderr.decode("utf-8", errors="replace")

        lines = [l for l in out.splitlines() if l.strip()]
        if query:
            q = query.strip().lower()
            lines = [l for l in lines if q in l.lower()]
        if len(lines) > limit:
            lines = lines[:limit]
            lines.append(f"... (showing first {limit} lines)")
        return "\n".join(lines) if lines else "(no matching processes)"
