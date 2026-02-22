"""HTTP client for backend bot control plane."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable
from urllib.parse import urlencode

import httpx
import websockets


class ControlPlaneClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "CONTROL_PLANE_ERROR",
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable


class ControlPlaneClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _normalize_base(self) -> str:
        if self.base_url.endswith("/api/v1"):
            return self.base_url
        return f"{self.base_url}/api/v1"

    @staticmethod
    def _unwrap_response(body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            return {}
        data = body.get("data")
        if isinstance(data, dict):
            return data
        result = body.get("result")
        if isinstance(result, dict):
            return result
        payload = body.get("payload")
        if isinstance(payload, dict):
            return payload
        return body

    @staticmethod
    def _coerce_task_payload(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        return {}

    @staticmethod
    def _first_non_empty_str(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @classmethod
    def _normalize_task_payload(cls, payload: Any) -> dict[str, Any]:
        raw = cls._coerce_task_payload(payload)
        task_id = cls._first_non_empty_str(raw, ("task_id", "taskId", "id", "run_id", "runId"))
        task_type = cls._first_non_empty_str(raw, ("task_type", "taskType", "type")) or "unknown"
        task_version = cls._first_non_empty_str(raw, ("task_version", "taskVersion", "version")) or "1.0"
        input_payload = raw.get("input")
        if not isinstance(input_payload, dict):
            input_payload = raw.get("payload")
        if not isinstance(input_payload, dict):
            input_payload = raw.get("data")
        if not isinstance(input_payload, dict):
            input_payload = raw.get("params")
        if not isinstance(input_payload, dict):
            input_payload = {}
        normalized = dict(raw)
        if task_id:
            normalized["task_id"] = task_id
            normalized["id"] = task_id
        normalized["task_type"] = task_type
        normalized["taskType"] = task_type
        normalized["task_version"] = task_version
        normalized["taskVersion"] = task_version
        normalized["input"] = input_payload
        return normalized

    def normalize_task(self, payload: Any) -> dict[str, Any]:
        """Public normalization entry used by worker paths."""
        return self._normalize_task_payload(payload)

    @classmethod
    def _normalize_register_response(cls, payload: Any) -> dict[str, Any]:
        raw = cls._coerce_task_payload(payload)
        house_id = cls._first_non_empty_str(raw, ("house_id", "houseId", "id"))
        bot_id = cls._first_non_empty_str(raw, ("bot_id", "botId"))
        if not house_id and bot_id:
            house_id = bot_id
        access_token = cls._first_non_empty_str(raw, ("access_token", "accessToken", "token"))
        refresh_token = cls._first_non_empty_str(raw, ("refresh_token", "refreshToken"))
        ws_url = cls._first_non_empty_str(raw, ("ws_url", "wsUrl", "ws_path", "wsPath"))
        normalized = dict(raw)
        if house_id:
            normalized["house_id"] = house_id
        if bot_id:
            normalized["bot_id"] = bot_id
        if access_token:
            normalized["access_token"] = access_token
        if refresh_token:
            normalized["refresh_token"] = refresh_token
        if ws_url:
            normalized["ws_url"] = ws_url
        return normalized

    @classmethod
    def _extract_task_from_ws_message(cls, msg: Any) -> dict[str, Any] | None:
        if not isinstance(msg, dict):
            return None
        if isinstance(msg.get("task"), dict):
            return cls._normalize_task_payload(msg.get("task"))
        msg_type = str(msg.get("type") or "")
        event = str(msg.get("event") or msg.get("name") or "")
        payload = cls._coerce_task_payload(msg.get("payload") or msg.get("data"))
        if isinstance(payload.get("task"), dict):
            return cls._normalize_task_payload(payload.get("task"))
        if event.startswith("task.assign"):
            return cls._normalize_task_payload(payload)
        if msg_type == "event":
            evt_name = str(msg.get("name") or event)
            if evt_name.startswith("task.assign"):
                return cls._normalize_task_payload(payload)
        return None

    def _request(self, method: str, path: str, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._normalize_base()}{path}"
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.request(method, url, json=json_body)
        except httpx.TimeoutException as exc:
            raise ControlPlaneClientError(
                f"control plane timeout: {method} {path}",
                code="CONTROL_PLANE_TIMEOUT",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise ControlPlaneClientError(
                f"control plane network error: {method} {path}: {exc}",
                code="CONTROL_PLANE_NETWORK_ERROR",
                retryable=True,
            ) from exc

        status_code = int(getattr(resp, "status_code", 0) or 0)
        if status_code >= 400:
            body: Any = None
            try:
                body = resp.json()
            except Exception:
                body = None
            message = self._extract_error_message(body, str(getattr(resp, "text", "") or ""))
            raise ControlPlaneClientError(
                f"control plane http error {status_code}: {message}",
                code="CONTROL_PLANE_HTTP_ERROR",
                status_code=status_code,
                retryable=self._is_retryable_status(status_code),
            )

        try:
            body = resp.json()
        except Exception as exc:
            raise ControlPlaneClientError(
                f"control plane bad response: non-json body for {method} {path}",
                code="CONTROL_PLANE_BAD_RESPONSE",
                status_code=status_code or None,
                retryable=False,
            ) from exc
        return self._unwrap_response(body)

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        return status_code >= 500 or status_code in {408, 409, 425, 429}

    @staticmethod
    def _extract_error_message(body: Any, fallback_text: str) -> str:
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                msg = err.get("message")
                if isinstance(msg, str) and msg.strip():
                    return msg.strip()
            for key in ("message", "detail", "error"):
                val = body.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        text = (fallback_text or "").strip()
        if text:
            return text[:200]
        return "request failed"

    @staticmethod
    def _with_aliases(payload: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
        out = dict(payload)
        for src, dst in aliases.items():
            if src in out and dst not in out:
                out[dst] = out[src]
        return out

    @classmethod
    def _normalize_ack_response(cls, payload: Any) -> dict[str, Any]:
        raw = cls._coerce_task_payload(payload)
        normalized = dict(raw)
        status = cls._first_non_empty_str(raw, ("status", "state"))
        ok_value = raw.get("ok")
        success_value = raw.get("success")
        accepted = raw.get("accepted")
        if isinstance(ok_value, bool):
            ok = ok_value
        elif isinstance(success_value, bool):
            ok = success_value
        elif isinstance(accepted, bool):
            ok = accepted
        elif status:
            ok = status.lower() in {"ok", "accepted", "done", "success", "completed"}
        else:
            ok = True
        if not status:
            status = "ok" if ok else "error"
        normalized["ok"] = ok
        normalized["success"] = ok
        normalized["status"] = status
        return normalized

    def build_ws_url(self, *, ws_path: str, token: str, server_url: str | None = None) -> str:
        """
        Build absolute websocket URL from server base and returned ws_path.
        """
        base = (server_url or self.base_url).rstrip("/")
        if ws_path.startswith("ws://") or ws_path.startswith("wss://"):
            prefix = ws_path
        else:
            # Map http(s) base to ws(s)
            if base.startswith("https://"):
                ws_base = "wss://" + base[len("https://") :]
            elif base.startswith("http://"):
                ws_base = "ws://" + base[len("http://") :]
            else:
                ws_base = base
            prefix = f"{ws_base}{ws_path if ws_path.startswith('/') else '/' + ws_path}"
        if not token:
            return prefix
        delimiter = "&" if "?" in prefix else "?"
        return f"{prefix}{delimiter}{urlencode({'token': token})}"

    def register_house(
        self,
        *,
        house_name: str,
        machine_fingerprint: str,
        identity_public_key: str,
        challenge: str,
        signature: str,
        owner_user_id: str | None,
        capabilities: list[str],
        feature_flags: list[str],
    ) -> dict[str, Any]:
        """
        Register local house to backend control plane.

        Uses Ed25519 identity (identity_public_key + signature). A house can have
        multiple agents, tools, and skills configured.
        """
        payload = {
            "house_name": house_name,
            "machine_fingerprint": machine_fingerprint,
            "identity_public_key": identity_public_key,
            "challenge": challenge,
            "signature": signature,
            "owner_user_id": owner_user_id,
            "capabilities": capabilities,
            "feature_flags": feature_flags,
        }
        payload = self._with_aliases(
            payload,
            {
                "house_name": "houseName",
                "machine_fingerprint": "machineFingerprint",
                "feature_flags": "featureFlags",
                "identity_public_key": "identityPublicKey",
                "owner_user_id": "ownerUserId",
            },
        )
        data = self._request("POST", "/houses/register", payload)
        return self._normalize_register_response(data)

    def get_house(self, house_id: str) -> dict[str, Any]:
        """Get house details (including owner_user_id)."""
        return self._request("GET", f"/houses/{house_id}") or {}

    def bind_house(self, house_id: str, owner_user_id: str) -> dict[str, Any]:
        """Bind house to a user (owner_user_id)."""
        body = self._with_aliases({"owner_user_id": owner_user_id}, {"owner_user_id": "ownerUserId"})
        return self._request("POST", f"/houses/{house_id}/bind", body) or {}

    def heartbeat(self, *, house_id: str, status: str, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
        body = self._with_aliases({"status": status, "metrics": metrics or {}}, {"metrics": "metricsData"})
        data = self._request("POST", f"/houses/{house_id}/heartbeat", body)
        return self._normalize_ack_response(data)

    def claim_task(self, *, house_id: str) -> dict[str, Any] | None:
        data = self._request("POST", f"/houses/{house_id}/tasks/claim", {})
        task = data.get("task")
        if isinstance(task, dict):
            return self._normalize_task_payload(task)
        if isinstance(data.get("assignment"), dict):
            return self._normalize_task_payload(data["assignment"])
        # Some control planes return the task object directly.
        if any(k in data for k in ("task_id", "id", "taskType", "task_type", "runId", "run_id")):
            return self._normalize_task_payload(data)
        return None

    def report_task_progress(
        self,
        *,
        house_id: str,
        task_id: str,
        progress: float | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        body = self._with_aliases({"progress": progress, "detail": detail}, {"detail": "message"})
        data = self._request(
            "POST",
            f"/houses/{house_id}/tasks/{task_id}/progress",
            body,
        )
        return self._normalize_ack_response(data)

    def report_task_result(self, *, house_id: str, task_id: str, result: dict[str, Any]) -> dict[str, Any]:
        body = self._with_aliases({"result": result}, {"result": "output"})
        data = self._request("POST", f"/houses/{house_id}/tasks/{task_id}/result", body)
        return self._normalize_ack_response(data)

    def report_task_failure(self, *, house_id: str, task_id: str, error: dict[str, Any]) -> dict[str, Any]:
        body = self._with_aliases({"error": error}, {"error": "failure"})
        data = self._request("POST", f"/houses/{house_id}/tasks/{task_id}/fail", body)
        return self._normalize_ack_response(data)

    async def run_ws_listener(
        self,
        *,
        ws_url: str,
        on_task: Callable[[dict[str, Any]], None],
        stop_event: asyncio.Event,
        reconnect_delay: float = 2.0,
    ) -> None:
        """
        Listen control-plane websocket and forward task.assign payloads.
        """
        while not stop_event.is_set():
            try:
                async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                    while not stop_event.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        if not isinstance(raw, str):
                            continue
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        task_payload = self._extract_task_from_ws_message(msg)
                        if task_payload is not None:
                            on_task(task_payload)
            except Exception:
                await asyncio.sleep(max(0.2, reconnect_delay))

