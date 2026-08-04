"""Shared logic for messages config: ack reaction scope and default emoji."""

from __future__ import annotations

# Default emoji when ack_reaction is not set.
DEFAULT_ACK_REACTION = "\U0001f440"  # 👀

# Slack uses short names for standard emoji (reactions.add name=)
SLACK_EMOJI_ALIASES: dict[str, str] = {
    "\U0001f440": "eyes",  # 👀
    "\U0001f44d": "+1",  # 👍
    "\U0001f44e": "-1",  # 👎
}


def ack_emoji_for_slack(emoji: str | None) -> str:
    """Return Slack reaction name (e.g. 'eyes') for the given emoji string."""
    if not emoji or not emoji.strip():
        return "eyes"
    e = (emoji or "").strip()
    return SLACK_EMOJI_ALIASES.get(e, e)


def should_send_ack(
    scope: str | None,
    is_direct: bool,
    is_mention: bool,
) -> bool:
    """
    Whether to send an ack reaction for this inbound message given scope and context.
    Applies the configured acknowledgement scope.
    """
    if not scope or scope.strip() == "off":
        return False
    scope = scope.strip().lower()
    if scope == "all":
        return True
    if scope == "direct":
        return is_direct
    if scope == "group-all":
        return not is_direct
    if scope == "group-mentions":
        return not is_direct and is_mention
    return False
