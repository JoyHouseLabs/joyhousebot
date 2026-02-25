import uuid

import pytest

import joyhousebot.api.server as server
from joyhousebot.api.server import RpcClientState, _handle_rpc_request, _load_persistent_state, app_state
from joyhousebot.config.loader import load_config
from joyhousebot.config.schema import AuthProfileConfig


async def _rpc_call(client: RpcClientState, method: str, params: dict | None = None):
    return await _handle_rpc_request(
        {"type": "req", "id": "test", "method": method, "params": params or {}},
        client,
        f"conn-{client.client_id or client.role}",
    )


@pytest.mark.asyncio
@pytest.mark.requires_pairing
async def test_chat_send_returns_started_then_agent_wait_gets_final(monkeypatch):
    old = dict(app_state)
    try:
        app_state["config"] = load_config()

        async def fake_chat(msg):
            return {"ok": True, "response": f"echo:{msg.message}"}

        monkeypatch.setattr(server, "chat", fake_chat)
        op = RpcClientState(connected=True, role="operator", scopes={"operator.admin"}, client_id="op")

        ok, payload, err = await _rpc_call(
            op,
            "chat.send",
            {"message": "hello", "sessionKey": "semantics-main"},
        )
        assert ok, err
        assert payload["status"] == "started"
        run_id = payload["runId"]

        ok, payload, err = await _rpc_call(op, "agent.wait", {"runId": run_id, "timeoutMs": 5_000})
        assert ok, err
        assert payload["runId"] == run_id
        assert payload["status"] == "ok"
        assert payload["endedAt"] is not None
    finally:
        app_state.clear()
        app_state.update(old)


@pytest.mark.asyncio
async def test_agent_and_chat_ack_semantics_inflight(monkeypatch):
    """With session serialization: same session allows only one run; second request returns in_flight with current runId."""
    old = dict(app_state)
    try:
        app_state["config"] = load_config()

        async def fake_chat(msg):
            return {"ok": True, "response": f"done:{msg.message}"}

        monkeypatch.setattr(server, "chat", fake_chat)
        op = RpcClientState(connected=True, role="operator", scopes={"operator.admin"}, client_id="op")

        ok, payload, err = await _rpc_call(
            op,
            "agent",
            {"message": "hello", "sessionKey": "semantics-main", "idempotencyKey": "rid-agent"},
        )
        assert ok, err
        assert payload["status"] == "accepted"
        assert payload["runId"] == "rid-agent"

        # Same session, different run_id: lane queue enqueues; returns queued with runId (rid-chat) and position.
        ok, payload, err = await _rpc_call(
            op,
            "chat.send",
            {"message": "hello", "sessionKey": "semantics-main", "idempotencyKey": "rid-chat"},
        )
        assert ok, err
        assert payload["status"] == "queued"
        assert payload["runId"] == "rid-chat"
        assert payload["sessionKey"] == "semantics-main"
        assert payload.get("position") == 1
        assert payload.get("queueDepth") == 1

        # Same session again: second queued entry.
        ok, payload, err = await _rpc_call(
            op,
            "chat.send",
            {"message": "hello", "sessionKey": "semantics-main", "idempotencyKey": "rid-chat"},
        )
        assert ok, err
        assert payload["status"] == "queued"
        assert payload["runId"] == "rid-chat"
        assert payload.get("position") == 2
        assert payload.get("queueDepth") == 2
    finally:
        app_state.clear()
        app_state.update(old)


@pytest.mark.asyncio
@pytest.mark.requires_pairing
async def test_chat_send_expect_final_returns_final_payload(monkeypatch):
    old = dict(app_state)
    try:
        app_state["config"] = load_config()

        async def fake_chat(msg):
            return {"ok": True, "response": f"done:{msg.message}"}

        monkeypatch.setattr(server, "chat", fake_chat)
        op = RpcClientState(connected=True, role="operator", scopes={"operator.admin"}, client_id="op")

        ok, payload, err = await _rpc_call(
            op,
            "chat.send",
            {"message": "hello", "sessionKey": "semantics-main", "expectFinal": True, "timeoutMs": 5_000},
        )
        assert ok, err
        assert payload["status"] == "ok"
        assert payload["runId"]
        assert payload["sessionKey"] == "semantics-main"
    finally:
        app_state.clear()
        app_state.update(old)


