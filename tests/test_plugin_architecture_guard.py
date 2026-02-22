from pathlib import Path

from joyhousebot.plugins.architecture_guard import collect_plugin_boundary_violations


def test_plugin_boundary_no_violations_in_current_package():
    repo_root = Path(__file__).resolve().parents[1]
    package_root = repo_root / "joyhousebot"
    violations = collect_plugin_boundary_violations(package_root=package_root)
    assert violations == []


def test_plugin_boundary_detects_forbidden_import(tmp_path: Path):
    package_root = tmp_path / "joyhousebot"
    (package_root / "api").mkdir(parents=True)
    (package_root / "plugins" / "bridge").mkdir(parents=True)
    (package_root / "api" / "bad.py").write_text(
        "from joyhousebot.plugins.bridge.host_client import PluginHostClient\n",
        encoding="utf-8",
    )
    (package_root / "plugins" / "bridge" / "host_client.py").write_text(
        "class PluginHostClient: ...\n",
        encoding="utf-8",
    )
    violations = collect_plugin_boundary_violations(package_root=package_root)
    assert any("forbidden-import-from: joyhousebot.plugins.bridge.host_client" in row for row in violations)

