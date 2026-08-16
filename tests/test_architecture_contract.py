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
    source = (ROOT / "porthouse/storage/postgres_store.py").read_text(
        encoding="utf-8"
    )
    assert "_assert_runtime_database_boundary" not in source
    assert "product_schema_migrations" not in source
    assert "product_goals" not in source


def test_channel_extensions_only_import_the_public_porthouse_sdk() -> None:
    violations: list[str] = []
    for path in (ROOT / "extensions").glob("channel-*/src/**/*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                if module.startswith("porthouse.") and not module.startswith(
                    "porthouse.extension_sdk"
                ):
                    violations.append(f"{path.relative_to(ROOT)}:{module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("porthouse.") and not alias.name.startswith(
                        "porthouse.extension_sdk"
                    ):
                        violations.append(f"{path.relative_to(ROOT)}:{alias.name}")
    assert violations == []


def test_provider_extensions_only_import_the_public_porthouse_sdk() -> None:
    violations: list[str] = []
    for path in (ROOT / "extensions").glob("provider-*/src/**/*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                if module.startswith("porthouse.") and not module.startswith(
                    "porthouse.extension_sdk"
                ):
                    violations.append(f"{path.relative_to(ROOT)}:{module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("porthouse.") and not alias.name.startswith(
                        "porthouse.extension_sdk"
                    ):
                        violations.append(f"{path.relative_to(ROOT)}:{alias.name}")
    assert violations == []


def test_capability_extensions_only_import_the_public_porthouse_sdk() -> None:
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
                if module.startswith("porthouse.") and not module.startswith(
                    "porthouse.extension_sdk"
                ):
                    violations.append(f"{path.relative_to(ROOT)}:{module}")
    assert violations == []


def test_connector_extensions_only_import_the_public_porthouse_sdk() -> None:
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
                if module.startswith("porthouse.") and not module.startswith(
                    "porthouse.extension_sdk"
                ):
                    violations.append(f"{path.relative_to(ROOT)}:{module}")
    assert violations == []


def test_research_implementation_is_not_in_core() -> None:
    assert not (ROOT / "porthouse/agent/tools/web.py").exists()


def test_context_assets_implementation_is_not_in_core() -> None:
    for relative in (
        "porthouse/agent/tools/retrieve.py",
        "porthouse/agent/tools/memory_get.py",
        "porthouse/agent/tools/fetch_url_to_knowledgebase.py",
        "porthouse/agent/tools/ingest/url_ingest.py",
    ):
        assert not (ROOT / relative).exists()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    assert not any(
        str(item).startswith("readability-lxml") for item in project["dependencies"]
    )


def test_filesystem_tool_implementation_is_not_in_core() -> None:
    assert not (ROOT / "porthouse/agent/tools/filesystem.py").exists()
    assert not (
        ROOT
        / "extensions/capability-filesystem/src/porthouse_capability_filesystem/legacy.py"
    ).exists()


def test_shell_tool_implementation_is_not_in_core() -> None:
    assert not (ROOT / "porthouse/agent/tools/shell.py").exists()
    assert not (
        ROOT / "extensions/capability-shell/src/porthouse_capability_shell/legacy.py"
    ).exists()


def test_runtime_control_tool_implementations_are_not_in_core() -> None:
    for name in ("message.py", "spawn.py", "cron.py", "monitor_scratch.py"):
        assert not (ROOT / "porthouse/agent/tools" / name).exists()
    extension = (
        ROOT
        / "extensions/capability-runtime-control/src/porthouse_capability_runtime_control"
    )
    assert not list(extension.glob("legacy_*.py"))


def test_mcp_client_implementation_is_not_in_core() -> None:
    assert not (ROOT / "porthouse/agent/tools/mcp.py").exists()
    runtime = (ROOT / "porthouse/agent/tool_runtime.py").read_text(encoding="utf-8")
    assert "connect_mcp_servers" not in runtime


