"""Helpers for direct message HTTP endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from joyhousebot.bus.events import OutboundMessage


def prepare_direct_message_send(*, body: Any, app_state: dict[str, Any]) -> tuple[Any, str, str, OutboundMessage]:
    """Validate request and build outbound payload for /message/send."""
    channel = body.channel.strip().lower()
    target = body.target.strip()
    if not channel:
        raise HTTPException(status_code=400, detail="channel is required")
    if not target:
        raise HTTPException(status_code=400, detail="target is required")
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    bus = app_state.get("message_bus")
    channel_manager = app_state.get("channel_manager")
    if bus is None:
        raise HTTPException(status_code=503, detail="Message bus not initialized")
    if channel_manager is None:
        raise HTTPException(status_code=503, detail="Direct outbound requires gateway mode")

    channel_obj = channel_manager.get_channel(channel) if hasattr(channel_manager, "get_channel") else None
    if channel_obj is None:
        raise HTTPException(status_code=404, detail=f"Channel not enabled: {channel}")

    msg = OutboundMessage(
        channel=channel,
        chat_id=target,
        content=body.message,
        reply_to=body.reply_to,
        metadata=body.metadata or {},
    )
    return bus, channel, target, msg


async def publish_direct_message(
    *,
    bus: Any,
    channel: str,
    target: str,
    msg: OutboundMessage,
    message_text: str,
    logger_error: Any,
) -> dict[str, Any]:
    """Publish direct message and return API response payload."""
    try:
        await bus.publish_outbound(msg)
        return {
            "ok": True,
            "queued": True,
            "channel": channel,
            "target": target,
            "message_length": len(message_text),
        }
    except Exception as e:
        logger_error(f"Direct message send error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

