import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_core_default_dependencies_exclude_channel_vendor_sdks() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependencies = {str(item).split("[", 1)[0].split(">", 1)[0] for item in project["dependencies"]}
    assert dependencies.isdisjoint(
        {
            "dingtalk-stream",
            "lark-oapi",
            "python-telegram-bot",
            "qq-botpy",
            "slack-sdk",
            "websockets",
        }
    )


def test_runtime_store_does_not_depend_on_product_database_markers() -> None:
    source = (ROOT / "joyhousebot/storage/postgres_store.py").read_text(
        encoding="utf-8"
    )
    assert "_assert_runtime_database_boundary" not in source
    assert "product_schema_migrations" not in source
    assert "product_goals" not in source


def test_channel_extensions_only_import_the_public_joyhousebot_sdk() -> None:
    violations: list[str] = []
    for path in (ROOT / "extensions").glob("channel-*/src/**/*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                if module.startswith("joyhousebot.") and not module.startswith(
                    "joyhousebot.extension_sdk"
                ):
                    violations.append(f"{path.relative_to(ROOT)}:{module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("joyhousebot.") and not alias.name.startswith(
                        "joyhousebot.extension_sdk"
                    ):
                        violations.append(f"{path.relative_to(ROOT)}:{alias.name}")
    assert violations == []


def test_provider_extensions_only_import_the_public_joyhousebot_sdk() -> None:
    violations: list[str] = []
    for path in (ROOT / "extensions").glob("provider-*/src/**/*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                if module.startswith("joyhousebot.") and not module.startswith(
                    "joyhousebot.extension_sdk"
                ):
                    violations.append(f"{path.relative_to(ROOT)}:{module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("joyhousebot.") and not alias.name.startswith(
                        "joyhousebot.extension_sdk"
                    ):
                        violations.append(f"{path.relative_to(ROOT)}:{alias.name}")
    assert violations == []


