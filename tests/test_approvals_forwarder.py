"""Tests for exec approval forwarder (should_forward, resolve_targets, build_request_message)."""

import pytest

from joyhousebot.approvals.exec_approval_forwarder import (
    build_request_message,
    resolve_forward_targets,
    should_forward_exec_approval,
)


def test_should_forward_disabled():
    class C:
        approvals = None
    assert should_forward_exec_approval(config=C(), payload={"request": {}}) is False

    class C2:
        class Exec:
            enabled = False
        approvals = type("A", (), {"exec": Exec})()

    assert should_forward_exec_approval(config=C2(), payload={"request": {}}) is False


def test_should_forward_enabled_no_filters():
    class Exec:
        enabled = True
    class A:
        exec = Exec()
    class C:
        approvals = A()

    assert should_forward_exec_approval(config=C(), payload={"request": {"sessionKey": "tg:123"}}) is True
    assert should_forward_exec_approval(config=C(), payload={"request": {}}) is True


def test_should_forward_agent_filter():
    class Exec:
        enabled = True
        agent_filter = ["main", "bot"]
    class A:
        exec = Exec()
    class C:
        approvals = A()

    assert should_forward_exec_approval(config=C(), payload={"request": {"agentId": "main"}}) is True
    assert should_forward_exec_approval(config=C(), payload={"request": {"agentId": "other"}}) is False
    assert should_forward_exec_approval(config=C(), payload={"request": {}}) is False


def test_should_forward_session_filter():
    class Exec:
        enabled = True
        session_filter = ["tg:", "discord:.*dm"]
    class A:
        exec = Exec()
    class C:
        approvals = A()

    assert should_forward_exec_approval(config=C(), payload={"request": {"sessionKey": "tg:123"}}) is True
    assert should_forward_exec_approval(config=C(), payload={"request": {"sessionKey": "discord:dm-456"}}) is True
    assert should_forward_exec_approval(config=C(), payload={"request": {"sessionKey": "slack:xyz"}}) is False
    assert should_forward_exec_approval(config=C(), payload={"request": {}}) is False


def test_resolve_targets_session_mode():
    class Exec:
        mode = "session"
        targets = []
    class A:
        exec = Exec()
    class C:
        approvals = A()

    payload = {"request": {"sessionKey": "telegram:chat_123"}}
    assert resolve_forward_targets(config=C(), payload=payload) == [("telegram", "chat_123")]

    payload_no_sk = {"request": {}}
    assert resolve_forward_targets(config=C(), payload=payload_no_sk) == []

    payload_bad_sk = {"request": {"sessionKey": "no-colon"}}
    assert resolve_forward_targets(config=C(), payload=payload_bad_sk) == []


def test_resolve_targets_targets_mode():
    class Exec:
        mode = "targets"
        targets = [
            {"channel": "tg", "to": "user_1"},
            {"channel": "discord", "to": "channel_2"},
        ]
    class A:
        exec = Exec()
    class C:
        approvals = A()

    payload = {"request": {}}
    got = resolve_forward_targets(config=C(), payload=payload)
    assert set(got) == {("tg", "user_1"), ("discord", "channel_2")}


def test_resolve_targets_both_mode():
    class Exec:
        mode = "both"
        targets = [{"channel": "tg", "to": "broadcast"}]
    class A:
        exec = Exec()
    class C:
        approvals = A()

    payload = {"request": {"sessionKey": "tg:user_1"}}
    got = resolve_forward_targets(config=C(), payload=payload)
    assert ("tg", "user_1") in got
    assert ("tg", "broadcast") in got
    assert len(got) == 2


def test_build_request_message():
    payload = {
        "id": "cr_abc123",
        "request": {"command": "ls -la", "cwd": "/tmp", "host": "local"},
        "expiresAtMs": 100_000,
    }
    now_ms = 95_000
    text = build_request_message(payload, now_ms)
    assert "cr_abc123" in text
    assert "ls -la" in text
    assert "/tmp" in text
    assert "local" in text
    assert "5s" in text or "5 s" in text
    assert "/approve" in text
    assert "allow-once" in text
