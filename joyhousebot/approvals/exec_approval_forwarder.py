"""Forward exec approval requests to chat (OpenClaw-style)."""

from __future__ import annotations

import re
import time
from typing import Any

from joyhousebot.bus.events import OutboundMessage

# request_id -> list of (channel, chat_id) for resolved/expired notifications
_pending_forward_targets: dict[str, list[tuple[str, str]]] = {}


def should_forward_exec_approval(
    *,
    config: Any,
    payload: dict[str, Any],
) -> bool:
    """True if we should forward this approval request to chat."""
    approvals = getattr(config, "approvals", None) if config else None
    exec_cfg = getattr(approvals, "exec", None) if approvals else None
    if not exec_cfg or not getattr(exec_cfg, "enabled", False):
        return False
    request = payload.get("request")
    if not isinstance(request, dict):
        return False
    agent_filter = getattr(exec_cfg, "agent_filter", None) or getattr(exec_cfg, "agentFilter", None)
    if agent_filter:
        agent_id = (request.get("agentId") or "").strip()
        if not agent_id:
            return False
        if agent_id not in list(agent_filter):
            return False
    session_filter = getattr(exec_cfg, "session_filter", None) or getattr(exec_cfg, "sessionFilter", None)
    if session_filter:
        session_key = (request.get("sessionKey") or "").strip()
        if not session_key:
            return False
        matched = False
        for pattern in session_filter:
            try:
                if pattern in session_key or re.search(pattern, session_key):
                    matched = True
                    break
            except re.error:
                if pattern in session_key:
                    matched = True
                    break
        if not matched:
            return False
    return True


def _parse_session_target(session_key: str) -> tuple[str, str] | None:
    """Parse session_key (channel:chat_id) into (channel, chat_id). Returns None if invalid."""
    sk = (session_key or "").strip()
    if ":" not in sk:
        return None
    idx = sk.index(":")
    channel = sk[:idx].strip()
    chat_id = sk[idx + 1 :].strip()
    if not channel or not chat_id:
        return None
    return (channel, chat_id)


def resolve_forward_targets(
    *,
    config: Any,
    payload: dict[str, Any],
) -> list[tuple[str, str]]:
    """Resolve (channel, chat_id) list for delivery. Uses session and/or config targets."""
    approvals = getattr(config, "approvals", None) if config else None
    exec_cfg = getattr(approvals, "exec", None) if approvals else None
    if not exec_cfg:
        return []
    mode = (getattr(exec_cfg, "mode", None) or "session").strip().lower()
    if mode not in ("session", "targets", "both"):
        mode = "session"
    request = payload.get("request")
    if not isinstance(request, dict):
        request = {}
    targets: list[tuple[str, str]] = []
    seen: set[str] = set()

    if mode in ("session", "both"):
        session_key = request.get("sessionKey") or ""
        parsed = _parse_session_target(str(session_key))
        if parsed:
            key = f"{parsed[0]}:{parsed[1]}"
            if key not in seen:
                seen.add(key)
                targets.append(parsed)

    if mode in ("targets", "both"):
        raw_targets = getattr(exec_cfg, "targets", None) or []
        for t in raw_targets:
            if isinstance(t, dict):
                ch = (t.get("channel") or "").strip()
                to = (t.get("to") or "").strip()
            else:
                ch = (getattr(t, "channel", None) or "").strip()
                to = (getattr(t, "to", None) or "").strip()
            if ch and to:
                key = f"{ch}:{to}"
                if key not in seen:
                    seen.add(key)
                    targets.append((ch, to))

    return targets


def build_request_message(payload: dict[str, Any], now_ms: int) -> str:
    """Build the approval request text (OpenClaw buildRequestMessage style)."""
    req_id = payload.get("id") or ""
    request = payload.get("request") or {}
    expires_at = int(payload.get("expiresAtMs") or 0)
    expires_in = max(0, (expires_at - now_ms) // 1000)

    command = (request.get("command") or "").strip()
    if "\n" in command or "`" in command:
        cmd_display = f"```\n{command}\n```"
    else:
        cmd_display = f"`{command}`" if command else ""

    lines = [
        "Exec approval required",
        f"ID: {req_id}",
        f"Command: {cmd_display}" if cmd_display else "Command: (none)",
    ]
    cwd = request.get("cwd")
    if cwd:
        lines.append(f"CWD: {cwd}")
    host = request.get("host")
    if host:
        lines.append(f"Host: {host}")
    agent_id = request.get("agentId")
    if agent_id:
        lines.append(f"Agent: {agent_id}")
    security = request.get("security")
    if security:
        lines.append(f"Security: {security}")
    ask = request.get("ask")
    if ask:
        lines.append(f"Ask: {ask}")
    lines.append(f"Expires in: {expires_in}s")
    lines.append("Reply with: /approve <id> allow-once|allow-always|deny")
    return "\n".join(lines)


def build_resolved_message(payload: dict[str, Any]) -> str:
    """Build the approval resolved notification text."""
    req_id = payload.get("id") or ""
    decision = (payload.get("decision") or "").strip().lower()
    resolved_by = (payload.get("resolvedBy") or "").strip()
    if decision == "allow-once":
        label = "allowed once"
    elif decision == "allow-always":
        label = "allowed always"
    else:
        label = "denied"
    base = f"Exec approval {label}."
    if resolved_by:
        base += f" Resolved by {resolved_by}."
    return f"{base} ID: {req_id}"


def build_expired_message(payload: dict[str, Any]) -> str:
    """Build the approval expired notification text."""
    req_id = payload.get("id") or ""
    return f"Exec approval expired. ID: {req_id}"


async def handle_exec_approval_requested(app_state: dict[str, Any], payload: dict[str, Any]) -> None:
    """Called after exec.approval.requested broadcast. Forwards to chat targets and stores them for resolved."""
    config = app_state.get("config")
    bus = app_state.get("message_bus")
    if not bus:
        return
    if not should_forward_exec_approval(config=config, payload=payload):
        return
    targets = resolve_forward_targets(config=config, payload=payload)
    if not targets:
        return
    now_ms = int(time.time() * 1000)
    content = build_request_message(payload, now_ms)
    req_id = (payload.get("id") or "").strip()
    if req_id:
        _pending_forward_targets[req_id] = list(targets)
    for channel, chat_id in targets:
        try:
            await bus.publish_outbound(OutboundMessage(channel=channel, chat_id=chat_id, content=content))
        except Exception:
            pass


async def handle_exec_approval_resolved(app_state: dict[str, Any], payload: dict[str, Any]) -> None:
    """Called after exec.approval.resolved. Sends a short resolved notice to the same targets we forwarded to."""
    bus = app_state.get("message_bus")
    if not bus:
        return
    req_id = (payload.get("id") or "").strip()
    targets = _pending_forward_targets.pop(req_id, []) if req_id else []
    if not targets:
        return
    content = build_resolved_message(payload)
    for channel, chat_id in targets:
        try:
            await bus.publish_outbound(OutboundMessage(channel=channel, chat_id=chat_id, content=content))
        except Exception:
            pass
