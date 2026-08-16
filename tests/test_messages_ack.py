"""Unit tests for the provider-neutral acknowledgement scope policy."""

from porthouse.channels.messages_ack import should_send_ack

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