@pytest.mark.asyncio
async def test_auth_profiles_status_returns_cooldown_observability(monkeypatch):
    old = dict(app_state)
    try:
        cfg = load_config()
        cfg.auth.profiles = {
            "p-openai": AuthProfileConfig(provider="openai", enabled=True),
        }
        app_state["config"] = cfg
        monkeypatch.setattr(
            "joyhousebot.agent.auth_profiles.load_profile_usage",
            lambda: {"p-openai": {"failure_count": 3, "cooldown_until_ms": 9999999999999}},
        )
        op = RpcClientState(connected=True, role="operator", scopes={"operator.read"}, client_id="op")

        ok, payload, err = await _rpc_call(op, "auth.profiles.status", {})
        assert ok, err
        assert isinstance(payload.get("ts"), int)
        assert payload.get("status") in {"ok", "degraded", "down", "empty"}
        assert isinstance(payload.get("providers"), list)
        assert isinstance(payload.get("profiles"), list)
        p = next(x for x in payload["profiles"] if x["profileId"] == "p-openai")
        assert p["provider"] == "openai"
        assert p["available"] is False
        assert p["state"] == "cooldown"
        assert p["failureCount"] == 3
    finally:
        app_state.clear()
        app_state.update(old)


@pytest.mark.asyncio
@pytest.mark.requires_pairing
async def test_connect_snapshot_includes_auth_alerts(monkeypatch):
    old = dict(app_state)
    try:
        cfg = load_config()
        cfg.auth.profiles = {
            "p-openai": AuthProfileConfig(provider="openai", enabled=True),
        }
        app_state["config"] = cfg
        monkeypatch.setattr(
            "joyhousebot.agent.auth_profiles.load_profile_usage",
            lambda: {"p-openai": {"failure_count": 2, "cooldown_until_ms": 9999999999999}},
        )
        client = RpcClientState()
        ok, payload, err = await _rpc_call(
            client,
            "connect",
            {"role": "operator", "scopes": ["operator.read"], "clientId": "op"},
        )
        assert ok, err
        snapshot = payload["snapshot"]
        assert isinstance(snapshot.get("alerts"), list)
        assert any(a.get("code") == "AUTH_PROFILES_DOWN" for a in snapshot["alerts"])
        assert snapshot.get("authProfiles", {}).get("status") == "down"
        assert snapshot.get("alerts") == snapshot.get("health", {}).get("alerts")
        assert snapshot.get("alertsSummary") == snapshot.get("health", {}).get("alertsSummary")
        assert snapshot.get("alertsLifecycle") == snapshot.get("health", {}).get("alertsLifecycle")
        assert isinstance(snapshot.get("actionsCatalog", {}).get("actions"), list)
        assert snapshot.get("actionsCatalog") == snapshot.get("health", {}).get("actionsCatalog")
        assert int(snapshot.get("actionsCatalog", {}).get("count") or 0) >= 1
        assert int(snapshot.get("alertsSummary", {}).get("critical") or 0) >= 1
    finally:
        app_state.clear()
        app_state.update(old)


