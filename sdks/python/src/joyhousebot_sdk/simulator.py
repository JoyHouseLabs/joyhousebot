"""Deterministic public v2 transport for independent App contract tests."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


class AppSimulator:
    """An in-process MockTransport, never a production Runtime substitute."""

    def __init__(
        self,
        *,
        client_id: str = "appclient_local",
        client_secret: str = "app-local-development-secret-00000000",
        installation_id: str = "appinst_local",
        app_id: str = "app.local",
        entrypoint_key: str = "default",
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.installation_id = installation_id
        self.app_id = app_id
        self.entrypoint_key = entrypoint_key
        self.entrypoint_id = f"ep:{installation_id}:{entrypoint_key}"
        self.access_token = "simulated-app-access-token"
        self.runs: dict[str, dict[str, Any]] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v2/app-auth/token":
            value = self._json(request)
            valid = (
                value.get("client_id") == self.client_id
                and value.get("client_secret") == self.client_secret
                and value.get("installation_id") == self.installation_id
            )
            if not valid:
                return self._error(request, 401, "invalid App credentials")
            return self._response(
                request,
                200,
                {
                    "access_token": self.access_token,
                    "token_type": "bearer",
                    "expires_at": (
                        datetime.now(timezone.utc) + timedelta(minutes=15)
                    ).isoformat(),
                    "scopes": value.get("scopes") or [],
                    "installation_id": self.installation_id,
                },
            )
        if request.headers.get("Authorization") != f"Bearer {self.access_token}":
            return self._error(request, 401, "invalid bearer token")
        if request.method == "GET" and path == "/v2/entrypoints":
            return self._response(
                request,
                200,
                {
                    "items": [
                        {
                            "id": self.entrypoint_id,
                            "key": self.entrypoint_key,
                            "app_id": self.app_id,
                            "name": "Simulator EntryPoint",
                            "description": "",
                            "input_schema": {"type": "object"},
                            "output_schema": None,
                            "interaction_mode": "background",
                            "permission_summary": [],
                            "risk_summary": [],
                        }
                    ],
                    "next_cursor": None,
                },
            )
        launch_path = f"/v2/entrypoints/{self.entrypoint_id}/runs"
        if request.method == "POST" and path == launch_path:
            request_key = str(request.headers.get("Idempotency-Key") or "")
            if not request_key:
                return self._error(request, 400, "Idempotency-Key required")
            run_id = "simrun_" + hashlib.sha256(request_key.encode()).hexdigest()[:24]
            self.runs.setdefault(run_id, self._run(run_id))
            return self._response(request, 202, self.runs[run_id])
        if request.method == "GET" and path.startswith("/v2/runs/"):
            run = self.runs.get(path.rsplit("/", 1)[-1])
            return self._response(request, 200, run) if run else self._error(
                request, 404, "Run not found"
            )
        return self._error(request, 404, "simulator route not found")

    @staticmethod
    def _run(run_id: str) -> dict[str, Any]:
        return {
            "id": run_id,
            "status": "succeeded",
            "progress": {"phase": "completed", "summary": "Completed", "completed": 1, "total": 1},
            "pending_action": None,
        }

    @staticmethod
    def _json(request: httpx.Request) -> dict[str, Any]:
        return dict(json.loads(request.content or b"{}"))

    @staticmethod
    def _response(request: httpx.Request, status: int, value: Any) -> httpx.Response:
        return httpx.Response(status, request=request, json=value)

    @classmethod
    def _error(cls, request: httpx.Request, status: int, message: str) -> httpx.Response:
        return cls._response(
            request,
            status,
            {
                "error": {
                    "code": "simulator_error",
                    "message": message,
                    "retryable": False,
                    "details": {},
                    "request_id": "simulator",
                }
            },
        )


__all__ = ["AppSimulator"]
