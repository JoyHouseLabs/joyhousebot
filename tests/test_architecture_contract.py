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


# --- Import-direction guard -------------------------------------------------
#
# The documented layering is
# ``api -> application -> runtime + domain services -> repositories`` with
# ``contracts``/``domain`` at the bottom.  Packages are assigned a tier below
# (higher tiers may import lower tiers, never the reverse); an import whose
# target sits in a *higher* tier than the source is a layering violation.
# The tiers snapshot the directions that are currently acyclic and healthy —
# e.g. ``runtime`` may use ``orchestration`` but not vice versa, ``agent``
# tools may use ``capabilities`` but ``capabilities`` must not reach back
# into ``agent``, and ``config`` sits high only because it validates provider
# names against ``providers.registry``.

PACKAGE_TIERS = {
    # 0 — foundation: no dependency on any other joyhousebot package.
    "contracts": 0,
    "domain": 0,
    "utils": 0,
    "bus": 0,
    "sandbox": 0,
    # 1 — repositories and domain services.
    "storage": 1,
    "scheduling": 1,
    "orchestration": 1,
    "session": 1,
    "services": 1,
    # 2 — execution runtime.
    "runtime": 2,
    # 3 — capability + provider registries used by the runtime.
    "capabilities": 3,
    "providers": 3,
    # 4 — agent loop, channel adapters, cron facade, runtime-wired config.
    "agent": 4,
    "config": 4,
    "channels": 4,
    "cron": 4,
    # 5 — application use cases.
    "application": 5,
    # 6 — entrypoints.
    "api": 6,
    "bootstrap": 6,
    "cli": 6,
    "__main__": 6,
}

# Known drift, recorded edge by edge (source file -> imported module) so the
# guard passes today but fails on any *new* violation.  Each entry names the
# cycle it belongs to; remove entries as the cycles are eliminated.
KNOWN_LAYER_VIOLATIONS = {
    # domain -> orchestration -> runtime -> domain 环：__post_init__ 里的 deferred import
    ("domain/scenarios/models.py", "joyhousebot.orchestration.aggregation"),
    # runtime <-> capabilities 环：coordinator 依赖 dispatcher
    ("runtime/coordinator.py", "joyhousebot.capabilities.dispatcher"),
    # capabilities <-> agent 环：registry/tool_adapter 依赖 agent.tools.base.Tool
    ("capabilities/registry.py", "joyhousebot.agent.tools.base"),
    ("capabilities/tool_adapter.py", "joyhousebot.agent.tools.base"),
    # orchestration <-> runtime 环：planner/task_graph 反向依赖 runtime.models
    ("orchestration/planner.py", "joyhousebot.runtime.models"),
    ("orchestration/task_graph.py", "joyhousebot.runtime.models"),
    # cron <-> scheduling 环：scheduling 仓储反向依赖 cron.types
    ("scheduling/repository.py", "joyhousebot.cron.types"),
    # services <-> agent 环：retrieval adapter 反向依赖 agent.memory
    ("services/retrieval/adapter.py", "joyhousebot.agent.memory"),
    # storage 越层：仓储层向上依赖 runtime 模型
    ("storage/runtime_store.py", "joyhousebot.runtime.models"),
    ("storage/postgres_runs.py", "joyhousebot.runtime.models"),
    ("storage/postgres_tasks.py", "joyhousebot.runtime.models"),
    # storage 越层：仓储层向上依赖 application 权限模型
    ("storage/postgres_admins.py", "joyhousebot.application.permissions"),
    # storage 越层：仓储层 deferred import bootstrap 默认数据
    ("storage/postgres_agents.py", "joyhousebot.bootstrap.default_agents"),
    ("storage/postgres_capabilities.py", "joyhousebot.bootstrap.default_skills"),
}


def _package_import_edges() -> list[tuple[str, str]]:
    """Collect (source file, imported joyhousebot module) edges via AST.

    ``ast.walk`` deliberately also reaches function-level deferred imports.
    """
    import ast

    edges: list[tuple[str, str]] = []
    package_root = ROOT / "joyhousebot"
    for path in sorted(package_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(package_root)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = list(relative.parts[:-1])
                    if node.level > 1:
                        base = base[: -(node.level - 1)]
                    modules = [".".join([*base, node.module] if node.module else base)]
                else:
                    modules = [node.module] if node.module else []
            else:
                continue
            for module in modules:
                parts = module.split(".") if module else []
                if len(parts) >= 2 and parts[0] == "joyhousebot":
                    edges.append((str(relative), module))
    return edges


def test_internal_imports_follow_layering() -> None:
    violations: list[str] = []
    for source, module in _package_import_edges():
        source_parts = source.split("/")
        source_package = source_parts[0] if len(source_parts) > 1 else "__main__"
        target_package = module.split(".")[1]
        if source_package == target_package:
            continue
        # Unregistered new packages default to the strictest tier so any
        # cross-package import from them fails until a tier is declared.
        source_tier = PACKAGE_TIERS.get(source_package, 0)
        target_tier = PACKAGE_TIERS.get(target_package)
        if target_tier is None or target_tier <= source_tier:
            continue
        if (source, module) in KNOWN_LAYER_VIOLATIONS:
            continue
        violations.append(f"{source} -> {module}")
    assert violations == []

    # Stale exemptions must be removed once the underlying drift is fixed.
    active = {(source, module) for source, module in _package_import_edges()}
    stale = sorted(KNOWN_LAYER_VIOLATIONS - active)
    assert stale == []