@pytest.mark.asyncio
async def test_control_overview_aggregates_operational_alerts(monkeypatch):
    old = dict(app_state)
    try:
        cfg = load_config()
        cfg.auth.profiles = {}
        cfg.channels.telegram.enabled = True
        app_state["config"] = cfg
        app_state["channel_manager"] = None
        app_state["cron_service"] = None
        monkeypatch.setattr(
            server,
            "_load_persistent_state",
            lambda name, default: {"running": True, "updatedAtMs": 1, "server": "http://127.0.0.1:8000", "botId": "bot-1"}
            if name == "control_plane.worker_status"
            else default,
        )
        payload = await server.control_overview()
        assert payload["health"] is False
        alerts = payload.get("alerts", [])
        codes = {a.get("code") for a in alerts}
        assert "CHANNELS_UNAVAILABLE_ALL" in codes
        assert "CONTROL_PLANE_WORKER_STALE" in codes
        channels_alert = next(a for a in alerts if a.get("code") == "CHANNELS_UNAVAILABLE_ALL")
        assert channels_alert.get("action", {}).get("name") == "diagnoseChannels"
        assert channels_alert.get("action", {}).get("type") == "run_command"
        assert isinstance(channels_alert.get("action", {}).get("params", {}).get("channels"), list)
        worker_alert = next(a for a in alerts if a.get("code") == "CONTROL_PLANE_WORKER_STALE")
        assert worker_alert.get("action", {}).get("type") == "run_command"
        assert "--server" in worker_alert.get("action", {}).get("args", [])
        assert isinstance(payload.get("alertsSummary"), dict)
        assert isinstance(payload.get("actionsCatalog"), dict)
        assert int(payload["alertsSummary"].get("critical") or 0) >= 1
        assert all(a.get("dedupeKey") for a in alerts)
        assert all(a.get("group") for a in alerts)
        assert all(a.get("canonicalCode") for a in alerts)
        assert all(isinstance(a.get("aliases"), list) for a in alerts)
        assert all(isinstance(a.get("action"), dict) for a in alerts)
        assert all(isinstance(a.get("actionSchema"), dict) for a in alerts)
        assert all(isinstance(a.get("executionPolicy"), dict) for a in alerts)
        assert isinstance(payload.get("alertsLifecycle"), dict)
        assert int(payload["alertsLifecycle"].get("activeCount") or 0) >= 1
        priorities = [int(a.get("priority") or 0) for a in alerts]
        assert priorities == sorted(priorities, reverse=True)
    finally:
        app_state.clear()
        app_state.update(old)


def test_normalize_operational_alerts_dedupes_and_ranks():
    raw = [
        {
            "source": "auth",
            "category": "provider",
            "code": "AUTH_PROVIDER_DOWN",
            "level": "warning",
            "provider": "openai",
        },
        {
            "source": "auth",
            "category": "provider",
            "code": "AUTH_PROVIDER_DOWN",
            "level": "critical",
            "provider": "openai",
        },
        {"source": "cron", "category": "scheduler", "code": "CRON_SCHEDULER_STALLED", "level": "warning"},
    ]
    alerts = server._normalize_operational_alerts(raw)
    assert len(alerts) == 2
    assert alerts[0]["code"] == "AUTH_PROVIDER_DOWN"
    assert alerts[0]["level"] == "critical"
    assert alerts[0]["canonicalCode"] == "AUTH.PROVIDER.DOWN"
    assert alerts[0]["action"]["type"] == "navigate"
    assert alerts[0]["action"]["name"] == "openPage"
    assert alerts[0]["action"]["params"]["provider"] == "openai"
    assert alerts[0]["executionPolicy"]["riskLevel"] == "low"
    assert "required" in alerts[0]["actionSchema"]
    assert alerts[1]["code"] == "CRON_SCHEDULER_STALLED"
    assert alerts[1]["action"]["type"] == "open_url"
    assert alerts[1]["action"]["name"] == "openCronOverview"


