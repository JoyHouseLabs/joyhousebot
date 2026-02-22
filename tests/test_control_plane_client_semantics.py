import httpx
import pytest

import joyhousebot.control_plane.client as cp_client
from joyhousebot.control_plane.client import ControlPlaneClient, ControlPlaneClientError


def test_unwrap_response_prefers_data_result_payload() -> None:
    assert ControlPlaneClient._unwrap_response({"data": {"a": 1}}) == {"a": 1}
    assert ControlPlaneClient._unwrap_response({"result": {"b": 2}}) == {"b": 2}
    assert ControlPlaneClient._unwrap_response({"payload": {"c": 3}}) == {"c": 3}
    assert ControlPlaneClient._unwrap_response({"ok": True}) == {"ok": True}
    assert ControlPlaneClient._unwrap_response("bad") == {}


def test_extract_task_from_ws_message_supports_multiple_shapes() -> None:
    # openclaw-like event envelope
    msg = {"type": "event", "name": "task.assign", "payload": {"task_id": "t1"}}
    payload = ControlPlaneClient._extract_task_from_ws_message(msg)
    assert payload is not None
    assert payload["task_id"] == "t1"
    assert payload["id"] == "t1"
    assert payload["task_type"] == "unknown"
    assert payload["task_version"] == "1.0"
    assert payload["input"] == {}

    # legacy event/payload shape
    msg = {"event": "task.assign.v1", "payload": {"id": "t2", "taskType": "agent.prompt"}}
    payload = ControlPlaneClient._extract_task_from_ws_message(msg)
    assert payload is not None
    assert payload["task_id"] == "t2"
    assert payload["task_type"] == "agent.prompt"
    assert payload["taskType"] == "agent.prompt"

    # direct task envelope
    msg = {"task": {"task_id": "t3", "input": {"message": "hi"}}}
    payload = ControlPlaneClient._extract_task_from_ws_message(msg)
    assert payload is not None
    assert payload["task_id"] == "t3"
    assert payload["input"] == {"message": "hi"}

    # unrelated event should be ignored
    msg = {"type": "event", "name": "heartbeat", "payload": {"ok": True}}
    assert ControlPlaneClient._extract_task_from_ws_message(msg) is None


def test_extract_task_from_ws_message_supports_nested_task_payload() -> None:
    msg = {
        "type": "event",
        "name": "task.assign",
        "payload": {"task": {"runId": "run-1", "taskType": "agent.prompt", "params": {"message": "hi"}}},
    }
    payload = ControlPlaneClient._extract_task_from_ws_message(msg)
    assert payload is not None
    assert payload["task_id"] == "run-1"
    assert payload["task_type"] == "agent.prompt"
    assert payload["input"] == {"message": "hi"}


def test_claim_task_accepts_assignment_alias(monkeypatch) -> None:
    client = ControlPlaneClient("http://127.0.0.1:8000")
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, json_body=None: {"assignment": {"id": "x", "taskType": "agent.prompt"}},
    )
    task = client.claim_task(house_id="bot1")
    assert task is not None
    assert task["task_id"] == "x"
    assert task["task_type"] == "agent.prompt"


def test_claim_task_accepts_direct_runid(monkeypatch) -> None:
    client = ControlPlaneClient("http://127.0.0.1:8000")
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, json_body=None: {"runId": "r-1", "type": "agent.prompt", "params": {"text": "ok"}},
    )
    task = client.claim_task(house_id="bot1")
    assert task is not None
    assert task["task_id"] == "r-1"
    assert task["task_type"] == "agent.prompt"
    assert task["input"] == {"text": "ok"}


def test_normalize_register_response_accepts_openclaw_like_fields() -> None:
    payload = ControlPlaneClient._normalize_register_response(
        {
            "botId": "b1",
            "accessToken": "at",
            "refreshToken": "rt",
            "wsPath": "/ws/control",
        }
    )
    assert payload["bot_id"] == "b1"
    assert payload["house_id"] == "b1"
    assert payload["access_token"] == "at"
    assert payload["refresh_token"] == "rt"
    assert payload["ws_url"] == "/ws/control"


def test_build_ws_url_encodes_token() -> None:
    client = ControlPlaneClient("http://127.0.0.1:8000")
    ws = client.build_ws_url(ws_path="/ws", token="a b+c", server_url="http://127.0.0.1:8000")
    assert ws == "ws://127.0.0.1:8000/ws?token=a+b%2Bc"


