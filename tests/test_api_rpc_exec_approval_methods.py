import pytest

from joyhousebot.api.rpc.exec_approval_methods import try_handle_exec_approval_method


def _rpc_error(code: str, message: str, data=None):
    return {"code": code, "message": message, "data": data}


@pytest.mark.asyncio
async def test_exec_approval_request_two_phase_and_resolve():
    app_state = {"rpc_exec_approval_pending": {}, "rpc_exec_approval_futures": {}}
    saved = {}
    events = []

    def _load(name, default):
        return saved.get(name, default)

    def _save(name, value):
        saved[name] = value

    async def _broadcast(event, payload, roles=None):
        events.append((event, payload, roles))

    req = await try_handle_exec_approval_method(
        method="exec.approval.request",
        params={"id": "apr_1", "command": "ls", "twoPhase": True},
        app_state=app_state,
        client_id="op1",
        rpc_error=_rpc_error,
        cleanup_expired_exec_approvals=lambda: None,
        now_ms=lambda: 100,
        broadcast_rpc_event=_broadcast,
        load_persistent_state=_load,
        save_persistent_state=_save,
    )
    assert req is not None and req[0] is True
    assert req[1]["status"] == "accepted"

    resolve = await try_handle_exec_approval_method(
        method="exec.approval.resolve",
        params={"id": "apr_1", "decision": "allow-once"},
        app_state=app_state,
        client_id="op1",
        rpc_error=_rpc_error,
        cleanup_expired_exec_approvals=lambda: None,
        now_ms=lambda: 200,
        broadcast_rpc_event=_broadcast,
        load_persistent_state=_load,
        save_persistent_state=_save,
    )
    assert resolve == (True, {"ok": True}, None)
    assert any(item[0] == "exec.approval.resolved" for item in events)


@pytest.mark.asyncio
async def test_exec_approvals_get_set_node_scopes():
    app_state = {"rpc_exec_approval_pending": {}, "rpc_exec_approval_futures": {}}
    store = {}

    def _load(name, default):
        return store.get(name, default)

    def _save(name, value):
        store[name] = value

    async def _broadcast(_event, _payload, roles=None):
        return roles

    set_res = await try_handle_exec_approval_method(
        method="exec.approvals.set",
        params={"file": {"version": 1, "defaults": {"allow": []}, "agents": {}}},
        app_state=app_state,
        client_id=None,
        rpc_error=_rpc_error,
        cleanup_expired_exec_approvals=lambda: None,
        now_ms=lambda: 1,
        broadcast_rpc_event=_broadcast,
        load_persistent_state=_load,
        save_persistent_state=_save,
    )
    assert set_res == (True, {"ok": True}, None)

    get_res = await try_handle_exec_approval_method(
        method="exec.approvals.get",
        params={},
        app_state=app_state,
        client_id=None,
        rpc_error=_rpc_error,
        cleanup_expired_exec_approvals=lambda: None,
        now_ms=lambda: 1,
        broadcast_rpc_event=_broadcast,
        load_persistent_state=_load,
        save_persistent_state=_save,
    )
    assert get_res is not None and get_res[0] is True
    assert get_res[1]["file"]["version"] == 1


@pytest.mark.asyncio
async def test_exec_approvals_pending_returns_active_requests():
    now_ms_val = 5000

    def _now_ms():
        return now_ms_val

    app_state = {
        "rpc_exec_approval_pending": {
            "apr_a": {
                "id": "apr_a",
                "request": {"command": "ls -la"},
                "createdAtMs": 1000,
                "expiresAtMs": 10000,
                "status": "pending",
                "decision": None,
            },
            "apr_expired": {
                "id": "apr_expired",
                "request": {"command": "rm -rf /"},
                "createdAtMs": 1,
                "expiresAtMs": 2,
                "status": "pending",
                "decision": None,
            },
        },
        "rpc_exec_approval_futures": {},
    }
    store = {}

    def _load(name, default):
        return store.get(name, default)

    def _save(name, value):
        store[name] = value

    async def _broadcast(_event, _payload, roles=None):
        pass

    res = await try_handle_exec_approval_method(
        method="exec.approvals.pending",
        params={},
        app_state=app_state,
        client_id=None,
        rpc_error=_rpc_error,
        cleanup_expired_exec_approvals=lambda: None,
        now_ms=_now_ms,
        broadcast_rpc_event=_broadcast,
        load_persistent_state=_load,
        save_persistent_state=_save,
    )
    assert res is not None and res[0] is True
    pending_list = res[1]["pending"]
    assert isinstance(pending_list, list)
    ids = [p["id"] for p in pending_list]
    assert "apr_a" in ids
    assert "apr_expired" not in ids  # expired

