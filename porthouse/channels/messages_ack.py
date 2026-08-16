"""Provider-neutral acknowledgement scope policy for Channel extensions."""

from __future__ import annotations

# Default emoji when ack_reaction is not set.
DEFAULT_ACK_REACTION = "\U0001f440"  # 👀

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