@pytest.mark.asyncio
async def test_actions_catalog_rpc_method():
    old = dict(app_state)
    try:
        app_state["config"] = load_config()
        op = RpcClientState(connected=True, role="operator", scopes={"operator.read"}, client_id="op")
        ok, payload, err = await _rpc_call(op, "actions.catalog", {})
        assert ok, err
        assert payload["version"] == 2
        assert "run_command" in payload.get("supportedActionTypes", [])
        assert payload.get("supportsBatchValidate") is True
        assert int(payload["count"]) == len(payload["actions"])
        first = payload["actions"][0]
        assert isinstance(first.get("schema"), dict)
        assert isinstance(first.get("validationRule"), dict)
        assert isinstance(first.get("executionPolicy"), dict)
        assert any(a.get("canonicalCode") == "AUTH.UNAVAILABLE.ALL" for a in payload["actions"])
    finally:
        app_state.clear()
        app_state.update(old)


@pytest.mark.asyncio
async def test_actions_validate_rpc_method_success_and_reject():
    old = dict(app_state)
    try:
        app_state["config"] = load_config()
        op = RpcClientState(connected=True, role="operator", scopes={"operator.read"}, client_id="op")
        ok, payload, err = await _rpc_call(
            op,
            "actions.validate",
            {"code": "AUTH_PROVIDER_DOWN", "action": {"type": "navigate", "target": "settings.auth.provider"}},
        )
        assert ok, err
        assert payload["ok"] is True
        assert payload["reason"] == "ok"

        ok, payload, err = await _rpc_call(
            op,
            "actions.validate",
            {"code": "AUTH_PROVIDER_DOWN", "action": {"type": "navigate", "target": "malicious.route"}},
        )
        assert not ok
        assert err["code"] == "INVALID_REQUEST"
    finally:
        app_state.clear()
        app_state.update(old)


@pytest.mark.asyncio
async def test_actions_validate_batch_rpc_method():
    old = dict(app_state)
    try:
        app_state["config"] = load_config()
        op = RpcClientState(connected=True, role="operator", scopes={"operator.read"}, client_id="op")
        ok, payload, err = await _rpc_call(
            op,
            "actions.validate.batch",
            {
                "items": [
                    {"code": "AUTH_PROVIDER_DOWN", "action": {"type": "navigate", "target": "settings.auth.provider"}},
                    {"code": "AUTH_PROVIDER_DOWN", "action": {"type": "navigate", "target": "bad.route"}},
                ]
            },
        )
        assert ok, err
        assert payload["total"] == 2
        assert payload["valid"] == 1
        assert payload["invalid"] == 1
        assert payload["ok"] is False
        assert payload["results"][0]["ok"] is True
        assert payload["results"][1]["ok"] is False
    finally:
        app_state.clear()
        app_state.update(old)


@pytest.mark.asyncio
async def test_actions_validate_batch_lifecycle_rpc_method():
    old = dict(app_state)
    try:
        cfg = load_config()
        cfg.channels.telegram.enabled = True
        app_state["config"] = cfg
        app_state["channel_manager"] = None
        app_state["cron_service"] = None
        op = RpcClientState(connected=True, role="operator", scopes={"operator.read"}, client_id="op")
        ok, payload, err = await _rpc_call(
            op,
            "actions.validate.batch.lifecycle",
            {"items": [{"code": "AUTH_PROVIDER_DOWN", "action": {"type": "navigate", "target": "settings.auth.provider"}}]},
        )
        assert ok, err
        assert payload["ok"] in {True, False}
        assert isinstance(payload.get("validation"), dict)
        assert isinstance(payload.get("alertsSummary"), dict)
        assert isinstance(payload.get("alertsLifecycle"), dict)
    finally:
        app_state.clear()
        app_state.update(old)


