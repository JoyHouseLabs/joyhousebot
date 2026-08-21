from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from joyhousebot.domain.owner_delegation import OWNER_TOKEN_AUDIENCE
from tests.support.postgres_store import PostgresTestStore


def _owner_client(store: PostgresTestStore) -> tuple[str, Ed25519PrivateKey]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    client_id = "joyhouse-product"
    store.create_owner_client(
        client_id=client_id,
        name="JoyHouse Product Gateway",
        issuer="https://api.joyhouse.me",
        public_key_pem=public_key,
        algorithm="EdDSA",
        allowed_scopes=["apps.read", "apps.launch", "runs.read", "runs.write"],
        actor_id="test",
    )
    return client_id, private_key


def _assertion(
    private_key: Ed25519PrivateKey,
    *,
    user_id: str = "owner-a",
    jti: str | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "iss": "https://api.joyhouse.me",
            "sub": user_id,
            "aud": OWNER_TOKEN_AUDIENCE,
            "iat": now,
            "exp": now + timedelta(minutes=2),
            "jti": jti or f"assertion-{uuid4().hex}",
        },
        private_key,
        algorithm="EdDSA",
    )


def _exchange(
    client: TestClient,
    client_id: str,
    private_key: Ed25519PrivateKey,
    *,
    jti: str | None = None,
):
    return client.post(
        "/v2/owner-auth/token",
        json={
            "client_id": client_id,
            "subject_token": _assertion(private_key, jti=jti),
            "scopes": ["apps.read", "apps.launch", "runs.read", "runs.write"],
        },
    )


def test_operator_can_reconcile_owner_client_policy(tmp_path, monkeypatch) -> None:
    store = PostgresTestStore(tmp_path / "owner-client-reconcile")
    client_id, previous_private_key = _owner_client(store)
    next_private_key = Ed25519PrivateKey.generate()
    next_public_key = next_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    monkeypatch.setenv("JOYHOUSEBOT_CONTROL_TOKEN", "owner-client-control-token-0000000000")
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))

    with client:
        updated = client.put(
            f"/control/v1/admin/apps/owner-clients/{client_id}",
            headers={"Authorization": "Bearer owner-client-control-token-0000000000"},
            json={
                "name": "JoyHouse Product Gateway",
                "issuer": "https://api.joyhouse.me",
                "public_key_pem": next_public_key,
                "algorithm": "EdDSA",
                "allowed_scopes": [
                    "apps.read",
                    "apps.install",
                    "apps.launch",
                    "runs.read",
                    "runs.write",
                ],
            },
        )
        assert updated.status_code == 200, updated.text
        assert "apps.install" in updated.json()["allowed_scopes"]

        listed = client.get(
            "/control/v1/admin/apps/owner-clients",
            headers={"Authorization": "Bearer owner-client-control-token-0000000000"},
        )
        assert listed.status_code == 200
        assert listed.json()["items"][0]["public_key_pem"] == next_public_key.strip()

        assert _exchange(client, client_id, previous_private_key).status_code == 401
        assert _exchange(client, client_id, next_private_key).status_code == 200


def test_owner_assertion_exchange_refresh_rotation_and_reuse_revocation(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "owner-delegation-refresh.db")
    client_id, private_key = _owner_client(store)
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))

    with client:
        exchanged = _exchange(client, client_id, private_key)
        assert exchanged.status_code == 200, exchanged.text
        tokens = exchanged.json()
        owner_headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        assert client.get("/v2/entrypoints", headers=owner_headers).status_code == 200

        refreshed = client.post(
            "/v2/owner-auth/refresh",
            json={"client_id": client_id, "refresh_token": tokens["refresh_token"]},
        )
        assert refreshed.status_code == 200, refreshed.text
        rotated = refreshed.json()
        assert rotated["access_token"] != tokens["access_token"]
        assert rotated["refresh_token"] != tokens["refresh_token"]

        reused = client.post(
            "/v2/owner-auth/refresh",
            json={"client_id": client_id, "refresh_token": tokens["refresh_token"]},
        )
        assert reused.status_code == 401
        assert reused.json()["error"]["retryable"] is False
        rotated_headers = {"Authorization": f"Bearer {rotated['access_token']}"}
        assert client.get("/v2/entrypoints", headers=rotated_headers).status_code == 401

    store.close()


def test_owner_assertion_is_single_use_and_owner_can_revoke_delegation(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "owner-delegation-revoke.db")
    client_id, private_key = _owner_client(store)
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))

    with client:
        assertion_id = "single-use-assertion"
        exchanged = _exchange(client, client_id, private_key, jti=assertion_id)
        assert exchanged.status_code == 200, exchanged.text
        replayed = _exchange(client, client_id, private_key, jti=assertion_id)
        assert replayed.status_code == 401
        assert set(replayed.json()["error"]) == {"code", "message", "retryable"}

        headers = {
            "Authorization": f"Bearer {exchanged.json()['access_token']}"
        }
        revoked = client.post("/v2/owner-auth/revoke", headers=headers)
        assert revoked.status_code == 200, revoked.text
        assert client.get("/v2/entrypoints", headers=headers).status_code == 401
        stale_refresh = client.post(
            "/v2/owner-auth/refresh",
            json={
                "client_id": client_id,
                "refresh_token": exchanged.json()["refresh_token"],
            },
        )
        assert stale_refresh.status_code == 401

    store.close()


def test_public_v2_validation_uses_stable_error_envelope(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "public-error-envelope.db")
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))

    with client:
        response = client.post(
            "/v2/owner-auth/token",
            json={"client_id": "bad client", "subject_token": "short", "scopes": []},
        )
        assert response.status_code == 422
        assert response.json() == {
            "error": {
                "code": "invalid_request",
                "message": "String should match pattern '^[A-Za-z0-9_.:-]{1,256}$'",
                "retryable": False,
                "field_path": "client_id",
            }
        }

    store.close()