def test_register_heartbeat_and_task_reports_send_dual_key_payloads(monkeypatch) -> None:
    client = ControlPlaneClient("http://127.0.0.1:8000")
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(method: str, path: str, json_body=None):
        calls.append((method, path, json_body))
        if path == "/houses/register":
            return {"houseId": "b1", "accessToken": "t", "wsPath": "/ws"}
        return {"ok": True}

    monkeypatch.setattr(client, "_request", fake_request)

    reg = client.register_house(
        house_name="bot-a",
        machine_fingerprint="mf-1",
        identity_public_key="pk",
        challenge="c",
        signature="s",
        owner_user_id="u1",
        capabilities=["task.execute.v1"],
        feature_flags=["ff1"],
    )
    assert reg["house_id"] == "b1"
    _, _, register_body = calls[-1]
    assert register_body["house_name"] == "bot-a"
    assert register_body["houseName"] == "bot-a"
    assert register_body["machine_fingerprint"] == "mf-1"
    assert register_body["machineFingerprint"] == "mf-1"
    assert register_body["feature_flags"] == ["ff1"]
    assert register_body["featureFlags"] == ["ff1"]
    assert register_body["owner_user_id"] == "u1"
    assert register_body["ownerUserId"] == "u1"

    client.heartbeat(house_id="b1", status="online", metrics={"x": 1})
    _, _, heartbeat_body = calls[-1]
    assert heartbeat_body["metrics"] == {"x": 1}
    assert heartbeat_body["metricsData"] == {"x": 1}

    client.report_task_progress(house_id="b1", task_id="t1", progress=0.5, detail="doing")
    _, _, progress_body = calls[-1]
    assert progress_body["detail"] == "doing"
    assert progress_body["message"] == "doing"

    client.report_task_result(house_id="b1", task_id="t1", result={"output": "ok"})
    _, _, result_body = calls[-1]
    assert result_body["result"] == {"output": "ok"}
    assert result_body["output"] == {"output": "ok"}

    client.report_task_failure(house_id="b1", task_id="t1", error={"code": "E"})
    _, _, fail_body = calls[-1]
    assert fail_body["error"] == {"code": "E"}
    assert fail_body["failure"] == {"code": "E"}


def test_ack_response_is_normalized_for_multiple_backend_shapes(monkeypatch) -> None:
    client = ControlPlaneClient("http://127.0.0.1:8000")
    responses = iter(
        [
            {"success": True},
            {"accepted": True},
            {"state": "completed"},
            {"status": "error"},
        ]
    )

    def fake_request(method: str, path: str, json_body=None):
        return next(responses)

    monkeypatch.setattr(client, "_request", fake_request)

    hb = client.heartbeat(house_id="b1", status="online", metrics={})
    assert hb["ok"] is True
    assert hb["success"] is True
    assert hb["status"] == "ok"

    p = client.report_task_progress(house_id="b1", task_id="t1", progress=0.2, detail="x")
    assert p["ok"] is True
    assert p["status"] == "ok"

    r = client.report_task_result(house_id="b1", task_id="t1", result={"x": 1})
    assert r["ok"] is True
    assert r["status"] == "completed"

    f = client.report_task_failure(house_id="b1", task_id="t1", error={"code": "E"})
    assert f["ok"] is False
    assert f["success"] is False
    assert f["status"] == "error"


def test_request_raises_normalized_http_error(monkeypatch) -> None:
    class FakeResponse:
        status_code = 503
        text = "service down"

        @staticmethod
        def json():
            return {"error": {"message": "backend unavailable"}}

    class FakeClient:
        def __init__(self, timeout: float):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method: str, url: str, json=None):
            return FakeResponse()

    monkeypatch.setattr(cp_client.httpx, "Client", FakeClient)
    client = ControlPlaneClient("http://127.0.0.1:8000")
    with pytest.raises(ControlPlaneClientError) as err:
        client._request("POST", "/bots/x/heartbeat", {"status": "online"})
    assert err.value.code == "CONTROL_PLANE_HTTP_ERROR"
    assert err.value.status_code == 503
    assert err.value.retryable is True
    assert "backend unavailable" in str(err.value)


def test_request_raises_normalized_bad_response_error(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        text = "<html>ok</html>"

        @staticmethod
        def json():
            raise ValueError("not json")

    class FakeClient:
        def __init__(self, timeout: float):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method: str, url: str, json=None):
            return FakeResponse()

    monkeypatch.setattr(cp_client.httpx, "Client", FakeClient)
    client = ControlPlaneClient("http://127.0.0.1:8000")
    with pytest.raises(ControlPlaneClientError) as err:
        client._request("POST", "/bots/x/tasks/claim", {})
    assert err.value.code == "CONTROL_PLANE_BAD_RESPONSE"
    assert err.value.retryable is False


def test_request_raises_normalized_network_error(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, timeout: float):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method: str, url: str, json=None):
            request = httpx.Request(method, url)
            raise httpx.ConnectError("boom", request=request)

    monkeypatch.setattr(cp_client.httpx, "Client", FakeClient)
    client = ControlPlaneClient("http://127.0.0.1:8000")
    with pytest.raises(ControlPlaneClientError) as err:
        client._request("POST", "/bots/x/tasks/claim", {})
    assert err.value.code == "CONTROL_PLANE_NETWORK_ERROR"
    assert err.value.retryable is True

