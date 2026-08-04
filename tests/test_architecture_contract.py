from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_removed_public_stacks_do_not_return() -> None:
    removed = [
        "joyhousebot/api/rpc",
        "joyhousebot/api/http",
        "joyhousebot/gateway",
        "joyhousebot/node",
        "joyhousebot/control_plane",
        "joyhousebot/heartbeat",
        "joyhousebot/identity",
        "joyhousebot/financial",
        "joyhousebot/plugins",
        "joyhousebot/browser",
        "joyhousebot/agent/collaboration",
        "joyhousebot/agent/tools/code_backends",
        "joyhousebot/session/manager.py",
        "joyhousebot/services/agents",
        "joyhousebot/services/knowledge_pipeline",
        "joyhousebot/services/plugins",
        "joyhousebot/services/sessions",
        "joyhousebot/services/skills",
        "joyhousebot/services/tasks",
        "joyhousebot/cli/commands.py",
        "frontend/src/services/gateway-client.ts",
        "frontend/src/composables/useGateway.ts",
        "plugin_host",
        "examples/native-plugins",
        "scripts/rpc_compat_smoke.py",
    ]
    remaining = []
    for relative in removed:
        path = ROOT / relative
        if path.is_file() or (path.is_dir() and any(path.rglob("*.py"))):
            remaining.append(relative)
    assert remaining == []


def test_python_modules_are_bounded() -> None:
    oversized: list[tuple[str, int]] = []
    for path in (ROOT / "joyhousebot").rglob("*.py"):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > 650:
            oversized.append((str(path.relative_to(ROOT)), lines))
    assert oversized == []


def test_cluster_domains_do_not_use_generic_json_state() -> None:
    forbidden = (
        "get_shared_state",
        "set_shared_state",
        "list_shared_state_keys",
        "mutate_shared_state",
    )
    matches: list[str] = []
    for path in (ROOT / "joyhousebot").rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        if any(token in content for token in forbidden):
            matches.append(str(path.relative_to(ROOT)))
    assert matches == []


def test_cluster_repository_files_are_bounded() -> None:
    repository_files = [
        "joyhousebot/scheduling/repository.py",
        "joyhousebot/channels/repository.py",
        "joyhousebot/agent/memory_repository.py",
        "joyhousebot/agent/profile_health_repository.py",
        "joyhousebot/services/retrieval/knowledge_repository.py",
    ]
    oversized = []
    for relative in repository_files:
        lines = len((ROOT / relative).read_text(encoding="utf-8").splitlines())
        if lines > 600:
            oversized.append((relative, lines))
    assert oversized == []


def test_cloud_tool_defaults_fail_closed() -> None:
    from joyhousebot.config.schema import Config

    config = Config()
    assert config.tools.restrict_to_workspace is True
    assert config.tools.exec.container_image
    assert config.tools.optional_allowlist == []

    from joyhousebot.agent.tools.shell import ExecTool

    assert not hasattr(ExecTool, "_execute_direct")
