import pytest

from joyhousebot.api.rpc.connect_methods import try_handle_connect_method


class _Client:
    def __init__(self):
        self.scopes = set()
        self.role = "unknown"
        self.client_id = ""
        self.connected = False


class _Cfg:
    class Gateway:
        rpc_default_scopes = ["operator.read"]

        class ControlUi:
            allow_insecure_auth = True

        control_ui = ControlUi()

    gateway = Gateway()

    def get_default_agent_id(self):
        return "agent-main"


@pytest.mark.asyncio
async def test_connect_builds_hello_payload_and_sets_client_state():
    client = _Client()
    logs = []

    async def _overview():
        return {"alerts": [], "alertsSummary": {}, "alertsLifecycle": {}, "authProfiles": {"ok": True}}

    res = await try_handle_connect_method(
        method="connect",
        params={"role": "operator", "clientId": "ui-1"},
        client=client,
        connection_key="rpc_1",
        client_host=None,
        config=_Cfg(),
        get_connect_nonce=lambda _key: None,
        rate_limiter=None,
        load_persistent_state=lambda _k, _v: None,
        save_persistent_state=lambda _k, _v: None,
        hash_pairing_token=lambda t: t or "",
        now_ms=lambda: 123,
        resolve_agent=lambda _aid: object(),
        build_sessions_list_payload=lambda _agent, _cfg: {"sessions": [{"key": "main-2"}]},
        control_overview=_overview,
        gateway_methods_with_plugins=lambda: ["health", "status"],
        gateway_events=["chat", "cron"],
        presence_entries=lambda: [{"id": "p1"}],
        normalize_presence_entry=lambda e: {"id": e["id"]},
        build_actions_catalog=lambda: {"count": 0},
        resolve_canvas_host_url=lambda _cfg: "http://canvas",
        log_connect=lambda role, scopes, client_id: logs.append((role, scopes, client_id)),
    )
    assert res is not None and res[0] is True
    assert client.connected is True
    assert client.client_id == "ui-1"
    assert res[1]["snapshot"]["sessionDefaults"]["mainSessionKey"] == "main-2"
    assert logs and logs[0][0] == "operator"


@pytest.mark.asyncio
async def test_connect_node_role_prefers_node_id_for_client_id():
    client = _Client()

    async def _overview():
        return {"alerts": []}

    res = await try_handle_connect_method(
        method="connect",
        params={"role": "node", "nodeId": "node-1", "scopes": ["node.read"]},
        client=client,
        connection_key="rpc_2",
        client_host=None,
        config=_Cfg(),
        get_connect_nonce=lambda _key: None,
        rate_limiter=None,
        load_persistent_state=lambda _k, _v: None,
        save_persistent_state=lambda _k, _v: None,
        hash_pairing_token=lambda t: t or "",
        now_ms=lambda: 1,
        resolve_agent=lambda _aid: None,
        build_sessions_list_payload=lambda _agent, _cfg: {"sessions": []},
        control_overview=_overview,
        gateway_methods_with_plugins=lambda: [],
        gateway_events=[],
        presence_entries=lambda: [],
        normalize_presence_entry=lambda e: e,
        build_actions_catalog=lambda: {},
        resolve_canvas_host_url=lambda _cfg: "",
        log_connect=lambda *_: None,
    )
    assert res is not None and res[0] is True
    assert client.client_id == "node-1"
    assert client.scopes == {"node.read"}

