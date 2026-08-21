from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import httpx
import pytest


def test_python_sdk_imports_without_runtime_package() -> None:
    sdk_source = Path(__file__).parents[1] / "sdks" / "python" / "src"
    code = "import joyhousebot_sdk; print(joyhousebot_sdk.__all__)"
    result = subprocess.run(
        [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(sdk_source)!r}); {code}"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "AppClient" in result.stdout
    assert "OwnerClient" in result.stdout


@pytest.mark.asyncio
async def test_app_client_uses_v2_and_hides_installation_identity_from_run_body() -> None:
    from joyhousebot_sdk import AppClient

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v2/app-auth/token":
            return httpx.Response(
                200,
                json={"access_token": "token", "expires_at": "2099-01-01T00:00:00Z"},
            )
        return httpx.Response(
            202,
            json={
                "id": "run_1",
                "status": "queued",
                "progress": {"summary": "", "completed": 0, "total": 0},
            },
        )

    client = AppClient(
        "https://runtime.example",
        client_id="app_talent",
        client_secret="x" * 40,
        installation_id="appinst_1",
        transport=httpx.MockTransport(handler),
    )
    try:
        handle = await client.run(
            "entrypoint_1",
            {"candidate_id": "candidate_1"},
            idempotency_key="screen:candidate_1:v2",
        )
    finally:
        await client.close()

    assert handle.id == "run_1"
    assert requests[0].url.path == "/v2/app-auth/token"
    assert requests[1].url.path == "/v2/entrypoints/entrypoint_1/runs"
    body = requests[1].content.decode()
    assert "installation_id" not in body
    assert "grant_id" not in body


@pytest.mark.asyncio
async def test_owner_client_serializes_concurrent_token_exchange() -> None:
    from joyhousebot_sdk import OwnerClient

    token_requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_requests
        if request.url.path == "/v2/owner-auth/token":
            token_requests += 1
            await asyncio.sleep(0.02)
            return httpx.Response(
                200,
                json={
                    "access_token": "owner-access",
                    "refresh_token": "owner-refresh",
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            )
        if request.url.path == "/v2/apps":
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        raise AssertionError(request.url.path)

    client = OwnerClient(
        "https://runtime.example",
        client_id="joyhouse-product",
        subject_token="signed-owner-assertion",
        transport=httpx.MockTransport(handler),
    )
    try:
        await asyncio.gather(client.list_apps(), client.list_apps(), client.list_apps())
    finally:
        await client.close()

    assert token_requests == 1
