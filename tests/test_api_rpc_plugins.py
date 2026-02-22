import pytest

from joyhousebot.api.rpc.plugins import try_handle_plugins_method
from joyhousebot.config.schema import Config


class _FakeSnapshot:
    def __init__(self, n: int):
        self.plugins = [{} for _ in range(n)]


class _FakeManager:
    class _Client:
        def setup_host(self, install_deps: bool, build_dist: bool, dry_run: bool):
            assert isinstance(install_deps, bool)
            assert isinstance(build_dist, bool)
            assert isinstance(dry_run, bool)
            if dry_run:
                return {"planned": [["npm", "install"]]}
            return {"executed": [{"command": ["npm", "install"], "ok": True}]}

    def __init__(self):
        self.client = self._Client()

    def list_plugins(self):
        return [{"id": "p1"}]

    def info(self, plugin_id: str):
        return {"id": plugin_id}

    def doctor(self):
        return {"ok": True}

    def status_report(self):
        return {"ok": True, "plugins": {"total": 1, "loaded": 1, "errored": 0}}

    def load(self, workspace_dir: str, config: dict, reload: bool):
        assert workspace_dir
        assert isinstance(config, dict)
        assert reload is True
        return _FakeSnapshot(2)

    def gateway_methods(self):
        return ["plugin.echo"]

    def http_dispatch(self, request: dict):
        if request.get("path") == "/ok":
            return {"ok": True, "status": 200}
        return {"ok": False, "error": {"code": "BAD", "message": "bad req"}}

    def cli_commands(self):
        return ["plugin.cmd"]

    def invoke_cli_command(self, command: str, payload: dict):
        if command == "ok":
            return {"ok": True, "result": payload}
        return {"ok": False, "error": {"code": "NO_CMD", "message": "missing"}}

    def channels_list(self):
        return ["c1"]

    def providers_list(self):
        return ["prov1"]

    def hooks_list(self):
        return [{"name": "h1"}]

    def start_services(self):
        return [{"id": "svc.a", "started": True, "error": ""}]

    def stop_services(self):
        return [{"id": "svc.a", "stopped": True, "error": ""}]


def _rpc_error(code: str, message: str, data=None):
    return {"code": code, "message": message, "data": data}


@pytest.mark.asyncio
async def test_plugins_list_and_reload_handlers():
    cfg = Config()
    state = {"plugin_manager": _FakeManager()}
    res = await try_handle_plugins_method(
        method="plugins.list",
        params={},
        config=cfg,
        app_state=state,
        rpc_error=_rpc_error,
    )
    assert res == (True, {"plugins": [{"id": "p1"}]}, None)

    reload_res = await try_handle_plugins_method(
        method="plugins.reload",
        params={},
        config=cfg,
        app_state=state,
        rpc_error=_rpc_error,
    )
    assert reload_res == (True, {"ok": True, "plugins": 2}, None)
    assert "plugin_snapshot" in state


@pytest.mark.asyncio
async def test_plugins_http_dispatch_error_mapping():
    cfg = Config()
    state = {"plugin_manager": _FakeManager()}
    res = await try_handle_plugins_method(
        method="plugins.http.dispatch",
        params={"request": {"path": "/bad"}},
        config=cfg,
        app_state=state,
        rpc_error=_rpc_error,
    )
    assert res is not None
    ok, payload, err = res
    assert ok is False
    assert payload is None
    assert err["code"] == "BAD"


@pytest.mark.asyncio
async def test_plugins_unhandled_method_returns_none():
    cfg = Config()
    state = {"plugin_manager": _FakeManager()}
    res = await try_handle_plugins_method(
        method="not.plugins",
        params={},
        config=cfg,
        app_state=state,
        rpc_error=_rpc_error,
    )
    assert res is None


@pytest.mark.asyncio
async def test_plugins_status_and_services_handlers():
    cfg = Config()
    state = {"plugin_manager": _FakeManager()}
    status_res = await try_handle_plugins_method(
        method="plugins.status",
        params={},
        config=cfg,
        app_state=state,
        rpc_error=_rpc_error,
    )
    assert status_res == (True, {"ok": True, "plugins": {"total": 1, "loaded": 1, "errored": 0}}, None)

    start_res = await try_handle_plugins_method(
        method="plugins.services.start",
        params={},
        config=cfg,
        app_state=state,
        rpc_error=_rpc_error,
    )
    assert start_res == (True, {"rows": [{"id": "svc.a", "started": True, "error": ""}]}, None)

    stop_res = await try_handle_plugins_method(
        method="plugins.services.stop",
        params={},
        config=cfg,
        app_state=state,
        rpc_error=_rpc_error,
    )
    assert stop_res == (True, {"rows": [{"id": "svc.a", "stopped": True, "error": ""}]}, None)

    setup_res = await try_handle_plugins_method(
        method="plugins.setup_host",
        params={"dryRun": True, "installDeps": True, "buildDist": True},
        config=cfg,
        app_state=state,
        rpc_error=_rpc_error,
    )
    assert setup_res == (True, {"planned": [["npm", "install"]]}, None)

