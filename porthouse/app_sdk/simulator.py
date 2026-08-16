"""In-process public App API simulator for independent App contract tests."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


class AppRuntimeSimulator:
    """A deterministic MockTransport, never a production Runtime substitute."""

    def __init__(
        self,
        *,
        client_id: str = "appclient_local",
        client_secret: str = "jhapp_local-development-secret",
        grant_id: str = "appgrant_local",
        installation_id: str = "appinst_local",
        app_id: str = "app.local",
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.grant_id = grant_id
        self.installation_id = installation_id
        self.app_id = app_id
        self.access_token = "simulated-app-access-token"
        self.runs: dict[str, dict[str, Any]] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v1/app-auth/token":
            value = self._json(request)
            if (
                value.get("client_id") != self.client_id
                or value.get("client_secret") != self.client_secret
                or value.get("grant_id") != self.grant_id
            ):
                return self._response(request, 401, {"detail": "invalid App credentials"})
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
            return self._response(request, 401, {"detail": "invalid bearer token"})
        if request.method == "GET" and path == "/v1/apps":
            return self._response(request, 200, {"items": [self._installation()]})
        launch_path = f"/v1/apps/{self.installation_id}/runs"
        if request.method == "POST" and path == launch_path:
            request_key = str(request.headers.get("Idempotency-Key") or "")
            if not request_key:
                return self._response(request, 400, {"detail": "Idempotency-Key required"})
            run_id = "simrun_" + hashlib.sha256(request_key.encode()).hexdigest()[:24]
            body = self._json(request)
            self.runs.setdefault(
                run_id,
                {
                    "run_id": run_id,
                    "status": "completed",
                    "kind": "agent",
                    "result": {
                        "content": f"simulated result for {body['input']['content']}",
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                },
            )
            return self._response(request, 202, self.runs[run_id])
        if request.method == "GET" and path.startswith("/v1/runs/"):
            run_id = path.rsplit("/", 1)[-1]
            run = self.runs.get(run_id)
            return self._response(
                request,
                200 if run else 404,
                run or {"detail": "Run not found"},
            )
        return self._response(request, 404, {"detail": "simulator route not found"})

    def _installation(self) -> dict[str, Any]:
        return {
            "installation_id": self.installation_id,
            "app_id": self.app_id,
            "version": "0.0.0-simulator",
            "status": "active",
            "manifest": {"entrypoints": [{"entrypoint_id": "default", "default": True}]},
        }

    @staticmethod
    def _json(request: httpx.Request) -> dict[str, Any]:
        import json

        return dict(json.loads(request.content or b"{}"))

    @staticmethod
    def _response(
        request: httpx.Request, status: int, value: dict[str, Any]
    ) -> httpx.Response:
        return httpx.Response(status, request=request, json=value)


__all__ = ["AppRuntimeSimulator"]
