from pathlib import Path

from joyhousebot.agent.skills import SkillsLoader
from joyhousebot.api.server import _gateway_methods_with_plugins, app_state
from joyhousebot.config.schema import Config, PluginEntryConfig
from joyhousebot.plugins.bridge.host_client import PluginHostClient
from joyhousebot.plugins.manager import PluginManager
from joyhousebot.plugins.native.loader import NativePluginLoader
from joyhousebot.plugins.skills import resolve_plugin_skill_dirs


def test_plugins_config_defaults_present():
    cfg = Config()
    assert cfg.plugins.enabled is True
    assert cfg.plugins.load.paths == []
    assert cfg.plugins.entries == {}
    assert cfg.plugins.installs == {}


def test_resolve_plugin_skill_dirs_with_manifest(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    plugin_root = tmp_path / "demo-plugin"
    (plugin_root / "skills" / "demo").mkdir(parents=True)
    (plugin_root / "skills" / "demo" / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
    (plugin_root / "openclaw.plugin.json").write_text(
        """
{
  "id": "demo-plugin",
  "configSchema": {"type":"object","additionalProperties":true},
  "skills": ["skills"]
}
""".strip(),
        encoding="utf-8",
    )

    cfg = Config()
    cfg.plugins.load.paths = [str(plugin_root)]
    cfg.plugins.entries["demo-plugin"] = PluginEntryConfig(enabled=True)
    dirs = resolve_plugin_skill_dirs(workspace=workspace, config=cfg)
    expected_skills = (plugin_root / "skills").resolve()
    assert expected_skills in [d.resolve() for d in dirs]
    assert len(dirs) >= 1


def test_skills_loader_includes_plugin_skills(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    plugin_root = tmp_path / "plugin-a"
    (plugin_root / "skills" / "from-plugin").mkdir(parents=True)
    (plugin_root / "skills" / "from-plugin" / "SKILL.md").write_text("# plugin\n", encoding="utf-8")
    (plugin_root / "openclaw.plugin.json").write_text(
        """
{
  "id": "plugin-a",
  "configSchema": {"type":"object","additionalProperties":true},
  "skills": ["skills"]
}
""".strip(),
        encoding="utf-8",
    )

    cfg = Config()
    cfg.plugins.load.paths = [str(plugin_root)]
    cfg.plugins.entries["plugin-a"] = PluginEntryConfig(enabled=True)
    monkeypatch.setattr("joyhousebot.config.loader.load_config", lambda: cfg)

    loader = SkillsLoader(workspace=workspace)
    names = {item["name"] for item in loader.list_skills(filter_unavailable=False)}
    assert "from-plugin" in names


def test_gateway_methods_merge_plugin_methods():
    original_snapshot = app_state.get("plugin_snapshot")

    class _Snapshot:
        gateway_methods = ["plugin.echo", "plugin.health"]

    app_state["plugin_snapshot"] = _Snapshot()
    merged = _gateway_methods_with_plugins()
    assert "plugin.echo" in merged
    assert "connect" in merged
    app_state["plugin_snapshot"] = original_snapshot


def test_plugin_host_requirements_report(tmp_path: Path):
    root = tmp_path / "proj"
    host = root / "plugin_host" / "src"
    host.mkdir(parents=True)
    (host / "host.mjs").write_text("export {};", encoding="utf-8")
    openclaw = tmp_path / "openclaw"
    openclaw.mkdir()
    (openclaw / "package.json").write_text("{}", encoding="utf-8")
    client = PluginHostClient(openclaw_dir=str(openclaw))
    report = client.requirements_report()
    assert "checks" in report
    assert report["checks"]["openclawDirExists"] is True
    assert report["checks"]["openclawPackageJsonExists"] is True


def test_plugin_host_setup_host_dry_run(tmp_path: Path):
    openclaw = tmp_path / "openclaw"
    openclaw.mkdir(parents=True)
    (openclaw / "package.json").write_text("{}", encoding="utf-8")
    client = PluginHostClient(openclaw_dir=str(openclaw))
    result = client.setup_host(dry_run=True, install_deps=False, build_dist=False)
    assert result["ok"] is True
    assert result["dryRun"] is True


def test_resolve_plugin_skill_dirs_with_native_manifest(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    plugin_root = tmp_path / "native-plugin"
    (plugin_root / "skills" / "native-demo").mkdir(parents=True)
    (plugin_root / "skills" / "native-demo" / "SKILL.md").write_text("# Native Demo\n", encoding="utf-8")
    (plugin_root / "joyhousebot.plugin.json").write_text(
        """
{
  "id": "native-plugin",
  "runtime": "python-native",
  "entry": "plugin.py:plugin",
  "skills": ["skills"]
}
""".strip(),
        encoding="utf-8",
    )
    cfg = Config()
    cfg.plugins.load.paths = [str(plugin_root)]
    cfg.plugins.entries["native-plugin"] = PluginEntryConfig(enabled=True)
    dirs = resolve_plugin_skill_dirs(workspace=workspace, config=cfg)
    expected_skills = (plugin_root / "skills").resolve()
    assert expected_skills in [d.resolve() for d in dirs]
    assert len(dirs) >= 1


def test_native_plugin_loader_loads_rpc_tool_hook(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    plugin_root = tmp_path / "native-rpc"
    plugin_root.mkdir(parents=True)
    (plugin_root / "joyhousebot.plugin.json").write_text(
        """
{
  "id": "native-rpc",
  "runtime": "python-native",
  "entry": "plugin.py:plugin"
}
""".strip(),
        encoding="utf-8",
    )
    (plugin_root / "plugin.py").write_text(
        """
class Plugin:
    def register(self, api):
        api.register_tool("native.echo", lambda value: value)
        api.register_rpc("native.echo", lambda params: {"echo": params.get("text", "")})
        api.register_hook("gateway_start", lambda *_: None, priority=10)
        api.register_service(
            "native.service",
            start=lambda: None,
            stop=lambda: None,
        )
        api.register_http(
            "/native/echo",
            lambda req: {"status": 200, "headers": {"x-native": "1"}, "body": {"echo": req.get("body")}},
            methods=["POST"],
        )
        api.register_cli(
            "native.echo",
            lambda payload: {"echo": payload.get("text", "")},
            description="Echo CLI command",
        )
        api.register_provider("native.mock.provider")
        api.register_channel(
            "native.mock.channel",
            start=lambda: None,
            stop=lambda: None,
        )

plugin = Plugin()
""".strip(),
        encoding="utf-8",
    )
    cfg = Config()
    cfg.plugins.load.paths = [str(plugin_root)]
    cfg.plugins.entries["native-rpc"] = PluginEntryConfig(enabled=True)
    loader = NativePluginLoader()
    registry = loader.load(
        workspace_dir=str(workspace),
        config=cfg.model_dump(by_alias=True),
    )
    assert "native.echo" in registry.gateway_methods
    assert "native.echo" in registry.tool_names
    assert "gateway_start" in registry.hook_names
    assert "native.service" in registry.service_ids
    assert "/native/echo" in registry.http_paths
    assert "native.echo" in registry.cli_commands
    assert "native.mock.provider" in registry.provider_ids
    assert "native.mock.channel" in registry.channel_ids
    record = next(r for r in registry.records if r.id == "native-rpc")
    assert record.runtime == "python-native"
    assert "native.service" in record.services
    assert "native.echo" in record.cli_commands
    assert "native.mock.provider" in record.provider_ids
    assert "native.mock.channel" in record.channel_ids


def test_manager_start_stop_native_services(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    plugin_root = tmp_path / "native-service"
    plugin_root.mkdir(parents=True)
    (plugin_root / "joyhousebot.plugin.json").write_text(
        """
{
  "id": "native-service",
  "runtime": "python-native",
  "entry": "plugin.py:plugin"
}
""".strip(),
        encoding="utf-8",
    )
    (plugin_root / "plugin.py").write_text(
        """
STATE = {"started": 0, "stopped": 0}

class Plugin:
    def register(self, api):
        api.register_service(
            "native.service.lifecycle",
            start=lambda: STATE.__setitem__("started", STATE["started"] + 1),
            stop=lambda: STATE.__setitem__("stopped", STATE["stopped"] + 1),
        )

plugin = Plugin()
""".strip(),
        encoding="utf-8",
    )
    cfg = Config()
    cfg.plugins.load.paths = [str(plugin_root)]
    cfg.plugins.entries["native-service"] = PluginEntryConfig(enabled=True)
    manager = PluginManager()
    manager.client.start_services = lambda: []  # type: ignore[method-assign]
    manager.client.stop_services = lambda: []  # type: ignore[method-assign]
    manager.load(workspace_dir=str(workspace), config=cfg.model_dump(by_alias=True), reload=True)
    start_rows = manager.start_services()
    stop_rows = manager.stop_services()
    assert any(row.get("id") == "native.service.lifecycle" and row.get("started") is True for row in start_rows)
    assert any(row.get("id") == "native.service.lifecycle" and row.get("stopped") is True for row in stop_rows)


def test_manager_http_dispatch_native_route(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    plugin_root = tmp_path / "native-http"
    plugin_root.mkdir(parents=True)
    (plugin_root / "joyhousebot.plugin.json").write_text(
        """
{
  "id": "native-http",
  "runtime": "python-native",
  "entry": "plugin.py:plugin"
}
""".strip(),
        encoding="utf-8",
    )
    (plugin_root / "plugin.py").write_text(
        """
class Plugin:
    def register(self, api):
        api.register_http(
            "/native/http/echo",
            lambda req: {"status": 201, "body": {"path": req.get("path"), "body": req.get("body")}},
            methods=["POST"],
        )

plugin = Plugin()
""".strip(),
        encoding="utf-8",
    )
    cfg = Config()
    cfg.plugins.load.paths = [str(plugin_root)]
    cfg.plugins.entries["native-http"] = PluginEntryConfig(enabled=True)
    manager = PluginManager()
    manager.client.http_dispatch = lambda request: {"ok": False, "error": {"code": "BRIDGE_UNAVAILABLE"}}  # type: ignore[method-assign]
    manager.load(workspace_dir=str(workspace), config=cfg.model_dump(by_alias=True), reload=True)
    result = manager.http_dispatch({"method": "POST", "path": "/native/http/echo", "body": {"x": 1}})
    assert result["ok"] is True
    assert result["status"] == 201
    assert result["body"]["path"] == "/native/http/echo"


def test_manager_invoke_native_cli_command(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    plugin_root = tmp_path / "native-cli"
    plugin_root.mkdir(parents=True)
    (plugin_root / "joyhousebot.plugin.json").write_text(
        """
{
  "id": "native-cli",
  "runtime": "python-native",
  "entry": "plugin.py:plugin"
}
""".strip(),
        encoding="utf-8",
    )
    (plugin_root / "plugin.py").write_text(
        """
class Plugin:
    def register(self, api):
        api.register_cli("native.cli.echo", lambda payload: {"ok": True, "value": payload.get("value")})

plugin = Plugin()
""".strip(),
        encoding="utf-8",
    )
    cfg = Config()
    cfg.plugins.load.paths = [str(plugin_root)]
    cfg.plugins.entries["native-cli"] = PluginEntryConfig(enabled=True)
    manager = PluginManager()
    manager.load(workspace_dir=str(workspace), config=cfg.model_dump(by_alias=True), reload=True)
    commands = manager.cli_commands()
    assert "native.cli.echo" in commands
    result = manager.invoke_cli_command("native.cli.echo", {"value": 7})
    assert result["ok"] is True
    assert result["result"]["value"] == 7


def test_manager_providers_list_merges_bridge_and_native(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    plugin_root = tmp_path / "native-provider"
    plugin_root.mkdir(parents=True)
    (plugin_root / "joyhousebot.plugin.json").write_text(
        """
{
  "id": "native-provider",
  "runtime": "python-native",
  "entry": "plugin.py:plugin"
}
""".strip(),
        encoding="utf-8",
    )
    (plugin_root / "plugin.py").write_text(
        """
class Plugin:
    def register(self, api):
        api.register_provider("native.provider")

plugin = Plugin()
""".strip(),
        encoding="utf-8",
    )
    cfg = Config()
    cfg.plugins.load.paths = [str(plugin_root)]
    cfg.plugins.entries["native-provider"] = PluginEntryConfig(enabled=True)
    manager = PluginManager()
    manager.client.providers_list = lambda: ["bridge.provider"]  # type: ignore[method-assign]
    manager.load(workspace_dir=str(workspace), config=cfg.model_dump(by_alias=True), reload=True)
    providers = manager.providers_list()
    assert "bridge.provider" in providers
    assert "native.provider" in providers


def test_manager_channels_list_merges_bridge_and_native(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    plugin_root = tmp_path / "native-channel"
    plugin_root.mkdir(parents=True)
    (plugin_root / "joyhousebot.plugin.json").write_text(
        """
{
  "id": "native-channel",
  "runtime": "python-native",
  "entry": "plugin.py:plugin"
}
""".strip(),
        encoding="utf-8",
    )
    (plugin_root / "plugin.py").write_text(
        """
class Plugin:
    def register(self, api):
        api.register_channel("native.channel")

plugin = Plugin()
""".strip(),
        encoding="utf-8",
    )
    cfg = Config()
    cfg.plugins.load.paths = [str(plugin_root)]
    cfg.plugins.entries["native-channel"] = PluginEntryConfig(enabled=True)
    manager = PluginManager()
    manager.client.channels_list = lambda: ["bridge.channel"]  # type: ignore[method-assign]
    manager.load(workspace_dir=str(workspace), config=cfg.model_dump(by_alias=True), reload=True)
    channels = manager.channels_list()
    assert "bridge.channel" in channels
    assert "native.channel" in channels


def test_native_loader_conflict_resolution_uses_first_plugin(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    first = tmp_path / "plugin-first"
    second = tmp_path / "plugin-second"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "joyhousebot.plugin.json").write_text(
        """
{"id":"plugin-first","runtime":"python-native","entry":"plugin.py:plugin"}
""".strip(),
        encoding="utf-8",
    )
    (second / "joyhousebot.plugin.json").write_text(
        """
{"id":"plugin-second","runtime":"python-native","entry":"plugin.py:plugin"}
""".strip(),
        encoding="utf-8",
    )
    (first / "plugin.py").write_text(
        """
class Plugin:
    def register(self, api):
        api.register_rpc("native.conflict", lambda payload: {"owner": "first"})

plugin = Plugin()
""".strip(),
        encoding="utf-8",
    )
    (second / "plugin.py").write_text(
        """
class Plugin:
    def register(self, api):
        api.register_rpc("native.conflict", lambda payload: {"owner": "second"})

plugin = Plugin()
""".strip(),
        encoding="utf-8",
    )
    cfg = Config()
    cfg.plugins.load.paths = [str(first), str(second)]
    cfg.plugins.entries["plugin-first"] = PluginEntryConfig(enabled=True)
    cfg.plugins.entries["plugin-second"] = PluginEntryConfig(enabled=True)
    loader = NativePluginLoader()
    registry = loader.load(workspace_dir=str(workspace), config=cfg.model_dump(by_alias=True))
    result = loader.invoke_rpc(registry, "native.conflict", {})
    assert result["ok"] is True
    assert result["payload"]["owner"] == "first"
    assert any(diag.get("code") == "NATIVE_CONFLICT_RPC" for diag in registry.diagnostics)


def test_manager_native_circuit_breaker_and_runtime_stats(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    plugin_root = tmp_path / "native-circuit"
    plugin_root.mkdir(parents=True)
    (plugin_root / "joyhousebot.plugin.json").write_text(
        """
{"id":"native-circuit","runtime":"python-native","entry":"plugin.py:plugin"}
""".strip(),
        encoding="utf-8",
    )
    (plugin_root / "plugin.py").write_text(
        """
class Plugin:
    def register(self, api):
        def _boom(_params):
            raise RuntimeError("boom")
        api.register_rpc("native.circuit.boom", _boom)

plugin = Plugin()
""".strip(),
        encoding="utf-8",
    )
    cfg = Config()
    cfg.plugins.load.paths = [str(plugin_root)]
    cfg.plugins.entries["native-circuit"] = PluginEntryConfig(enabled=True)
    manager = PluginManager()
    manager.load(workspace_dir=str(workspace), config=cfg.model_dump(by_alias=True), reload=True)
    for _ in range(3):
        result = manager.invoke_gateway_method("native.circuit.boom", {})
        assert result["ok"] is False
        assert result["error"]["code"] == "NATIVE_RPC_ERROR"
    blocked = manager.invoke_gateway_method("native.circuit.boom", {})
    assert blocked["ok"] is False
    assert blocked["error"]["code"] == "NATIVE_CIRCUIT_OPEN"
    runtime = manager.native_runtime_report()
    assert runtime["totals"]["calls"] >= 4
    assert runtime["totals"]["errors"] >= 4
    assert any(item.get("key", "").startswith("rpc:native.circuit.boom") for item in runtime["openCircuits"])
    assert "NATIVE_CIRCUIT_OPEN" in runtime["last24h"]["errorsByCode"]


def test_manager_runtime_stats_persist_across_instances(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    plugin_root = tmp_path / "native-stats"
    plugin_root.mkdir(parents=True)
    (plugin_root / "joyhousebot.plugin.json").write_text(
        """
{"id":"native-stats","runtime":"python-native","entry":"plugin.py:plugin"}
""".strip(),
        encoding="utf-8",
    )
    (plugin_root / "plugin.py").write_text(
        """
class Plugin:
    def register(self, api):
        api.register_rpc("native.stats.ok", lambda payload: {"ok": True})

plugin = Plugin()
""".strip(),
        encoding="utf-8",
    )
    cfg = Config()
    cfg.plugins.load.paths = [str(plugin_root)]
    cfg.plugins.entries["native-stats"] = PluginEntryConfig(enabled=True)
    manager1 = PluginManager()
    manager1.load(workspace_dir=str(workspace), config=cfg.model_dump(by_alias=True), reload=True)
    result = manager1.invoke_gateway_method("native.stats.ok", {})
    assert result["ok"] is True
    stats_path = workspace / ".joyhouse" / "plugin-runtime-stats.json"
    assert stats_path.exists()
    manager2 = PluginManager()
    manager2.load(workspace_dir=str(workspace), config=cfg.model_dump(by_alias=True), reload=True)
    runtime = manager2.native_runtime_report()
    assert runtime["totals"]["calls"] >= 1
    assert runtime["totals"]["ok"] >= 1


def test_manager_status_report_contains_runtime_and_origin_counts(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    plugin_root = tmp_path / "native-status"
    plugin_root.mkdir(parents=True)
    (plugin_root / "joyhousebot.plugin.json").write_text(
        """
{"id":"native-status","runtime":"python-native","entry":"plugin.py:plugin"}
""".strip(),
        encoding="utf-8",
    )
    (plugin_root / "plugin.py").write_text(
        """
class Plugin:
    def register(self, api):
        api.register_rpc("native.status.ping", lambda payload: {"pong": True})

plugin = Plugin()
""".strip(),
        encoding="utf-8",
    )
    cfg = Config()
    cfg.plugins.load.paths = [str(plugin_root)]
    cfg.plugins.entries["native-status"] = PluginEntryConfig(enabled=True)
    manager = PluginManager()
    manager.load(workspace_dir=str(workspace), config=cfg.model_dump(by_alias=True), reload=True)
    manager.invoke_gateway_method("native.status.ping", {})
    report = manager.status_report()
    assert report["ok"] is True
    assert report["plugins"]["total"] >= 1
    assert report["plugins"]["byOrigin"].get("native", 0) >= 1
    assert "nativeRuntime" in report
    assert report["nativeRuntime"]["totals"]["calls"] >= 1