def test_capability_extensions_only_import_the_public_joyhousebot_sdk() -> None:
    violations: list[str] = []
    for path in (ROOT / "extensions").glob("capability-*/src/**/*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom):
                modules = [str(node.module or "")]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            for module in modules:
                if module.startswith("joyhousebot.") and not module.startswith(
                    "joyhousebot.extension_sdk"
                ):
                    violations.append(f"{path.relative_to(ROOT)}:{module}")
    assert violations == []


def test_connector_extensions_only_import_the_public_joyhousebot_sdk() -> None:
    violations: list[str] = []
    for path in (ROOT / "extensions").glob("connector-*/src/**/*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom):
                modules = [str(node.module or "")]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            for module in modules:
                if module.startswith("joyhousebot.") and not module.startswith(
                    "joyhousebot.extension_sdk"
                ):
                    violations.append(f"{path.relative_to(ROOT)}:{module}")
    assert violations == []


def test_research_implementation_is_not_in_core() -> None:
    assert not (ROOT / "joyhousebot/agent/tools/web.py").exists()


def test_context_assets_implementation_is_not_in_core() -> None:
    for relative in (
        "joyhousebot/agent/tools/retrieve.py",
        "joyhousebot/agent/tools/memory_get.py",
        "joyhousebot/agent/tools/fetch_url_to_knowledgebase.py",
        "joyhousebot/agent/tools/ingest/url_ingest.py",
    ):
        assert not (ROOT / relative).exists()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    assert not any(
        str(item).startswith("readability-lxml") for item in project["dependencies"]
    )


def test_filesystem_tool_implementation_is_not_in_core() -> None:
    assert not (ROOT / "joyhousebot/agent/tools/filesystem.py").exists()
    assert not (
        ROOT
        / "extensions/capability-filesystem/src/joyhousebot_capability_filesystem/legacy.py"
    ).exists()


def test_shell_tool_implementation_is_not_in_core() -> None:
    assert not (ROOT / "joyhousebot/agent/tools/shell.py").exists()
    assert not (
        ROOT / "extensions/capability-shell/src/joyhousebot_capability_shell/legacy.py"
    ).exists()


def test_runtime_control_tool_implementations_are_not_in_core() -> None:
    for name in ("message.py", "spawn.py", "cron.py", "monitor_scratch.py"):
        assert not (ROOT / "joyhousebot/agent/tools" / name).exists()
    extension = (
        ROOT
        / "extensions/capability-runtime-control/src/joyhousebot_capability_runtime_control"
    )
    assert not list(extension.glob("legacy_*.py"))


def test_mcp_client_implementation_is_not_in_core() -> None:
    assert not (ROOT / "joyhousebot/agent/tools/mcp.py").exists()
    runtime = (ROOT / "joyhousebot/agent/capability_connector_runtime.py").read_text(
        encoding="utf-8"
    )
    assert "connect_mcp_servers" not in runtime


def test_migrated_provider_implementations_are_not_in_core() -> None:
    assert not (ROOT / "joyhousebot/providers/anthropic.py").exists()
    assert not (ROOT / "joyhousebot/providers/openai_compatible.py").exists()
    registry = (ROOT / "joyhousebot/providers/registry.py").read_text(encoding="utf-8")
    assert "api.openai.com" not in registry
    assert "api.deepseek.com" not in registry
    assert "openrouter.ai" not in registry
    assert not (ROOT / "joyhousebot/providers/transcription.py").exists()
    defaults = (ROOT / "joyhousebot/domain/agents/defaults.py").read_text(
        encoding="utf-8"
    )
    migrations = (ROOT / "joyhousebot/storage/postgres_agents.py").read_text(
        encoding="utf-8"
    )
    assert "openrouter/deepseek" not in defaults
    assert "openrouter/deepseek" not in migrations
    assert "anthropic/claude" not in migrations


def test_migrated_channel_implementations_are_not_in_core() -> None:
    builtin = ROOT / "joyhousebot/channels/plugins/builtin"
    assert not list(builtin.glob("*.py"))

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert "channel-dingtalk" not in project["optional-dependencies"]
    assert "channel-discord" not in project["optional-dependencies"]
    assert "channel-feishu" not in project["optional-dependencies"]
    assert "channel-qq" not in project["optional-dependencies"]
    assert "channel-slack" not in project["optional-dependencies"]
    assert "channel-telegram" not in project["optional-dependencies"]
    assert "channel-whatsapp" not in project["optional-dependencies"]


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
        "apps/console/src/services/gateway-client.ts",
        "apps/console/src/composables/useGateway.ts",
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
    # The ratcheting function/file baseline lives in scripts/check_complexity.py.
    # This architecture contract keeps the repository-wide absolute ceiling
    # visible even when that standalone guard is not invoked directly.
    default_limit = 900
    oversized: list[tuple[str, int]] = []
    for path in (ROOT / "joyhousebot").rglob("*.py"):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > default_limit:
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
        "joyhousebot/services/memory/repository.py",
        "joyhousebot/agent/profile_health_repository.py",
        "joyhousebot/services/retrieval/knowledge_repository.py",
    ]
    oversized = []
    for relative in repository_files:
        lines = len((ROOT / relative).read_text(encoding="utf-8").splitlines())
        # The scheduler repository keeps schedule, occurrence and fenced
        # delivery completion in one transaction boundary.
        if lines > 650:
            oversized.append((relative, lines))
    assert oversized == []


def test_cloud_tool_defaults_fail_closed() -> None:
    from joyhousebot.config.schema import Config

    config = Config()
    assert config.tools.optional_allowlist == []
    assert not (ROOT / "joyhousebot/agent/tools/shell.py").exists()
    assert not (
        ROOT
        / "extensions/capability-shell/src/joyhousebot_capability_shell/legacy.py"
    ).exists()


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
    "observability": 1,
    "operations": 5,
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
    # Stable outward-facing facade over lower Core contracts/adapters.
    "extension_sdk": 4,
    # 5 — application use cases.
    "application": 5,
    # 6 — entrypoints.
    "api": 6,
    "bootstrap": 6,
    "cli": 6,
    # Dedicated credential-isolating HTTP entrypoint plus its bounded service.
    "model_gateway": 6,
    "__main__": 6,
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
        violations.append(f"{source} -> {module}")
    assert violations == []


def test_runtime_runner_uses_explicit_storage_views() -> None:
    source = (ROOT / "joyhousebot/runtime/runner.py").read_text(encoding="utf-8")

    assert "store: Any" not in source
    assert "self.stores = RuntimeStores.from_backend(store)" in source
    assert "self.store:" not in source
    assert "EventBroker(self.stores.events)" in source


def test_migrated_runtime_services_use_only_narrow_storage_views() -> None:
    migrated = (
        "agent_execution.py",
        "agent_execution_outcomes.py",
        "agent_terminal.py",
        "submission.py",
        "coordinator.py",
        "controls.py",
        "graph_bounded_loop_execution.py",
        "graph_branch_execution.py",
        "graph_capability_execution.py",
        "graph_compensation_execution.py",
        "graph_control_execution.py",
        "graph_finalization.py",
        "graph_foreach_execution.py",
        "graph_materialization.py",
        "graph_reconciliation.py",
        "graph_saga_execution.py",
        "graph_subrun_execution.py",
        "graph_task_execution.py",
        "graph_task_lifecycle.py",
        "graph_wait_event_execution.py",
        "maintenance.py",
        "plan_confirmation.py",
        "planning_loop.py",
        "request_coordination.py",
    )
    violations: list[str] = []
    for filename in migrated:
        path = ROOT / "joyhousebot/runtime" / filename
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in {"self", "runtime"}
            and node.attr == "store"
            for node in ast.walk(tree)
        ):
            violations.append(filename)

    assert violations == []
    submission = (ROOT / "joyhousebot/runtime/submission.py").read_text(
        encoding="utf-8"
    )
    assert "getattr(self.stores.graphs" not in submission