def test_migrated_provider_implementations_are_not_in_core() -> None:
    assert not (ROOT / "porthouse/providers/anthropic.py").exists()
    assert not (ROOT / "porthouse/providers/openai_compatible.py").exists()
    registry = (ROOT / "porthouse/providers/registry.py").read_text(encoding="utf-8")
    assert "api.openai.com" not in registry
    assert "api.deepseek.com" not in registry
    assert "openrouter.ai" not in registry
    assert not (ROOT / "porthouse/providers/transcription.py").exists()
    defaults = (ROOT / "porthouse/domain/agents/defaults.py").read_text(
        encoding="utf-8"
    )
    migrations = (ROOT / "porthouse/storage/postgres_agents.py").read_text(
        encoding="utf-8"
    )
    assert "openrouter/deepseek" not in defaults
    assert "openrouter/deepseek" not in migrations
    assert "anthropic/claude" not in migrations


def test_migrated_channel_implementations_are_not_in_core() -> None:
    builtin = ROOT / "porthouse/channels/plugins/builtin"
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
        "porthouse/api/rpc",
        "porthouse/api/http",
        "porthouse/gateway",
        "porthouse/node",
        "porthouse/control_plane",
        "porthouse/heartbeat",
        "porthouse/identity",
        "porthouse/financial",
        "porthouse/plugins",
        "porthouse/browser",
        "porthouse/agent/collaboration",
        "porthouse/agent/tools/code_backends",
        "porthouse/session/manager.py",
        "porthouse/services/agents",
        "porthouse/services/knowledge_pipeline",
        "porthouse/services/plugins",
        "porthouse/services/sessions",
        "porthouse/services/skills",
        "porthouse/services/tasks",
        "porthouse/cli/commands.py",
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
    # 700 lines is the default review gate. It still catches accidental monoliths
    # without making normal orchestration modules fail over a handful of lines.
    default_limit = 700
    module_limits = {
        # RuntimeStore is intentionally a Protocol/record aggregation surface;
        # domain implementations remain subject to the stricter default.
        "porthouse/storage/runtime_store.py": 850,
        # Pydantic transport DTOs are a versioned API aggregation surface; runtime
        # and repository modules remain subject to the stricter default.
        "porthouse/api/schemas.py": 700,
        # Market lifecycle coordination is intentionally grouped by its signed
        # acquisition state machine. The storage mixin mirrors one bounded set
        # of Market-owned tables; neither module imports business App code.
        "porthouse/application/app_market.py": 850,
        "porthouse/storage/postgres_app_market.py": 850,
    }
    oversized: list[tuple[str, int]] = []
    for path in (ROOT / "porthouse").rglob("*.py"):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        relative = str(path.relative_to(ROOT))
        if lines > module_limits.get(relative, default_limit):
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
    for path in (ROOT / "porthouse").rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        if any(token in content for token in forbidden):
            matches.append(str(path.relative_to(ROOT)))
    assert matches == []


def test_cluster_repository_files_are_bounded() -> None:
    repository_files = [
        "porthouse/scheduling/repository.py",
        "porthouse/channels/repository.py",
        "porthouse/services/memory/repository.py",
        "porthouse/agent/profile_health_repository.py",
        "porthouse/services/retrieval/knowledge_repository.py",
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
    from porthouse.config.schema import Config

    config = Config()
    assert config.tools.optional_allowlist == []
    assert not (ROOT / "porthouse/agent/tools/shell.py").exists()
    assert not (
        ROOT
        / "extensions/capability-shell/src/porthouse_capability_shell/legacy.py"
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
    # 0 — foundation: no dependency on any other porthouse package.
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
    """Collect (source file, imported porthouse module) edges via AST.

    ``ast.walk`` deliberately also reaches function-level deferred imports.
    """
    import ast

    edges: list[tuple[str, str]] = []
    package_root = ROOT / "porthouse"
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
                if len(parts) >= 2 and parts[0] == "porthouse":
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
