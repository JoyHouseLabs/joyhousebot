from joyhousebot.services.plugins.doctor_service import (
    collect_plugins_doctor_reports,
    should_run_setup_host,
    snapshot_has_plugin_issues,
)


class _Client:
    def requirements_report(self):
        return {"checks": {"openclawDirExists": True, "openclawPackageJsonExists": True, "openclawLoaderAvailable": False}}


class _Manager:
    def __init__(self):
        self.client = _Client()

    def native_doctor(self, workspace_dir: str, config: dict):
        assert workspace_dir
        assert isinstance(config, dict)
        return {"checks": {"loadedCount": 1}}

    def doctor(self):
        return {"native": {"runtime": {"totals": {"calls": 3}}}}


def test_collect_reports_and_should_setup():
    manager = _Manager()
    report, native_report = collect_plugins_doctor_reports(
        manager=manager,
        workspace_dir="/tmp/workspace",
        config_payload={},
    )
    assert should_run_setup_host(report) is True
    assert native_report["runtime"]["totals"]["calls"] == 3


def test_snapshot_has_plugin_issues():
    ok_snapshot = type("S", (), {"diagnostics": [], "plugins": [type("P", (), {"status": "loaded"})()]})()
    bad_snapshot = type("S", (), {"diagnostics": [{"level": "error"}], "plugins": []})()
    assert snapshot_has_plugin_issues(ok_snapshot) is False
    assert snapshot_has_plugin_issues(bad_snapshot) is True

