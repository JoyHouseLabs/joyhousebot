"""Shared permission-grant matching semantics (joyhousebot.utils.permissions).

Every enforcement point (Principal.can, capability dispatcher, tool/plugin
registries, MCP gateway) uses these helpers; the semantics pinned here are
the contract for all of them. Everything is fail-closed.
"""

from joyhousebot.application.context import Principal
from joyhousebot.utils.permissions import (
    missing_permissions,
    permission_granted,
    permissions_satisfied,
)


def test_exact_match_only_for_plain_grants():
    assert permission_granted("runs.read", "runs.read")
    assert not permission_granted("runs.read", "runs.write")
    assert not permission_granted("runs.read.extra", "runs.read")


def test_global_wildcard_grants_everything():
    assert permission_granted("*", "anything.at.all")
    assert permission_granted("*", "runs")


def test_namespace_wildcard_matches_inside_namespace_only():
    assert permission_granted("runs.*", "runs.read")
    assert permission_granted("runs.*", "runs.cancel")
    # siblings that merely share a string prefix do not match
    assert not permission_granted("runs.*", "runsx.read")
    # the bare namespace name itself is not granted
    assert not permission_granted("runs.*", "runs")


def test_fail_closed_on_empty_or_odd_grants():
    assert not permission_granted("", "runs.read")
    assert not permission_granted("  ", "runs.read")
    assert not permission_granted("runs.read", "")
    assert not permission_granted(".*", "runs.read")


def test_missing_permissions_is_and_semantics():
    assert missing_permissions(["runs.read"], ["runs.read", "runs.cancel"]) == ["runs.cancel"]
    assert missing_permissions(["runs.*"], ["runs.read", "runs.cancel"]) == []
    assert missing_permissions(["*"], ["runs.read", "agents.publish"]) == []
    assert missing_permissions([], []) == []
    assert missing_permissions([], ["runs.read"]) == ["runs.read"]


def test_permissions_satisfied():
    assert permissions_satisfied(["runs.*"], ["runs.read", "runs.cancel"])
    assert not permissions_satisfied(["runs.read"], ["runs.read", "runs.cancel"])
    assert permissions_satisfied([], [])


def test_principal_can_uses_shared_semantics():
    ns = Principal(subject="s", user_id="u", permissions=("runs.*",))
    assert ns.can("runs.read")
    assert not ns.can("runsx.read")
    assert not ns.can("agents.publish")

    exact = Principal(subject="s", user_id="u", permissions=("agents.publish",))
    assert exact.can("agents.publish")
    assert not exact.can("agents.read")

    star = Principal(subject="s", user_id="u", permissions=("*",))
    assert star.can("anything")

    none = Principal(subject="s", user_id="u")
    assert not none.can("runs.read")

    operator = Principal(subject="s", user_id="u", role="operator")
    assert operator.can("anything")
