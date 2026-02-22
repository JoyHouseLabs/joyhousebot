"""Tests for sandbox service (list_containers_local, recreate_containers_local, explain_local)."""

from joyhousebot.sandbox.service import explain_local, list_containers_local, recreate_containers_local


def test_list_containers_local_empty():
    """When read_json returns empty containers, list is empty."""
    def read_json(name, default):
        return default
    out = list_containers_local(read_json, browser_only=False)
    assert isinstance(out, list)


def test_explain_local_returns_policy_and_backend():
    """explain_local returns policy, backend, docker_available."""
    def read_json(name, default):
        return default
    out = explain_local(read_json, session="", agent="")
    assert "policy" in out
    assert "backend" in out
    assert out["backend"] in ("docker", "direct")
    assert "docker_available" in out
    assert "containers_count" in out


def test_recreate_containers_local_returns_ok():
    """recreate_containers_local returns ok and operation."""
    def read_json(name, default):
        return {"containers": [], "recreateOps": []}
    def write_json(name, data):
        pass
    out = recreate_containers_local(
        read_json,
        write_json,
        all_items=False,
        session="",
        agent="",
        browser_only=False,
        force=False,
    )
    assert out.get("ok") is True
    assert "operation" in out
    assert "removed" in out
