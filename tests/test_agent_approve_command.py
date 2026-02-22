"""Tests for /approve command parsing in AgentLoop."""

import pytest

from joyhousebot.agent.loop import AgentLoop


def test_parse_approve_command_basic():
    assert AgentLoop._parse_approve_command("/approve cr_abc123 allow-once") == ("cr_abc123", "allow-once")
    assert AgentLoop._parse_approve_command("/approve cr_xyz allow-always") == ("cr_xyz", "allow-always")
    assert AgentLoop._parse_approve_command("/approve apr_1 deny") == ("apr_1", "deny")


def test_parse_approve_command_aliases():
    assert AgentLoop._parse_approve_command("/approve id allow") == ("id", "allow-once")
    assert AgentLoop._parse_approve_command("/approve id once") == ("id", "allow-once")
    assert AgentLoop._parse_approve_command("/approve id reject") == ("id", "deny")
    assert AgentLoop._parse_approve_command("/approve id always") == ("id", "allow-always")


def test_parse_approve_command_case_insensitive():
    assert AgentLoop._parse_approve_command("/Approve cr_1 ALLOW-ONCE") == ("cr_1", "allow-once")


def test_parse_approve_command_invalid():
    assert AgentLoop._parse_approve_command("") is None
    assert AgentLoop._parse_approve_command("/approve") is None
    assert AgentLoop._parse_approve_command("/approve cr_1") is None
    assert AgentLoop._parse_approve_command("/approve cr_1 invalid") is None
    assert AgentLoop._parse_approve_command("/other cr_1 allow-once") is None
    assert AgentLoop._parse_approve_command("  /approve cr_1 allow-once  ") == ("cr_1", "allow-once")