def test_run_application_services_use_only_narrow_storage_views() -> None:
    migrated = (
        "app_manifest_validation.py",
        "runs.py",
        "run_creation.py",
        "run_plans.py",
        "graph_patch_preparation.py",
        "graph_patches.py",
        "workflow_compiler.py",
        "workflows.py",
    )
    violations: list[str] = []
    for filename in migrated:
        path = ROOT / "joyhousebot/application" / filename
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and node.attr == "store"
            for node in ast.walk(tree)
        ):
            violations.append(filename)

    assert violations == []


def _class_bases(relative: str, class_name: str) -> list[str]:
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.ClassDef) and item.name == class_name
    )
    return [ast.unparse(base) for base in node.bases]


def test_core_runtime_and_agent_use_explicit_service_composition() -> None:
    assert _class_bases("joyhousebot/runtime/runner.py", "NativeAgentRuntime") == []
    assert _class_bases("joyhousebot/agent/executor.py", "NativeAgentExecutor") == []

    runtime = (ROOT / "joyhousebot/runtime/runner.py").read_text(encoding="utf-8")
    executor = (ROOT / "joyhousebot/agent/executor.py").read_text(encoding="utf-8")
    assert "RuntimeServices.create(self)" in runtime
    assert "AgentServices.create(self)" in executor


def test_postgres_store_is_composed_from_repository_groups() -> None:
    assert _class_bases("joyhousebot/storage/postgres_store.py", "PostgresRuntimeStore") == []
    source = (ROOT / "joyhousebot/storage/runtime_store.py").read_text(encoding="utf-8")
    assert "class RuntimeStore" not in source

    postgres = (ROOT / "joyhousebot/storage/postgres_store.py").read_text(
        encoding="utf-8"
    )
    assert "PostgresRepositorySet" in postgres