@pytest.mark.asyncio
@pytest.mark.requires_pairing
async def test_rpc_e2e_connect_status_actions_validate_lifecycle():
    old = dict(app_state)
    try:
        cfg = load_config()
        app_state["config"] = cfg
        client = RpcClientState()

        ok, payload, err = await _rpc_call(
            client,
            "connect",
            {"role": "operator", "scopes": ["operator.read"], "clientId": "e2e-op"},
        )
        assert ok, err
        snapshot = payload["snapshot"]
        assert isinstance(snapshot.get("alertsSummary"), dict)
        assert isinstance(snapshot.get("actionsCatalog"), dict)
        assert isinstance(snapshot.get("alertsLifecycle"), dict)

        ok, payload, err = await _rpc_call(client, "status", {})
        assert ok, err
        assert isinstance(payload.get("alerts"), list)
        assert isinstance(payload.get("alertsSummary"), dict)

        ok, payload, err = await _rpc_call(client, "actions.catalog", {})
        assert ok, err
        assert int(payload.get("count") or 0) >= 1

        ok, payload, err = await _rpc_call(
            client,
            "actions.validate",
            {"code": "AUTH_PROVIDER_DOWN", "action": {"type": "navigate", "target": "settings.auth.provider"}},
        )
        assert ok, err
        assert payload["ok"] is True

        ok, payload, err = await _rpc_call(client, "alerts.lifecycle", {})
        assert ok, err
        assert "active" in payload
        assert "resolvedRecent" in payload
    finally:
        app_state.clear()
        app_state.update(old)


@pytest.mark.asyncio
async def test_alerts_lifecycle_rpc_method():
    old = dict(app_state)
    try:
        app_state["config"] = load_config()
        op = RpcClientState(connected=True, role="operator", scopes={"operator.read"}, client_id="op")
        ok, payload, err = await _rpc_call(op, "alerts.lifecycle", {})
        assert ok, err
        assert "active" in payload
        assert "resolvedRecent" in payload
    finally:
        app_state.clear()
        app_state.update(old)


def test_apply_alerts_lifecycle_tracks_resolve(monkeypatch):
    state = {"active": {}, "resolvedRecent": [], "lastUpdatedMs": 0}

    monkeypatch.setattr(server, "_load_alerts_lifecycle_state", lambda: state)
    monkeypatch.setattr(server, "_save_alerts_lifecycle_state", lambda s: state.update(s))

    alerts = [{"dedupeKey": "a:b:c:", "code": "X", "canonicalCode": "X", "source": "a", "category": "b", "level": "warning"}]
    active_alerts, lifecycle = server._apply_alerts_lifecycle(alerts)
    assert len(active_alerts) == 1
    assert lifecycle["activeCount"] == 1

    active_alerts, lifecycle = server._apply_alerts_lifecycle([])
    assert len(active_alerts) == 0
    assert lifecycle["activeCount"] == 0
    assert lifecycle["resolvedRecentCount"] >= 1


@pytest.mark.asyncio
async def test_node_pair_verify_uses_hashed_token_storage():
    old = dict(app_state)
    try:
        app_state["config"] = load_config()
        op = RpcClientState(connected=True, role="operator", scopes={"operator.admin"}, client_id="op")
        node_id = f"node-{uuid.uuid4().hex[:8]}"
        node = RpcClientState(connected=True, role="node", scopes=set(), client_id=node_id)

        ok, _, err = await _rpc_call(
            node,
            "node.pair.request",
            {"nodeId": node_id, "displayName": "Node"},
        )
        assert ok, err

        ok, payload, err = await _rpc_call(op, "node.pair.list", {})
        assert ok, err
        req = next(p for p in payload["pending"] if p.get("deviceId") == node_id)

        ok, payload, err = await _rpc_call(op, "node.pair.approve", {"requestId": req["requestId"]})
        assert ok, err
        token = payload["token"]

        token_map = _load_persistent_state("rpc.node_tokens", {})
        rec = token_map.get(node_id)
        assert isinstance(rec, dict)
        assert rec.get("hash")
        assert rec["hash"] != token

        ok, payload, err = await _rpc_call(node, "node.pair.verify", {"nodeId": node_id, "token": token})
        assert ok, err
        assert payload == {"ok": True, "nodeId": node_id}
    finally:
        app_state.clear()
        app_state.update(old)
