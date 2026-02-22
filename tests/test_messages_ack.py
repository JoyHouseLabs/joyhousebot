"""Unit tests for channels.messages_ack: should_send_ack and ack_emoji_for_slack."""

from joyhousebot.channels.messages_ack import (
    DEFAULT_ACK_REACTION,
    ack_emoji_for_slack,
    should_send_ack,
)


# --- should_send_ack ---


def test_should_send_ack_off_or_empty() -> None:
    assert should_send_ack(None, True, True) is False
    assert should_send_ack("", True, True) is False
    assert should_send_ack("  ", True, True) is False
    assert should_send_ack("off", True, True) is False
    assert should_send_ack("OFF", True, True) is False


def test_should_send_ack_all() -> None:
    assert should_send_ack("all", True, True) is True
    assert should_send_ack("all", True, False) is True
    assert should_send_ack("all", False, True) is True
    assert should_send_ack("all", False, False) is True
    assert should_send_ack("  ALL  ", False, False) is True


def test_should_send_ack_direct() -> None:
    assert should_send_ack("direct", True, True) is True
    assert should_send_ack("direct", True, False) is True
    assert should_send_ack("direct", False, True) is False
    assert should_send_ack("direct", False, False) is False


def test_should_send_ack_group_all() -> None:
    assert should_send_ack("group-all", False, True) is True
    assert should_send_ack("group-all", False, False) is True
    assert should_send_ack("group-all", True, True) is False
    assert should_send_ack("group-all", True, False) is False


def test_should_send_ack_group_mentions() -> None:
    assert should_send_ack("group-mentions", False, True) is True
    assert should_send_ack("group-mentions", False, False) is False
    assert should_send_ack("group-mentions", True, True) is False
    assert should_send_ack("group-mentions", True, False) is False
    assert should_send_ack("  GROUP-MENTIONS  ", False, True) is True


def test_should_send_ack_unknown_scope_returns_false() -> None:
    assert should_send_ack("unknown", True, True) is False
    assert should_send_ack("group-only", False, True) is False


# --- ack_emoji_for_slack ---


def test_ack_emoji_for_slack_empty_defaults_to_eyes() -> None:
    assert ack_emoji_for_slack(None) == "eyes"
    assert ack_emoji_for_slack("") == "eyes"
    assert ack_emoji_for_slack("   ") == "eyes"


def test_ack_emoji_for_slack_unicode_aliases() -> None:
    assert ack_emoji_for_slack(DEFAULT_ACK_REACTION) == "eyes"
    assert ack_emoji_for_slack("\U0001f44d") == "+1"
    assert ack_emoji_for_slack("\U0001f44e") == "-1"


def test_ack_emoji_for_slack_whitespace_stripped() -> None:
    assert ack_emoji_for_slack("  \U0001f440  ") == "eyes"


def test_ack_emoji_for_slack_unknown_returns_as_is() -> None:
    assert ack_emoji_for_slack("custom_emoji") == "custom_emoji"
    assert ack_emoji_for_slack("thumbsup") == "thumbsup"
